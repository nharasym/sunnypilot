"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): unit tests for the device-lock core.
# Run locally (no device build needed):
#   uv run --with pytest python -m pytest sunnypilot/device_lock/tests/ --noconftest -o addopts="" -q

import sys
import types

try:  # on-device/CI the compiled native module exists; locally, stub it before importing lock.py
  from openpilot.common.params_pyx import Params  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - local dev path
  stub = types.ModuleType("openpilot.common.params_pyx")
  stub.Params = object
  stub.ParamKeyFlag = object
  stub.ParamKeyType = object
  stub.UnknownKeyName = KeyError
  sys.modules["openpilot.common.params_pyx"] = stub

import pytest

from openpilot.sunnypilot.device_lock.constants import (
  PARAM_LOCKED, PARAM_OFFROAD_MODE, PARAM_PIN_HASH, PARAM_PREV_OFFROAD,
  RATE_LIMIT_AFTER_ATTEMPTS, RATE_LIMIT_BASE_SECONDS,
)
from openpilot.sunnypilot.device_lock.lock import (
  DeviceLock, PinError, check_pin, hash_pin, validate_pin,
)


class FakeParams:
  """Minimal in-memory stand-in for Params covering the calls DeviceLock makes."""

  def __init__(self, initial=None):
    self._d = dict(initial or {})

  def get(self, key, encoding=None, return_default=False):
    return self._d.get(key)

  def get_bool(self, key):
    return bool(self._d.get(key, False))

  def put(self, key, value, block=False):
    self._d[key] = value

  def put_bool(self, key, value, block=False):
    self._d[key] = bool(value)


class FakeClock:
  def __init__(self):
    self.t = 1000.0

  def __call__(self):
    return self.t

  def advance(self, dt):
    self.t += dt


@pytest.fixture
def lock():
  clock = FakeClock()
  dl = DeviceLock(params=FakeParams(), monotonic=clock)
  dl._clock = clock  # test handle
  return dl


# --- PIN hashing ---

def test_hash_pin_roundtrip():
  stored = hash_pin("1234")
  assert check_pin("1234", stored)
  assert not check_pin("1235", stored)


def test_hash_pin_salted_differently_each_time():
  """Same PIN must not produce the same stored value twice (random salt)."""
  assert hash_pin("1234") != hash_pin("1234")


def test_plaintext_pin_never_in_stored_value():
  assert "1234" not in hash_pin("1234")


def test_check_pin_rejects_malformed_stored():
  for bad in ("", "nosalt", "zz$zz", None):
    assert not check_pin("1234", bad)


@pytest.mark.parametrize("pin", ["1234", "12345678"])
def test_validate_pin_accepts_valid(pin):
  validate_pin(pin)


@pytest.mark.parametrize("pin", ["123", "123456789", "abcd", "12a4", ""])
def test_validate_pin_rejects_invalid(pin):
  with pytest.raises(PinError):
    validate_pin(pin)


# --- lock / unlock ---

def test_lock_forces_offroad_mode(lock):
  lock.set_pin("1234")
  lock.lock()
  assert lock.is_locked()
  assert lock._params.get_bool(PARAM_OFFROAD_MODE), "locking must force the car to stock"


def test_unlock_with_correct_pin(lock):
  lock.set_pin("1234")
  lock.lock()
  assert lock.try_unlock("1234")
  assert not lock.is_locked()


def test_unlock_with_wrong_pin_stays_locked(lock):
  lock.set_pin("1234")
  lock.lock()
  assert not lock.try_unlock("9999")
  assert lock.is_locked()
  assert lock._params.get_bool(PARAM_OFFROAD_MODE)


def test_unlock_restores_previous_offroad_mode(lock):
  """A user who already ran Always Offroad keeps it after unlocking."""
  lock._params.put_bool(PARAM_OFFROAD_MODE, True)
  lock.set_pin("1234")
  lock.lock()
  assert lock.try_unlock("1234")
  assert lock._params.get_bool(PARAM_OFFROAD_MODE)
  assert lock._params.get_bool(PARAM_PREV_OFFROAD)


def test_unlock_clears_offroad_mode_if_it_was_off(lock):
  lock._params.put_bool(PARAM_OFFROAD_MODE, False)
  lock.set_pin("1234")
  lock.lock()
  lock.try_unlock("1234")
  assert not lock._params.get_bool(PARAM_OFFROAD_MODE)


