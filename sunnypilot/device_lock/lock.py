"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): core device-lock state machine.
#
# Purpose: let the owner lock the device (e.g. leaving the car at a dealership) so the car
# reverts to its factory ADAS while the comma stays powered and plugged in, and nobody can
# use or unlock it from the device screen.
#
# Design:
#   - DeviceLocked (PERSISTENT|BACKUP) is the SOURCE OF TRUTH.
#   - OffroadMode is a DERIVED effect, re-asserted from DeviceLocked on boot and every UI tick.
#     Toggling Always Offroad off locally is therefore a no-op that self-heals; only clearing
#     DeviceLocked (correct PIN, or remote sunnylink saveParams) actually unlocks.
#   - Forcing OffroadMode drives the existing, already-wired revert-to-stock chain:
#       OffroadMode -> deviceState.started=False (hardwared) -> pandad is_onroad=False and
#       always_offroad=True -> panda forced to NO_OUTPUT -> factory ADAS runs, openpilot
#       can never engage. No panda/pandad/hardwared changes are needed.
#
# This module is deliberately free of UI and cereal imports so it can be unit-tested off-device.

import hmac
import hashlib
import os
import time

from openpilot.sunnypilot.device_lock.constants import (
  PARAM_LOCKED,
  PARAM_PIN_HASH,
  PARAM_ATTEMPTS,
  PARAM_PREV_OFFROAD,
  PARAM_OFFROAD_MODE,
  PARAM_IS_ONROAD,
  OFFROAD_ALERT_LOCKED,
  PIN_MIN_LENGTH,
  PIN_MAX_LENGTH,
  PIN_KDF_ITERATIONS,
  PIN_SALT_BYTES,
  RATE_LIMIT_AFTER_ATTEMPTS,
  RATE_LIMIT_BASE_SECONDS,
  RATE_LIMIT_MAX_SECONDS,
)


class PinError(ValueError):
  """Raised when a PIN fails policy validation (length / digits)."""


class LockError(RuntimeError):
  """Raised when the lock cannot be engaged right now (e.g. the car is onroad)."""


def validate_pin(pin: str) -> None:
  """Raise PinError if the PIN doesn't meet policy. Digits only, PIN_MIN..PIN_MAX long."""
  if not pin.isdigit():
    raise PinError("PIN must be digits only")
  if not PIN_MIN_LENGTH <= len(pin) <= PIN_MAX_LENGTH:
    raise PinError(f"PIN must be {PIN_MIN_LENGTH}-{PIN_MAX_LENGTH} digits")


def hash_pin(pin: str, salt: bytes | None = None) -> str:
  """Hash a PIN as 'salt_hex$hash_hex'. Never store or log the plaintext PIN."""
  salt = salt if salt is not None else os.urandom(PIN_SALT_BYTES)
  digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, PIN_KDF_ITERATIONS)
  return f"{salt.hex()}${digest.hex()}"


def check_pin(pin: str, stored: str) -> bool:
  """Constant-time compare of a candidate PIN against a stored 'salt$hash'."""
  try:
    salt_hex, hash_hex = stored.split("$", 1)
    salt = bytes.fromhex(salt_hex)
  except (ValueError, AttributeError):
    return False
  candidate = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, PIN_KDF_ITERATIONS)
  return hmac.compare_digest(candidate.hex(), hash_hex)


