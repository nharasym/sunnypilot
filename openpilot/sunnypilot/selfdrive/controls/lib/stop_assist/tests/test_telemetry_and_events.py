"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(stop-assist): telemetry serialises in every state, the event table entry exists and can only warn, and the
# fork ordinals are pinned. unittest.TestCase, setUp only (runs under the on-device shim too).
import copy
import unittest
from types import SimpleNamespace as NS

from openpilot.cereal import custom, log
from openpilot.common.realtime import DT_CTRL
from openpilot.selfdrive.selfdrived.alertmanager import AlertManager
from openpilot.selfdrive.selfdrived.events import EVENTS, EventName
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import SOURCE_E2E, StopAssist
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.stop_assist import Reason, State
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.tests.sim_helpers import FakeEvents, FakeParams, Sim, quiet_faults
from openpilot.sunnypilot.selfdrive.selfdrived import events as ev
from openpilot.sunnypilot.selfdrive.selfdrived.events_base import ET, Alert, Priority

StopAssistSchema = custom.LongitudinalPlanSP.StopAssist
EventNameSP = custom.OnroadEventSP.EventName


def camel(name: str) -> str:
  head, *rest = name.lower().split('_')
  return head + ''.join(w.capitalize() for w in rest)


def members(cls) -> dict[str, int]:
  return {camel(k): v for k, v in vars(cls).items() if k.isupper()}


class TestOrdinals(unittest.TestCase):
  def test_stop_assist_field_is_ordinal_8(self):
    # a port that lands an upstream @8 must renumber ours, not silently collide
    field = custom.LongitudinalPlanSP.schema.fields['stopAssist']
    assert field.proto.ordinal.explicit == 8

  def test_event_is_28(self):
    assert int(EventNameSP.stopAssist) == 28
    assert ev.EVENT_NAME_SP[28] == "stopAssist"

  def test_source_is_the_stock_e2e_as_a_plain_int(self):
    assert SOURCE_E2E == 4 and type(SOURCE_E2E) is int
    assert SOURCE_E2E == log.LongitudinalPlan.LongitudinalPlanSource.e2e

  def test_python_states_and_reasons_match_the_schema(self):
    assert members(State) == dict(StopAssistSchema.State.schema.enumerants)
    assert members(Reason) == dict(StopAssistSchema.Reason.schema.enumerants)


class TestTelemetry(unittest.TestCase):
  def test_fill_serialises_in_every_state(self):
    """Drive real scenarios with numpy inputs through every state; fill a real LongitudinalPlanSP each frame."""
    seen: set[int] = set()

    def drive(sim, frames, sa):
      for _ in range(frames):
        sim.step()
        msg = custom.LongitudinalPlanSP.new_message()
        sa.fill(msg.stopAssist)
        with custom.LongitudinalPlanSP.from_bytes(msg.to_bytes()) as r:
          s = r.stopAssist
          assert s.state.raw == sa.state, (s.state, sa.state)
          assert s.active == bool(sim.records[-1]['entries'])
          if s.active:
            a, _, stop = sim.records[-1]['entries'][0]
            assert abs(s.aCandidate - a) < 1e-5 and s.shouldStop == stop
          seen.add(s.state.raw)

    def drive_until(sim, sa, state, max_frames=1000):
      for _ in range(max_frames):
        drive(sim, 1, sa)
        if sa.state == state:
          return
      raise AssertionError(f"state {state} not reached")

    def new(lights=True):
      sa = StopAssist(NS(openpilotLongitudinalControl=True), FakeEvents(), params=FakeParams(lights, 3))
      return sa, Sim(sa, v0=12.0, stop_at=55.0)

    sa, sim = new(lights=False)             # OFF
    drive(sim, 5, sa)

    sa, sim = new()                         # CONFIRM, APPROACH, FINAL, HOLD, RELEASED, IDLE
    drive_until(sim, sa, State.HOLD)
    drive(sim, 30, sa)
    sim.green = True
    drive_until(sim, sa, State.IDLE)

    sa, sim = new()                         # RAMP_OUT (a lead cuts in), then FAULTED
    drive_until(sim, sa, State.APPROACH)
    sim.lead = (10.0, 0.0)
    drive(sim, 3, sa)
    sim.lead = None
    sa._law = None                          # the next law call (the next approach) raises
    with quiet_faults() as log:
      drive_until(sim, sa, State.FAULTED)
      drive(sim, 5, sa)
    assert log.exceptions == 1

    assert seen == set(range(State.FAULTED + 1)), sorted(seen)

  def test_fill_when_off_writes_every_field(self):
    sa = StopAssist(NS(openpilotLongitudinalControl=False), FakeEvents(), params=FakeParams())
    Sim(sa).step()
    msg = custom.LongitudinalPlanSP.new_message()
    sa.fill(msg.stopAssist)
    with custom.LongitudinalPlanSP.from_bytes(msg.to_bytes()) as r:
      s = r.stopAssist
      assert s.state == 'off' and not s.active and s.planStopM == -1.0 and s.reason == 'none'


