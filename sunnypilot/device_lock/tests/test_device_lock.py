"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): unit tests for the device-lock core.
# Run locally (no device build needed):
#   uv run --with pytest python -m pytest sunnypilot/device_lock/tests/ --noconftest -o addopts="" -q

from openpilot.sunnypilot.device_lock.tests._params_stub import ensure_params_stub
ensure_params_stub()

import pytest

from openpilot.sunnypilot.device_lock.constants import (
  PARAM_IS_ONROAD, PARAM_LOCKED, PARAM_OFFROAD_MODE, PARAM_PIN_FORMAT, PARAM_PIN_HASH,
  PARAM_PREV_OFFROAD, PIN_FORMAT_CURRENT,
  RATE_LIMIT_AFTER_ATTEMPTS, RATE_LIMIT_BASE_SECONDS,
)
from openpilot.sunnypilot.device_lock.lock import (
  DeviceLock, LockError, PinError, check_pin, hash_pin, validate_pin,
)


class FakeParams:
  """In-memory stand-in for Params.

  Signatures MUST match common/params_pyx.pyx exactly. A previous version accepted an
  `encoding=` kwarg that the real Params.get() does not have; every test passed while the
  device UI crash-looped on the lock screen. A test double that is more permissive than the
  real API hides exactly this class of bug, so keep these strict:
      get(key, block=False, return_default=False)
      get_bool(key, block=False)
      put(key, dat, block=False)
      put_bool(key, val, block=False)
  """

  def __init__(self, initial=None):
    self._d = dict(initial or {})

  def get(self, key, block=False, return_default=False):
    return self._d.get(key)

  def get_bool(self, key, block=False):
    return bool(self._d.get(key, False))

  def put(self, key, dat, block=False):
    self._d[key] = dat

  def put_bool(self, key, val, block=False):
    self._d[key] = bool(val)


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
  stored = hash_pin("0123")
  assert check_pin("0123", stored)
  assert not check_pin("0132", stored)


def test_hash_pin_salted_differently_each_time():
  """Same PIN must not produce the same stored value twice (random salt)."""
  assert hash_pin("0123") != hash_pin("0123")


def test_plaintext_pin_never_in_stored_value():
  assert "0123" not in hash_pin("0123")


def test_check_pin_rejects_malformed_stored():
  for bad in ("", "nosalt", "zz$zz", None):
    assert not check_pin("0123", bad)


@pytest.mark.parametrize("pin", ["0123", "01230123"])
def test_validate_pin_accepts_valid(pin):
  validate_pin(pin)


@pytest.mark.parametrize("pin", ["012", "012301230", "abcd", "01a3", "", "4567", "0129"])
def test_validate_pin_rejects_invalid(pin):
  with pytest.raises(PinError):
    validate_pin(pin)


# --- lock / unlock ---

def test_lock_forces_offroad_mode(lock):
  lock.set_pin("0123")
  lock.lock()
  assert lock.is_locked()
  assert lock._params.get_bool(PARAM_OFFROAD_MODE), "locking must force the car to stock"


def test_unlock_with_correct_pin(lock):
  lock.set_pin("0123")
  lock.lock()
  assert lock.try_unlock("0123")
  assert not lock.is_locked()


def test_unlock_with_wrong_pin_stays_locked(lock):
  lock.set_pin("0123")
  lock.lock()
  assert not lock.try_unlock("3333")
  assert lock.is_locked()
  assert lock._params.get_bool(PARAM_OFFROAD_MODE)


def test_unlock_restores_previous_offroad_mode(lock):
  """A user who already ran Always Offroad keeps it after unlocking."""
  lock._params.put_bool(PARAM_OFFROAD_MODE, True)
  lock.set_pin("0123")
  lock.lock()
  assert lock.try_unlock("0123")
  assert lock._params.get_bool(PARAM_OFFROAD_MODE)
  assert lock._params.get_bool(PARAM_PREV_OFFROAD)


def test_unlock_clears_offroad_mode_if_it_was_off(lock):
  lock._params.put_bool(PARAM_OFFROAD_MODE, False)
  lock.set_pin("0123")
  lock.lock()
  lock.try_unlock("0123")
  assert not lock._params.get_bool(PARAM_OFFROAD_MODE)


# --- THE CORE PROPERTY: local Always-Offroad toggle-off must not unlock ---

def test_enforce_reverts_local_offroad_toggle_off(lock):
  """Someone clears OffroadMode on the device screen -> next tick puts it back."""
  lock.set_pin("0123")
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
  lock.set_pin("0123")
  lock.lock()
  for _ in range(10):
    lock.enforce()
  assert lock._params.get_bool(PARAM_OFFROAD_MODE)


