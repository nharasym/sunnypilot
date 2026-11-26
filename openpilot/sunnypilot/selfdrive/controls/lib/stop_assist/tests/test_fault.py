"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(stop-assist): faults. An exception anywhere turns the feature off for the process; a held stop stays held
# until the driver acts; fill() never raises. unittest.TestCase, setUp only (runs under the on-device shim too).
import unittest
from types import SimpleNamespace as NS

import numpy as np

from openpilot.cereal import custom
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import SOURCE_E2E, StopAssist
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import stop_assist as sa_module
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.stop_assist import FAULT_ALERT_FRAMES, Reason, State
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.tests.sim_helpers import FakeEvents, FakeParams, Sim

EVENT = int(custom.OnroadEventSP.EventName.stopAssist)
LATCH = (0.0, SOURCE_E2E, True)


def _boom(*args, **kwargs):
  raise RuntimeError("injected")


class _CountingLog:
  def __init__(self):
    self.exceptions = 0

  def exception(self, *args, **kwargs):
    self.exceptions += 1


class _FaultBase(unittest.TestCase):
  def setUp(self):
    self.ev = FakeEvents()
    self.sa = StopAssist(NS(openpilotLongitudinalControl=True), self.ev, params=FakeParams(True, 3))
    self.sim = Sim(self.sa, v0=12.0, stop_at=55.0)
    self.log = _CountingLog()

  def step(self):
    self.ev.clear()
    rec = self.sim.step()
    rec['events'] = list(self.ev.events)
    return rec

  def until_state(self, state, max_frames=2000):
    for _ in range(max_frames):
      rec = self.step()
      if rec['state'] == state:
        return rec
    raise AssertionError("state not reached")

  def inject(self, method):
    setattr(self.sa, method, _boom)  # the instance attribute shadows the method

  def with_counting_log(self, fn):
    saved = sa_module.cloudlog
    sa_module.cloudlog = self.log
    try:
      return fn()
    finally:
      sa_module.cloudlog = saved


class TestFaultMidApproach(_FaultBase):
  def test_returns_nothing_and_stays_off(self):
    self.until_state(State.APPROACH)
    self.inject('_law')

    def run():
      rec = self.step()
      assert rec['entries'] == [] and rec['state'] == State.FAULTED and rec['reason'] == Reason.FAULT
      for _ in range(300):
        assert self.step()['entries'] == []
    self.with_counting_log(run)
    assert self.log.exceptions == 1  # logged once, not every frame
    assert self.sa.state == State.FAULTED

  def test_off_alert_for_three_seconds(self):
    self.until_state(State.APPROACH)
    self.inject('_law')
    shown = [EVENT in self.step()['events'] for _ in range(FAULT_ALERT_FRAMES + 40)]
    assert all(shown[:FAULT_ALERT_FRAMES]) and not any(shown[FAULT_ALERT_FRAMES:])
    assert FAULT_ALERT_FRAMES == 60


class TestFaultInHold(_FaultBase):
  def setUp(self):
    super().setUp()
    self.until_state(State.HOLD)
    self.inject('_hold')

  def test_latch_holds_until_gas(self):
    rec = self.step()
    assert rec['entries'] == [LATCH] and rec['state'] == State.FAULTED and rec['out_stop']
    self.sim.green = True  # the latch does not release on the model's go
    for _ in range(100):
      rec = self.step()
      assert rec['entries'] == [LATCH] and self.sim.v == 0.0
    self.sim.gas = True
    assert self.step()['entries'] == []
    self.sim.gas = False
    for _ in range(100):
      assert self.step()['entries'] == []

  def test_latch_ends_on_brake(self):
    assert self.step()['entries'] == [LATCH]
    self.sim.brake = True
    assert self.step()['entries'] == []
    self.sim.brake = False
    assert self.step()['entries'] == []

  def test_latch_ends_on_disengage(self):
    assert self.step()['entries'] == [LATCH]
    self.sim.long_active = False
    assert self.step()['entries'] == []
    self.sim.long_active = True
    assert self.step()['entries'] == []

  def test_latch_read_failure_returns_nothing(self):
    assert self.step()['entries'] == [LATCH]
    assert self.sa.update({}, []) == []  # carState cannot be read: give the car back to stock
    assert self.step()['entries'] == []


