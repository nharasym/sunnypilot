"""Unit tests for HL-FEAT(bsm-approaching): the approaching-vehicle audible alert.

The chime must fire once per approach (rising edge of blinker+approaching+speed), hold the
alert for BSM_CHIME_HOLD_FRAMES so the single-play sound triggers, and never re-fire until
the condition has been clear for BSM_REARM_FRAMES. Toggle off = fully inert.
"""
import sys
import types
import unittest
from types import SimpleNamespace

try:  # on-device / CI the compiled libparams_c exists; locally, stub params before importing
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

from opendbc.car import structs
from openpilot.cereal import custom
from openpilot.sunnypilot.selfdrive.car.car_specific import (
  CarSpecificEventsSP, BSM_CHIME_HOLD_FRAMES, BSM_REARM_FRAMES, BSM_APPROACHING_MIN_SPEED,
  BSM_RETRIGGER_GAP_FRAMES)

EventNameSP = custom.OnroadEventSP.EventName


class FakeParams:
  def __init__(self, enabled=True):
    self.enabled = enabled

  def get_bool(self, key):
    return self.enabled if key == "BsmApproachingAlert" else False


class FakeEvents:
  def has(self, name):
    return False

  def add(self, name):
    pass

  def remove(self, name):
    pass


def make_cs(v_ego=20.0, left_blinker=False, right_blinker=False):
  cs = structs.CarState()
  cs.vEgo = v_ego
  cs.leftBlinker = left_blinker
  cs.rightBlinker = right_blinker
  return cs


def make_cs_sp(left=False, right=False):
  return SimpleNamespace(leftBlindspotApproaching=left, rightBlindspotApproaching=right)


def make_controller(enabled=True):
  cp = structs.CarParams(brand="toyota")
  cp_sp = structs.CarParamsSP()
  return CarSpecificEventsSP(cp, cp_sp, params=FakeParams(enabled=enabled))


def has_chime(controller, cs, cs_sp):
  events_sp = controller.update(cs, cs_sp, FakeEvents())
  return EventNameSP.bsmApproaching in events_sp.events