def test_clearing_locked_param_is_the_only_way_out(lock):
  """Simulates a remote sunnylink saveParams({'DeviceLocked': '0'}) unlock."""
  lock.set_pin("0123")
  lock.lock()
  lock._params.put_bool(PARAM_LOCKED, False)  # remote write
  lock.enforce()
  assert not lock.is_locked()


# --- rate limiting ---

def test_rate_limit_kicks_in_after_threshold(lock):
  lock.set_pin("0123")
  lock.lock()
  for _ in range(RATE_LIMIT_AFTER_ATTEMPTS):
    lock.try_unlock("1111")
  assert lock.is_rate_limited()
  assert lock.cooldown_remaining() > 0


def test_correct_pin_refused_while_rate_limited(lock):
  lock.set_pin("0123")
  lock.lock()
  for _ in range(RATE_LIMIT_AFTER_ATTEMPTS):
    lock.try_unlock("1111")
  assert not lock.try_unlock("0123"), "must refuse even the right PIN during cooldown"
  assert lock.is_locked()


def test_cooldown_expires(lock):
  lock.set_pin("0123")
  lock.lock()
  for _ in range(RATE_LIMIT_AFTER_ATTEMPTS):
    lock.try_unlock("1111")
  lock._clock.advance(RATE_LIMIT_BASE_SECONDS + 1)
  assert not lock.is_rate_limited()
  assert lock.try_unlock("0123")


def test_successful_unlock_resets_attempts(lock):
  lock.set_pin("0123")
  lock.lock()
  lock.try_unlock("1111")
  lock.try_unlock("0123")
  assert lock.failed_attempts == 0


def test_attempts_persist_in_params(lock):
  lock.set_pin("0123")
  lock.lock()
  lock.try_unlock("1111")
  lock.try_unlock("1111")
  assert lock.failed_attempts == 2


def test_no_pin_set_cannot_unlock(lock):
  """With no PIN configured, PIN unlock must fail closed (remote unlock still works)."""
  lock._params.put_bool(PARAM_LOCKED, True)
  assert not lock._params.get(PARAM_PIN_HASH)
  assert not lock.try_unlock("0123")
  assert lock.is_locked()


# --- must never engage mid-drive ---

def test_lock_refused_while_onroad(lock):
  """Locking forces OffroadMode, which drops openpilot - it must never happen while driving."""
  lock.set_pin("0123")
  lock._params.put_bool(PARAM_IS_ONROAD, True)
  with pytest.raises(LockError):
    lock.lock()
  assert not lock.is_locked()
  assert not lock._params.get_bool(PARAM_OFFROAD_MODE), "must not touch OffroadMode when refused"


def test_lock_allowed_when_offroad(lock):
  lock.set_pin("0123")
  lock._params.put_bool(PARAM_IS_ONROAD, False)
  lock.lock()
  assert lock.is_locked()


def test_lock_refused_if_car_goes_onroad_mid_dialog(lock):
  """The race the UI alone can't cover: dialog opened offroad, car onroad before submit."""
  lock.set_pin("0123")           # dialog opened while offroad
  lock._params.put_bool(PARAM_IS_ONROAD, True)   # ignition on / starts moving
  with pytest.raises(LockError):
    lock.lock()                  # submit
  assert not lock.is_locked()


def test_enforce_does_not_drop_control_on_remote_lock_mid_drive(lock):
  """A remote saveParams({'DeviceLocked':'1'}) writes the param directly, bypassing lock().

  enforce() must not force OffroadMode while onroad or it would drop control mid-drive.
  """
  lock._params.put_bool(PARAM_IS_ONROAD, True)
  lock._params.put_bool(PARAM_LOCKED, True)  # remote write, bypasses lock()
  lock.enforce()
  assert not lock._params.get_bool(PARAM_OFFROAD_MODE), "must not force offroad while driving"
  assert lock.is_locked(), "the lock itself still stands"


def test_enforce_applies_once_parked(lock):
  """The deferred remote lock takes effect as soon as the car is offroad."""
  lock._params.put_bool(PARAM_IS_ONROAD, True)
  lock._params.put_bool(PARAM_LOCKED, True)
  lock.enforce()
  assert not lock._params.get_bool(PARAM_OFFROAD_MODE)

  lock._params.put_bool(PARAM_IS_ONROAD, False)  # parked
  lock.enforce()
  assert lock._params.get_bool(PARAM_OFFROAD_MODE)