class TestUpdateNeverRaises(unittest.TestCase):
  def test_garbage_inputs(self):
    for sm, cands in (({}, []), (None, None), ({'modelV2': 1}, [(1, 2)]), (NS(logMonoTime=None), [()])):
      sa = StopAssist(NS(openpilotLongitudinalControl=True), FakeEvents(), params=FakeParams())
      assert sa.update(sm, cands) == []
      assert sa.state == State.FAULTED
      assert sa.update(sm, cands) == []

  def test_misspelled_or_missing_param_key_faults_instead_of_raising(self):
    params = FakeParams()
    del params.d["StopAssistLightsOffsetFt"]  # the device raises UnknownKeyName for a key missing from params_keys.h
    sa = StopAssist(NS(openpilotLongitudinalControl=True), FakeEvents(), params=params)
    sim = Sim(sa)
    assert sim.step()['entries'] == [] and sa.state == State.FAULTED

  def test_broken_events_object_faults_instead_of_raising(self):
    sa = StopAssist(NS(openpilotLongitudinalControl=True), object(), params=FakeParams())
    sim = Sim(sa)
    for _ in range(40):  # the first APPROACH frame adds the event
      sim.step()
    assert sa.state == State.FAULTED


class TestFill(unittest.TestCase):
  def setUp(self):
    self.sa = StopAssist(NS(openpilotLongitudinalControl=True), FakeEvents(), params=FakeParams())

  def _numpy_internals(self):
    sa = self.sa
    sa._setting_on = np.bool_(True)
    sa._offset_m = np.float32(0.9144)
    sa._remaining = np.float64(12.5)
    sa._plan_stop_m = np.float32(40.25)
    sa._a_need = np.float64(1.25)
    sa._a_model = np.float32(-0.75)
    sa._hold_s = np.float64(1.5)
    sa._reason = np.int64(Reason.GREEN)
    sa._big = np.bool_(True)
    sa._out = [(np.float64(-1.5), np.int64(SOURCE_E2E), np.bool_(True))]

  def test_numpy_values_in_every_state(self):
    for state in range(State.FAULTED + 1):
      self.setUp()
      self._numpy_internals()
      if state == State.FAULTED:
        self.sa._faulted = True
      else:
        self.sa._state = np.int64(state)
      msg = custom.LongitudinalPlanSP.new_message()
      self.sa.fill(msg.stopAssist)
      assert (self.sa.state == State.FAULTED) == (state == State.FAULTED), state
      with custom.LongitudinalPlanSP.from_bytes(msg.to_bytes()) as r:
        s = r.stopAssist
        assert s.state.raw == state
        assert s.enabled and s.active and s.shouldStop and s.modelBig
        assert abs(s.aCandidate + 1.5) < 1e-6 and abs(s.offsetM - 0.9144) < 1e-6 and abs(s.remainingM - 12.5) < 1e-6
        assert abs(s.planStopM - 40.25) < 1e-6 and abs(s.aNeed - 1.25) < 1e-6 and abs(s.modelAccel + 0.75) < 1e-6
        assert abs(s.holdS - 1.5) < 1e-6 and s.reason == 'green'

  def test_never_raises_and_turns_the_feature_off(self):
    class Broken:
      def __setattr__(self, name, value):
        raise RuntimeError("broken struct")
    self.sa.fill(Broken())
    assert self.sa.state == State.FAULTED

  def test_a_partial_failure_still_writes_faulted(self):
    class Picky:
      def __setattr__(self, name, value):
        if name == 'aNeed':
          raise RuntimeError("one field fails")
        object.__setattr__(self, name, value)
    s = Picky()
    self.sa.fill(s)
    assert s.state == State.FAULTED and self.sa.state == State.FAULTED

  def test_fill_error_during_hold_keeps_the_hold_latched(self):
    ev = FakeEvents()
    sa = StopAssist(NS(openpilotLongitudinalControl=True), ev, params=FakeParams())
    sim = Sim(sa)
    sim.run_until(lambda r: r['state'] == State.HOLD)

    class Broken:
      def __setattr__(self, name, value):
        raise RuntimeError("broken struct")
    sa.fill(Broken())
    assert sim.step()['entries'] == [LATCH]