class TestEvent(unittest.TestCase):
  def test_entry_is_warning_only(self):
    entry = ev.EVENTS_SP[EventNameSP.stopAssist]
    assert set(entry) == {ET.WARNING}
    for et in (ET.NO_ENTRY, ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE, ET.USER_DISABLE, ET.ENABLE, ET.PERMANENT):
      assert et not in entry

  def _alert(self, sm) -> Alert:
    a = ev.stop_assist_alert(None, None, sm, False, 0, None)
    assert isinstance(a, Alert)
    assert a.alert_size == ev.AlertSize.small and a.alert_status == ev.AlertStatus.normal
    assert a.visual_alert == ev.VisualAlert.none and a.audible_alert == ev.AudibleAlert.none
    assert a.duration == int(0.1 / DT_CTRL)
    # "Stopping" (a whole approach) is LOWER so it never hides a LOW alert; the one-off fault notice is LOW
    assert a.priority == (Priority.LOW if a.alert_text_1 == "Stop Assist Off" else Priority.LOWER), (a.alert_text_1, a.priority)
    return a

  def test_callback_text_for_every_state(self):
    for name in StopAssistSchema.State.schema.enumerants:
      msg = custom.LongitudinalPlanSP.new_message()
      msg.stopAssist.state = name
      a = self._alert({'longitudinalPlanSP': msg})
      if name == 'faulted':
        assert (a.alert_text_1, a.alert_text_2) == ("Stop Assist Off", "Turned off for this drive")
      else:
        assert (a.alert_text_1, a.alert_text_2) == ("Stopping", ""), name

  def test_callback_never_raises(self):
    for sm in ({}, {'longitudinalPlanSP': NS()}, {'longitudinalPlanSP': None}, None, NS()):
      assert self._alert(sm).alert_text_1 == "Stopping"

  def test_round_trip_planner_to_selfdrived(self):
    planner_side = ev.EventsSP()
    planner_side.add(EventNameSP.stopAssist)
    msgs = planner_side.to_msg()
    assert len(msgs) == 1 and msgs[0].warning and not msgs[0].noEntry and not msgs[0].softDisable

    selfdrived_side = ev.EventsSP()
    selfdrived_side.add_from_msg(msgs)
    assert selfdrived_side.has(EventNameSP.stopAssist)
    msg = custom.LongitudinalPlanSP.new_message()
    msg.stopAssist.state = 'approach'
    alerts = selfdrived_side.create_alerts([ET.PERMANENT, ET.WARNING, ET.NO_ENTRY, ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE],
                                           [None, None, {'longitudinalPlanSP': msg}, False, 0, None])
    assert [(a.alert_type, a.alert_text_1) for a in alerts] == [("stopAssist/warning", "Stopping")]
    for et in (ET.NO_ENTRY, ET.SOFT_DISABLE, ET.IMMEDIATE_DISABLE, ET.USER_DISABLE):
      assert not selfdrived_side.contains(et)

  def test_stopping_never_hides_an_active_low_alert(self):
    # the real selfdrived AlertManager shows the highest (priority, start frame). At LOW, "Stopping" (which starts
    # with the approach) would replace a DM prompt already showing, and its steerRequired visual (the Toyota
    # cluster's LDA_ALERT) for the whole approach. A silent LOWEST alert (steering override) must not hide it.
    msg = custom.LongitudinalPlanSP.new_message()
    msg.stopAssist.state = 'approach'
    sm = {'longitudinalPlanSP': msg}

    def stock(name, et):
      a = copy.copy(EVENTS[getattr(EventName, name)][et])
      a.alert_type, a.event_type = f"{name}/{et}", et
      return a

    def stopping():
      a = copy.copy(ev.stop_assist_alert(None, None, sm, False, 0, None))
      a.alert_type, a.event_type = "stopAssist/warning", ET.WARNING
      return a

    for name, et, wins in (("driverUnresponsive1", ET.PERMANENT, True), ("driverDistracted1", ET.PERMANENT, True),
                           ("preLaneChangeLeft", ET.WARNING, True), ("steerOverride", ET.OVERRIDE_LATERAL, False)):
      am = AlertManager()
      for frame in range(1, 301):  # 3 s at 100 Hz; the approach (and "Stopping") starts at 1 s
        am.add_many(frame, [stock(name, et)] + ([stopping()] if frame >= 100 else []))
        am.process_alerts(frame, set())
        expected = f"{name}/{et}" if wins or frame < 100 else "stopAssist/warning"
        assert am.current_alert.alert_type == expected, (name, frame, am.current_alert.alert_type)

  def test_planner_adds_the_event_through_the_real_events_object(self):
    events = ev.EventsSP()
    sa = StopAssist(NS(openpilotLongitudinalControl=True), events, params=FakeParams())
    sim = Sim(sa)
    found = False
    for _ in range(80):
      events.clear()
      sim.step()
      if sa.state in (State.APPROACH, State.FINAL):
        assert events.names == [EventNameSP.stopAssist]
        found = True
    assert found
