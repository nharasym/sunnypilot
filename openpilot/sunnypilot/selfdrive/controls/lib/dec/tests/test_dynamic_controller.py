from openpilot.common.test import OpenpilotTestCase
from openpilot.sunnypilot.selfdrive.controls.lib.dec.dec import DynamicExperimentalController

class MockLeadOne:
  def __init__(self, present=0.0):
    self.present = present

class MockRadarState:
  def __init__(self, present=0.0):
    self.leadOne = MockLeadOne(present=present)

class MockCarState:
  def __init__(self, vEgo=0.0, vCruise=0.0, standstill=False):
    self.vEgo = vEgo
    self.vCruise = vCruise
    self.standstill = standstill

class MockModelData:
  def __init__(self, valid=True):
    size = 33 if valid else 10  # incomplete if invalid
    self.position = type("Pos", (), {"x": [0.0] * size})()
    self.orientation = type("Ori", (), {"x": [0.0] * size})()

class MockSelfDriveState:
  def __init__(self, experimentalMode=False):
    self.experimentalMode = experimentalMode

class MockParams:
  def get_bool(self, name):
    return True

def default_sm():
  sm = {
    'carState': MockCarState(vEgo=10.0, vCruise=20.0),
    'radarState': MockRadarState(present=1.0),
    'modelV2': MockModelData(valid=True),
    'selfdriveState': MockSelfDriveState(experimentalMode=True),
  }
  return sm

def mock_cp():
  class CP:
    radarUnavailable = False
  return CP()

def mock_mpc():
  class MPC:
    crash_cnt = 0
  return MPC()

# Fake Kalman Filter that always returns a given value
class FakeKalman:
  def __init__(self, value=1.0):
    self.value = value
  def add_data(self, v): pass
  def get_value(self): return self.value
  def get_confidence(self): return 1.0
  def reset_data(self): pass

class TestDynamicExperimentalController(OpenpilotTestCase):
  def test_initial_mode_is_acc(self, mock_cp, mock_mpc):
    controller = DynamicExperimentalController(mock_cp, mock_mpc, params=MockParams())
    assert controller.mode() == "acc"

  def test_standstill_triggers_blended(self, mock_cp, mock_mpc, default_sm):
    controller = DynamicExperimentalController(mock_cp, mock_mpc, params=MockParams())
    default_sm['carState'].standstill = True
    for _ in range(10):
      controller.update(default_sm)
    assert controller.mode() == "blended"

  def test_emergency_blended_on_fcw(self, mock_cp, mock_mpc, default_sm):
    controller = DynamicExperimentalController(mock_cp, mock_mpc, params=MockParams())
    mock_mpc.crash_cnt = 1  # simulate FCW
    for _ in range(2):
      controller.update(default_sm)
    assert controller.mode() == "blended"

  def test_fcw_detected_on_the_first_frame(self, mock_cp, mock_mpc, default_sm):
    """HL-FIX(dec-fcw-order): the FCW filter must be add_data()-then-get_value().

    With the original get-then-add ordering the filter is still uninitialized when the decision is
    made, so get_value() returns None -> 0.0 and this frame's crash_cnt is ignored for a full cycle.
    That is a one-frame lag on the EMERGENCY path, which bypasses mode hysteresis and so has nothing
    downstream to absorb it (~50 ms ~= 1.5 m of extra stopping distance at speed).

    One update must therefore be enough. Note test_emergency_blended_on_fcw above uses range(2) --
    that second frame was papering over exactly this lag.
    """
    controller = DynamicExperimentalController(mock_cp, mock_mpc, params=MockParams())
    mock_mpc.crash_cnt = 1  # crash predicted on the very first frame
    controller.update(default_sm)
    assert controller.mode() == "blended", "FCW must take effect on the frame it is detected"

  def test_radarless_slowdown_triggers_blended(self, mock_cp, mock_mpc, default_sm):
    mock_cp.radarUnavailable = True
    controller = DynamicExperimentalController(mock_cp, mock_mpc, params=MockParams())

    # Force conditions to simulate slowdown
    controller._slow_down_filter = FakeKalman(value=1.0)  # ty: ignore[invalid-assignment]
    controller._v_ego_kph = 35.0
    default_sm['modelV2'] = MockModelData(valid=False)  # Incomplete trajectory

    for _ in range(3):
      controller.update(default_sm)

    assert controller.mode() == "blended"
