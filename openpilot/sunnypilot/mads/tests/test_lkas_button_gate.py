"""Unit tests for the LKAS/LDA button availability gate (allow_always).

The button handler is gated on (CS.cruiseState.available or allow_always). On brands
with a physical LDA button and no stock main-cruise requirement (stock Toyota LTA works
with ACC main off), a press before main is armed must not be silently dropped — that
gate eating the drive's first press was the root cause of the "double press to enable
steering" report (route 00000009, t=61.8: press with cruise_avail=False -> no event).
The panda-side LDA decode is unconditional to match; the two gates must stay in lockstep.
"""
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

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

import openpilot.sunnypilot.mads.mads as mads_module
from openpilot.sunnypilot.mads.mads import ModularAssistiveDrivingSystem
from openpilot.cereal import custom
from opendbc.car import structs

ButtonType = structs.CarState.ButtonEvent.Type
EventNameSP = custom.OnroadEventSP.EventName


class FakeEvents:
  def __init__(self):
    self.names = []

  def add(self, name):
    self.names.append(name)

  def remove(self, name):
    self.names = [n for n in self.names if n != name]

  def has(self, name):
    return name in self.names

  def contains(self, name):
    return name in self.names

  def contains_in_list(self, lst):
    return any(n in self.names for n in lst)


def make_mads_for_button(allow_always):
  """handler-level fixture: MADS disabled, openpilot disengaged, main cruise NOT available"""
  mads = ModularAssistiveDrivingSystem.__new__(ModularAssistiveDrivingSystem)
  mads.enabled = False
  mads.active = False
  mads.allow_always = allow_always
  mads.no_main_cruise = False
  mads.main_enabled_toggle = False
  mads.unified_engagement_mode = False
  mads.steering_mode_on_brake = 0  # Remain Active
  mads.lateral_mismatch_counter = 0
  mads.events = FakeEvents()
  mads.events_sp = FakeEvents()
  mads.state_machine = SimpleNamespace(state=0)
  mads.selfdrive = SimpleNamespace(
    enabled=False, enabled_prev=False,
    CS_prev=SimpleNamespace(cruiseState=SimpleNamespace(available=False),
                            gasPressed=False),
  )
  return mads


def make_cs_with_lkas_press():
  cs = structs.CarState()
  be = structs.CarState.ButtonEvent()
  be.type = ButtonType.lkas
  be.pressed = True
  cs.buttonEvents = [be]
  cs.cruiseState.available = False  # main cruise not armed: the case under test
  return cs


class TestLkasButtonGate(unittest.TestCase):
  def test_press_without_main_enables_when_allow_always(self):
    # the fixed behavior: first press of the drive works with main off
    mads = make_mads_for_button(allow_always=True)
    mads.update_events(make_cs_with_lkas_press())
    assert mads.events_sp.has(EventNameSP.lkasEnable)

  def test_press_without_main_dropped_when_gated(self):
    # the pre-fix behavior, kept as the contrast case documenting the gate's effect
    mads = make_mads_for_button(allow_always=False)
    mads.update_events(make_cs_with_lkas_press())
    assert not mads.events_sp.has(EventNameSP.lkasEnable)


class TestAllowAlwaysBrands(unittest.TestCase):
  @staticmethod
  def _init_mads(brand):
    with mock.patch.object(mads_module, "StateMachine", lambda s: SimpleNamespace(state=0)), \
         mock.patch.object(mads_module, "read_steering_mode_param", lambda *a: 0):
      params = SimpleNamespace(get_bool=lambda k: False)
      selfdrive = SimpleNamespace(
        CP=structs.CarParams(brand=brand),
        CP_SP=structs.CarParamsSP(),
        params=params, events=FakeEvents(), events_sp=FakeEvents(),
      )
      return ModularAssistiveDrivingSystem(selfdrive)

  def test_toyota_is_allow_always(self):
    assert self._init_mads("toyota").allow_always is True

  def test_other_brand_stays_gated(self):
    assert self._init_mads("honda").allow_always is False


if __name__ == "__main__":
  unittest.main()
