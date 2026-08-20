"""Unit tests for the MADS lateral mismatch guard (data_sample counter semantics).

While panda and selfdrived disagree on controls_allowed_lateral, the panda blocks every
nonzero-torque steering frame (starving e.g. the Toyota EPS into a fault within ~2s), so
the guard must trip on sustained disagreement quickly — but engagement transients (stale
10Hz pandaStates samples around an engage edge) must never accumulate into a disengagement.
"""
import sys
import types
import unittest
from types import SimpleNamespace

try:  # on-device / CI the compiled libparams_c exists; locally, stub params before importing mads
  from openpilot.common.params import Params  # noqa: F401
except (ImportError, OSError):  # OSError: ctypes dlopen of libparams_c fails on an unbuilt tree
  def _stub_native(name):
    mod = types.ModuleType(name)
    mod.__getattr__ = lambda attr: type(attr, (), {"__init__": lambda self, *a, **k: None})
    sys.modules[name] = mod
    return mod

  params_stub = _stub_native("openpilot.common.params")
  params_stub.Params = type("Params", (), {"get": lambda *a, **k: None, "get_bool": lambda *a, **k: False})
  params_stub.UnknownKeyName = type("UnknownKeyName", (Exception,), {})
  _stub_native("msgq.ipc_pyx")

from openpilot.sunnypilot.mads.mads import LATERAL_MISMATCH_MAX_COUNT, ModularAssistiveDrivingSystem
from opendbc.car import structs

SafetyModel = structs.CarParams.SafetyModel


def make_mads(pandas_lateral_ok=True):
  mads = ModularAssistiveDrivingSystem.__new__(ModularAssistiveDrivingSystem)
  mads.active = True
  mads.lateral_mismatch_counter = 0
  ps = SimpleNamespace(controlsAllowedLateral=pandas_lateral_ok, safetyModel=SafetyModel.toyota)
  mads.selfdrive = SimpleNamespace(enabled=False, sm={"pandaStates": [ps]})
  return mads, ps


class TestLateralMismatchGuard(unittest.TestCase):
  def test_threshold_is_one_second(self):
    # 100 loops at 100Hz = 1.0s, anchored to SubMaster's pandaStates alive tolerance:
    # any pipeline stall long enough to trip this also raises commIssue independently
    assert LATERAL_MISMATCH_MAX_COUNT == 100

  def test_sustained_disagreement_reaches_threshold(self):
    mads, ps = make_mads()
    ps.controlsAllowedLateral = False
    for _ in range(LATERAL_MISMATCH_MAX_COUNT - 1):
      mads.data_sample()
    assert mads.lateral_mismatch_counter < LATERAL_MISMATCH_MAX_COUNT
    mads.data_sample()
    assert mads.lateral_mismatch_counter >= LATERAL_MISMATCH_MAX_COUNT

  def test_engagement_transient_decays_to_zero(self):
    # a one-shot burst of stale mismatched samples must drain away, never disengage
    mads, ps = make_mads()
    ps.controlsAllowedLateral = False
    for _ in range(30):
      mads.data_sample()
    ps.controlsAllowedLateral = True
    for _ in range(30):
      mads.data_sample()
    assert mads.lateral_mismatch_counter == 0

  def test_flickering_desync_still_trips(self):
    # a persistent flicker (panda blocking most frames with brief agreeing gaps) must
    # still accumulate: hard-resetting on any agreement would hide sustained torque blocking
    mads, ps = make_mads()
    for _ in range(50):  # 50 cycles of 9 mismatched + 1 agreeing loops (90% blocked)
      ps.controlsAllowedLateral = False
      for _ in range(9):
        mads.data_sample()
      ps.controlsAllowedLateral = True
      mads.data_sample()
      if mads.lateral_mismatch_counter >= LATERAL_MISMATCH_MAX_COUNT:
        break
    assert mads.lateral_mismatch_counter >= LATERAL_MISMATCH_MAX_COUNT

  def test_inactive_or_op_enabled_clears(self):
    mads, ps = make_mads()
    ps.controlsAllowedLateral = False
    for _ in range(50):
      mads.data_sample()
    mads.active = False
    mads.data_sample()
    assert mads.lateral_mismatch_counter == 0

    mads.active = True
    for _ in range(50):
      mads.data_sample()
    mads.selfdrive.enabled = True
    mads.data_sample()
    assert mads.lateral_mismatch_counter == 0

  def test_ignored_safety_modes_do_not_count(self):
    mads, ps = make_mads()
    ps.controlsAllowedLateral = False
    ps.safetyModel = SafetyModel.silent  # e.g. a disconnected panda slot
    for _ in range(LATERAL_MISMATCH_MAX_COUNT * 2):
      mads.data_sample()
    assert mads.lateral_mismatch_counter == 0


if __name__ == "__main__":
  unittest.main()