class TestBsmApproachingAlert(unittest.TestCase):
  def test_toggle_off_no_event(self):
    c = make_controller(enabled=False)
    assert not has_chime(c, make_cs(left_blinker=True), make_cs_sp(left=True))

  def test_first_approach_of_drive_chimes(self):
    c = make_controller()
    assert has_chime(c, make_cs(left_blinker=True), make_cs_sp(left=True))

  def test_chime_holds_then_expires_without_retrigger(self):
    c = make_controller()
    cs, cs_sp = make_cs(left_blinker=True), make_cs_sp(left=True)
    frames = 0
    # condition held continuously well past the hold window: exactly one chime window
    for _ in range(BSM_CHIME_HOLD_FRAMES * 3):
      if has_chime(c, cs, cs_sp):
        frames += 1
    assert frames == BSM_CHIME_HOLD_FRAMES

  def test_no_rearm_until_clear_long_enough(self):
    c = make_controller()
    cs_on, cs_sp_on = make_cs(left_blinker=True), make_cs_sp(left=True)
    cs_off, cs_sp_off = make_cs(), make_cs_sp()
    for _ in range(BSM_CHIME_HOLD_FRAMES * 2):
      has_chime(c, cs_on, cs_sp_on)
    # brief clear (shorter than the re-arm dwell) must NOT allow a second chime
    for _ in range(BSM_REARM_FRAMES // 2):
      assert not has_chime(c, cs_off, cs_sp_off)
    assert not has_chime(c, cs_on, cs_sp_on)

  def test_rearm_after_full_clear(self):
    c = make_controller()
    cs_on, cs_sp_on = make_cs(left_blinker=True), make_cs_sp(left=True)
    cs_off, cs_sp_off = make_cs(), make_cs_sp()
    for _ in range(BSM_CHIME_HOLD_FRAMES * 2):
      has_chime(c, cs_on, cs_sp_on)
    for _ in range(BSM_REARM_FRAMES + 1):
      has_chime(c, cs_off, cs_sp_off)
    assert has_chime(c, cs_on, cs_sp_on)

  def test_wrong_side_no_event(self):
    c = make_controller()
    assert not has_chime(c, make_cs(left_blinker=True), make_cs_sp(right=True))

  def test_right_side_works(self):
    c = make_controller()
    assert has_chime(c, make_cs(right_blinker=True), make_cs_sp(right=True))

  def test_low_speed_no_event(self):
    c = make_controller()
    assert not has_chime(c, make_cs(v_ego=BSM_APPROACHING_MIN_SPEED - 1.0, left_blinker=True), make_cs_sp(left=True))

  def test_approaching_without_blinker_no_event(self):
    c = make_controller()
    assert not has_chime(c, make_cs(), make_cs_sp(left=True))

  def test_none_cs_sp_safe(self):
    c = make_controller()
    assert not has_chime(c, make_cs(left_blinker=True), None)

  def test_other_brand_inert(self):
    cp = structs.CarParams(brand="honda")
    c = CarSpecificEventsSP(cp, structs.CarParamsSP(), params=FakeParams())
    assert not has_chime(c, make_cs(left_blinker=True), make_cs_sp(left=True))

  def test_second_threat_mid_hold_forces_alert_gap(self):
    # a new threat landing while the alert is up must drop the alert for the gap window so
    # soundd sees a fresh alert and replays the chime — never a silently-merged second threat
    c = make_controller()
    cs_left, sp_left = make_cs(left_blinker=True), make_cs_sp(left=True)
    for _ in range(50):
      assert has_chime(c, cs_left, sp_left)
    # both blinkers on (hazard-ish merge) and the RIGHT side now flags approaching too
    cs_both, sp_both = make_cs(left_blinker=True, right_blinker=True), make_cs_sp(left=True, right=True)
    gap_frames = 0
    seen_after_gap = 0
    for _ in range(BSM_RETRIGGER_GAP_FRAMES + 20):
      if has_chime(c, cs_both, sp_both):
        seen_after_gap += 1
      elif seen_after_gap == 0:
        gap_frames += 1
    assert gap_frames == BSM_RETRIGGER_GAP_FRAMES  # alert dropped exactly the gap window
    assert seen_after_gap == 20                    # then a fresh hold began

  def test_both_sides_simultaneous_single_chime(self):
    # both sides flagging on the SAME frame is one threat event: one chime, no gap dance
    c = make_controller()
    cs, sp = make_cs(left_blinker=True, right_blinker=True), make_cs_sp(left=True, right=True)
    frames = 0
    for _ in range(BSM_CHIME_HOLD_FRAMES * 2):
      if has_chime(c, cs, sp):
        frames += 1
    assert frames == BSM_CHIME_HOLD_FRAMES


class TestPlumbing(unittest.TestCase):
  def test_carstatesp_capnp_field_match(self):
    # convert_to_capnp does a generic dataclass->capnp field mapping: a name mismatch
    # between structs.CarStateSP and custom.capnp CarStateSP fails at publish time on-device,
    # so pin it here
    msg = custom.CarStateSP.new_message(leftBlindspotApproaching=True, rightBlindspotApproaching=False)
    assert msg.leftBlindspotApproaching is True
    assert msg.rightBlindspotApproaching is False
    s = structs.CarStateSP()
    assert hasattr(s, "leftBlindspotApproaching") and hasattr(s, "rightBlindspotApproaching")

  def test_event_has_audible_alert(self):
    from openpilot.sunnypilot.selfdrive.selfdrived.events import EVENTS_SP
    from openpilot.sunnypilot.selfdrive.selfdrived.events_base import ET
    entry = EVENTS_SP[EventNameSP.bsmApproaching]
    # PERMANENT, not WARNING: warnings are suppressed unless engaged/MADS-active, and this
    # alert must also cover fully-manual and MADS-paused merges
    alert = entry[ET.PERMANENT]
    assert alert.audible_alert != 0  # a real sound is attached
    # Alert stores duration in control frames (seconds / DT_CTRL); the linger must stay
    # shorter than BSM_RETRIGGER_GAP_FRAMES or the gap can't reset the alert identity
    assert alert.duration < BSM_RETRIGGER_GAP_FRAMES


if __name__ == "__main__":
  unittest.main()