# --- THE CORE PROPERTY: local Always-Offroad toggle-off must not unlock ---

def test_enforce_reverts_local_offroad_toggle_off(lock):
  """Someone clears OffroadMode on the device screen -> next tick puts it back."""
  lock.set_pin("1234")
  lock.lock()

  lock._params.put_bool(PARAM_OFFROAD_MODE, False)  # simulate the on-screen toggle
  lock.enforce()

  assert lock._params.get_bool(PARAM_OFFROAD_MODE), "toggling Always Offroad off must self-heal"
  assert lock.is_locked()


def test_enforce_is_noop_when_unlocked(lock):
  lock._params.put_bool(PARAM_OFFROAD_MODE, False)
  lock.enforce()
  assert not lock._params.get_bool(PARAM_OFFROAD_MODE), "must not force offroad when unlocked"


def test_enforce_idempotent(lock):
  lock.set_pin("1234")
  lock.lock()
  for _ in range(10):
    lock.enforce()
  assert lock._params.get_bool(PARAM_OFFROAD_MODE)


def test_clearing_locked_param_is_the_only_way_out(lock):
  """Simulates a remote sunnylink saveParams({'DeviceLocked': '0'}) unlock."""
  lock.set_pin("1234")
  lock.lock()
  lock._params.put_bool(PARAM_LOCKED, False)  # remote write
  lock.enforce()
  assert not lock.is_locked()


# --- rate limiting ---

def test_rate_limit_kicks_in_after_threshold(lock):
  lock.set_pin("1234")
  lock.lock()
  for _ in range(RATE_LIMIT_AFTER_ATTEMPTS):
    lock.try_unlock("0000")
  assert lock.is_rate_limited()
  assert lock.cooldown_remaining() > 0


def test_correct_pin_refused_while_rate_limited(lock):
  lock.set_pin("1234")
  lock.lock()
  for _ in range(RATE_LIMIT_AFTER_ATTEMPTS):
    lock.try_unlock("0000")
  assert not lock.try_unlock("1234"), "must refuse even the right PIN during cooldown"
  assert lock.is_locked()


def test_cooldown_expires(lock):
  lock.set_pin("1234")
  lock.lock()
  for _ in range(RATE_LIMIT_AFTER_ATTEMPTS):
    lock.try_unlock("0000")
  lock._clock.advance(RATE_LIMIT_BASE_SECONDS + 1)
  assert not lock.is_rate_limited()
  assert lock.try_unlock("1234")


def test_successful_unlock_resets_attempts(lock):
  lock.set_pin("1234")
  lock.lock()
  lock.try_unlock("0000")
  lock.try_unlock("1234")
  assert lock.failed_attempts == 0


def test_attempts_persist_in_params(lock):
  lock.set_pin("1234")
  lock.lock()
  lock.try_unlock("0000")
  lock.try_unlock("0000")
  assert lock.failed_attempts == 2


def test_no_pin_set_cannot_unlock(lock):
  """With no PIN configured, PIN unlock must fail closed (remote unlock still works)."""
  lock._params.put_bool(PARAM_LOCKED, True)
  assert not lock._params.get(PARAM_PIN_HASH)
  assert not lock.try_unlock("1234")
  assert lock.is_locked()


# --- remote unlock path must stay open ---

def test_lock_params_are_remotely_writable():
  """Regression: sunnylink saveParams must be able to clear the lock / reset the PIN.

  Remote unlock is the recovery path if the PIN is forgotten, so these keys must never end up
  in sunnylinkd's BLOCKED_PARAMS. Parsed from source to avoid importing the websocket stack.
  """
  import ast
  import pathlib

  src = pathlib.Path(__file__).parents[3] / "sunnypilot" / "sunnylink" / "athena" / "sunnylinkd.py"
  tree = ast.parse(src.read_text())
  blocked: set[str] = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Assign) and any(
      isinstance(t, ast.Name) and t.id == "BLOCKED_PARAMS" for t in node.targets
    ):
      blocked = {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
  assert blocked, "could not parse BLOCKED_PARAMS from sunnylinkd.py"
  assert PARAM_LOCKED not in blocked, f"{PARAM_LOCKED} must stay remotely writable (remote unlock)"
  assert PARAM_PIN_HASH not in blocked, f"{PARAM_PIN_HASH} must stay remotely writable (PIN reset)"