class DeviceLock:
  """Device lock state. Safe to construct anywhere; all state lives in Params."""

  def __init__(self, params=None, monotonic=time.monotonic):
    if params is None:
      # imported lazily so this module (and constants) can be used/tested without the
      # compiled params extension present
      from openpilot.common.params import Params
      params = Params()
    self._params = params
    self._monotonic = monotonic
    self._cooldown_until: float | None = None

  # --- state ---

  def is_locked(self) -> bool:
    return self._params.get_bool(PARAM_LOCKED)

  def has_pin(self) -> bool:
    return bool(self._params.get(PARAM_PIN_HASH))

  def set_pin(self, pin: str) -> None:
    """Set/replace the unlock PIN. Raises PinError if it fails policy."""
    validate_pin(pin)
    self._params.put(PARAM_PIN_HASH, hash_pin(pin), block=True)
    self._reset_attempts()

  # --- lock / unlock ---

  def is_onroad(self) -> bool:
    return self._params.get_bool(PARAM_IS_ONROAD)

  def lock(self) -> None:
    """Engage the lock: remember the current OffroadMode, then force the car to stock.

    Refuses while onroad and raises LockError. Locking forces OffroadMode, which closes the
    panda relay and drops openpilot - doing that mid-drive would yank control away from the
    driver. The guard lives here, not just in the UI, so no call path can bypass it (the UI
    button can be enabled offroad and the car can go onroad before the PIN is submitted).
    """
    if self.is_onroad():
      raise LockError("Cannot lock while the car is onroad")

    self._params.put_bool(PARAM_PREV_OFFROAD, self._params.get_bool(PARAM_OFFROAD_MODE), block=True)
    self._params.put_bool(PARAM_LOCKED, True, block=True)
    self._reset_attempts()
    self.enforce()
    self._set_alert(True)

  def unlock(self) -> None:
    """Release the lock and restore OffroadMode to whatever it was before locking."""
    self._params.put_bool(PARAM_LOCKED, False, block=True)
    self._params.put_bool(PARAM_OFFROAD_MODE, self._params.get_bool(PARAM_PREV_OFFROAD), block=True)
    self._reset_attempts()
    self._set_alert(False)

  @staticmethod
  def _set_alert(show: bool) -> None:
    """Mirror the lock state into an offroad alert so it's visible on the home screen and remotely.

    Best-effort and lazily imported: the alert is cosmetic, so a failure here must never stop the
    lock itself from engaging or releasing.
    """
    try:
      from openpilot.selfdrive.selfdrived.alertmanager import set_offroad_alert
      set_offroad_alert(OFFROAD_ALERT_LOCKED, show)
    except Exception:
      pass

  def try_unlock(self, pin: str) -> bool:
    """Attempt a PIN unlock. Returns True on success.

    Wrong PINs increment a persistent attempt counter and, past the threshold, start an
    escalating cooldown. Check is_rate_limited() first to show the user the wait.
    """
    if self.is_rate_limited():
      return False

    stored = self._params.get(PARAM_PIN_HASH)
    if not stored or not check_pin(pin, stored):
      self._register_failure()
      return False

    self.unlock()
    return True

  # --- enforcement (derived effect; safe to call every tick) ---

  def enforce(self) -> None:
    """While locked, keep OffroadMode asserted so the car stays on its factory ADAS.

    This is what makes toggling Always Offroad off on the device screen a no-op: the change
    is reverted within a frame. Only clearing DeviceLocked actually stops enforcement.
    Cheap and idempotent - only writes when the value is wrong.

    Never asserts while onroad. lock() already refuses mid-drive, but DeviceLocked can also be
    set straight into Params by a remote sunnylink saveParams, which bypasses lock() entirely -
    without this check that would drop control mid-drive. A lock set while driving simply takes
    effect once the car is parked (or at the next boot, via manager.py).
    """
    if self.is_locked() and not self.is_onroad() and not self._params.get_bool(PARAM_OFFROAD_MODE):
      self._params.put_bool(PARAM_OFFROAD_MODE, True)

  # --- rate limiting ---

  @property
  def failed_attempts(self) -> int:
    return self._params.get(PARAM_ATTEMPTS, return_default=True) or 0

  def is_rate_limited(self) -> bool:
    return self.cooldown_remaining() > 0

  def cooldown_remaining(self) -> float:
    """Seconds until another PIN attempt is allowed (0 if allowed now)."""
    if self._cooldown_until is None:
      return 0.0
    return max(0.0, self._cooldown_until - self._monotonic())

  def _register_failure(self) -> None:
    attempts = self.failed_attempts + 1
    self._params.put(PARAM_ATTEMPTS, attempts, block=True)
    if attempts >= RATE_LIMIT_AFTER_ATTEMPTS:
      over = attempts - RATE_LIMIT_AFTER_ATTEMPTS
      backoff = min(RATE_LIMIT_BASE_SECONDS * (2 ** over), RATE_LIMIT_MAX_SECONDS)
      self._cooldown_until = self._monotonic() + backoff

  def _reset_attempts(self) -> None:
    self._params.put(PARAM_ATTEMPTS, 0, block=True)
    self._cooldown_until = None