def test_unlock_is_allowed_onroad(lock):
  """Unlocking only removes a restriction, so it must not be blocked."""
  lock.set_pin("0123")
  lock.lock()
  lock._params.put_bool(PARAM_IS_ONROAD, True)
  assert lock.try_unlock("0123")
  assert not lock.is_locked()


# --- stale PIN format must fail safe, not strand the owner ---

def test_pin_written_records_current_format(lock):
  lock.set_pin("0123")
  assert lock._params.get(PARAM_PIN_FORMAT) == PIN_FORMAT_CURRENT


def test_stale_format_pin_is_treated_as_absent(lock):
  """A hash written under the old digit alphabet can never match symbol input.

  has_pin() must report False so the lock screen says "unlock from the dashboard" rather than
  offering input that provably cannot succeed.
  """
  lock.set_pin("0123")
  lock._params.put(PARAM_PIN_FORMAT, 0)  # simulate a pre-symbol PIN
  assert not lock.has_pin()
  assert not lock.try_unlock("0123"), "must not accept against a stale-format hash"


def test_setting_a_new_pin_clears_the_stale_format(lock):
  lock._params.put(PARAM_PIN_HASH, "deadbeef$cafe")
  lock._params.put(PARAM_PIN_FORMAT, 0)
  assert not lock.has_pin()

  lock.set_pin("0123")
  assert lock.has_pin()
  assert lock.try_unlock("0123")


# --- the test double must not be more permissive than the real Params ---

def test_no_params_call_uses_a_kwarg_the_real_api_lacks():
  """Regression: lock.py called Params.get(key, encoding="utf8"); the real signature has no
  `encoding`. Every test passed (FakeParams accepted it) while the device UI crash-looped on
  the lock screen. Parse the real signatures out of params_pyx.pyx and check our call sites.
  """
  import pathlib
  import re

  root = pathlib.Path(__file__).parents[3]
  pyx = (root / "common" / "params_pyx.pyx").read_text()

  allowed: dict[str, set[str]] = {}
  for m in re.finditer(r"^  def (get|get_bool|put|put_bool|remove)\(self,([^)]*)\)", pyx, re.M):
    name, args = m.group(1), m.group(2)
    kwargs = set()
    for part in args.split(","):
      part = part.strip()
      if "=" in part:
        kwargs.add(part.split("=")[0].strip().split()[-1])
    allowed[name] = kwargs
  assert allowed, "could not parse Params signatures from params_pyx.pyx"

  src = (root / "sunnypilot" / "device_lock" / "lock.py").read_text()
  used = re.findall(r"_params\.(get|get_bool|put|put_bool|remove)\(([^)]*)\)", src)
  assert used, "no Params calls found - did lock.py move?"
  for name, argstr in used:
    for kw in re.findall(r"(\w+)\s*=", argstr):
      msg = f"lock.py calls Params.{name}({kw}=...) but the real signature only accepts {sorted(allowed[name])}"
      assert kw in allowed[name], msg


# --- every offroad-alert key must be a declared param ---

def test_alert_registry_keys_are_declared_params():
  """Regression: Offroad_DeviceLocked was registered in alerts_offroad.json but never declared in
  params_keys.h. params_pyx raises UnknownKeyName on undeclared keys, so the mici alerts-refresh
  daemon thread died on its first iteration and the alerts panel silently never showed anything.
  Pin the CLASS: every key in the alert registry must be declared in params_keys.h.
  """
  import json
  import pathlib
  import re

  root = pathlib.Path(__file__).parents[3]
  registry = json.loads((root / "selfdrive" / "selfdrived" / "alerts_offroad.json").read_text())
  declared = set(re.findall(r'\{"(\w+)",', (root / "common" / "params_keys.h").read_text()))
  missing = [k for k in registry if k not in declared]
  assert not missing, f"alerts_offroad.json keys missing from params_keys.h: {missing}"


def test_lock_alert_key_exists_in_registry():
  """Reverse direction of the declaration test: OFFROAD_ALERT_LOCKED must exist as a key in
  alerts_offroad.json. If the entry were renamed/removed, set_offroad_alert raises KeyError,
  _set_alert swallows it, and the alert would silently never show while every test stays green.
  """
  import json
  import pathlib

  from openpilot.sunnypilot.device_lock.constants import OFFROAD_ALERT_LOCKED

  root = pathlib.Path(__file__).parents[3]
  registry = json.loads((root / "selfdrive" / "selfdrived" / "alerts_offroad.json").read_text())
  assert OFFROAD_ALERT_LOCKED in registry, f"{OFFROAD_ALERT_LOCKED} missing from alerts_offroad.json"


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
