"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(stop-assist): the state machine driven through whole stops on a closed-loop sim with numpy inputs.
# unittest.TestCase, setUp only (runs under the on-device shim too). Every scenario also checks the entry invariants.
import math
import random
import unittest
from types import SimpleNamespace as NS

import numpy as np

from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import SOURCE_E2E, StopAssist
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import constants as C
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.stop_assist import State, Reason
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.stop_point import planned_stop
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.tests.sim_helpers import (
  DT_NS, EPS, FakeEvents, FakeParams, Sim, make_sm, plan_cruise, plan_standstill_go, plan_stop, stock_candidates,
)

EVENT = 28  # custom.OnroadEventSP.EventName.stopAssist (pinned in test_telemetry_and_events)
HOLD_ENTRY = (0.0, SOURCE_E2E, True)
SMOOTH = 1.0 - math.exp(-0.05 / 0.3)  # modeld smooths the published action over 0.3 s


def no_stop_plan(v):
  """A plan without a planned stop: its minimum stays above LIGHTS_STOP_V."""
  x, vv = plan_cruise(v)
  return x, np.maximum(vv, C.LIGHTS_STOP_V + 0.3).astype(np.float32)


def new_sa(lights=True, offset_ft=3, op_long=True):
  ev = FakeEvents()
  params = FakeParams(lights, offset_ft)
  sa = StopAssist(NS(openpilotLongitudinalControl=op_long), ev, params=params)
  return sa, ev, params


def states(records):
  seq = []
  for r in records:
    if not seq or seq[-1] != r['state']:
      seq.append(r['state'])
  return seq


class _Base(unittest.TestCase):
  def setUp(self):
    self.sa, self.ev, self.params = new_sa()
    self.sim = Sim(self.sa, v0=12.0, stop_at=55.0)

  def step(self, **kw):
    self.ev.clear()
    rec = self.sim.step(**kw)
    rec['events'] = list(self.ev.events)
    return rec

  def run_until(self, pred, max_frames=2000):
    for _ in range(max_frames):
      rec = self.step()
      if pred(rec):
        return rec
    raise AssertionError("condition not reached")

  def until_state(self, state, max_frames=2000):
    return self.run_until(lambda r: r['state'] == state, max_frames)

  def to_hold(self):
    self.until_state(State.HOLD)


class TestFullStop(_Base):
  def test_arm_confirm_approach_final_hold_release(self):
    self.to_hold()
    rest_x = self.sim.x
    hold_t0 = self.sim.t
    for _ in range(40):  # 2 s at the red light
      rec = self.step()
      assert rec['state'] == State.HOLD and rec['entries'] == [HOLD_ENTRY]
    self.sim.green = True
    go_t0 = self.sim.t + DT_NS
    rec = self.until_state(State.RELEASED, 40)
    assert rec['entries'] == []
    assert rec['t'] - hold_t0 >= 1e9 * C.HOLD_MIN_S
    assert C.GO_CONFIRM_S - EPS <= (rec['t'] - go_t0) / 1e9 <= C.GO_CONFIRM_S + 0.05 + EPS
    assert rec['reason'] == Reason.GREEN
    self.until_state(State.IDLE, 400)  # drove on: 10 m, 3 m/s, no planned stop for 0.5 s
    assert states(self.sim.records) == [State.CONFIRM, State.APPROACH, State.FINAL, State.HOLD, State.RELEASED, State.IDLE]

    # it stopped at the model's point minus the offset, not beyond the model's point, braking no harder than A_CAP
    target = 55.0 - 3 * C.FT_TO_M
    assert target - 1.0 <= rest_x <= target + 0.3, (rest_x, target)
    out = [r['out_a'] for r in self.sim.records]
    assert min(out) >= -C.A_CAP - EPS
    confirm = [r for r in self.sim.records if r['state'] == State.CONFIRM]
    assert len(confirm) >= int(C.CONFIRM_S / 0.05) and all(r['entries'] == [] for r in confirm)

  def test_event_only_while_approach_or_final(self):
    self.to_hold()
    for _ in range(20):
      self.step()
    self.sim.green = True
    self.until_state(State.IDLE, 600)
    for r in self.sim.records:
      shown = EVENT in r.get('events', [])
      assert shown == (r['state'] in (State.APPROACH, State.FINAL)), (r['state'], shown)

  def test_offset_moves_the_stop_point(self):
    rests = {}
    for ft in (0, 10):
      sa, ev, _ = new_sa(offset_ft=ft)
      sim = Sim(sa, v0=12.0, stop_at=55.0)
      sim.run_until(lambda r: r['state'] == State.HOLD)
      rests[ft] = sim.x
    assert math.isclose(rests[0] - rests[10], 10 * C.FT_TO_M, abs_tol=0.3), rests
    assert rests[0] < 55.0


class TestHoldRelease(_Base):
  def test_regression_hold_releases_on_the_degenerate_standstill_plan(self):
    # the merged plan released on "the plan shows no stop", which a standstill plan never shows: its minimum is
    # the car's own 0 m/s. The model's go must release it.
    self.to_hold()
    for _ in range(int(C.HOLD_MIN_S / 0.05)):  # the 1.0 s dwell
      assert self.step()['entries'] == [HOLD_ENTRY]
    x, v = plan_standstill_go(30.0)
    assert np.all(v[:4] == 0.0) and math.isclose(float(x[-1]), 30.0, abs_tol=1e-3)
    assert float(np.min(v)) < C.LIGHTS_STOP_V and planned_stop(x, v) is None
    self.sim.plan_override = (x, v)
    self.sim.model_stop_override = False
    self.sim.a_model_override = 0.5
    released = None
    for k in range(20):
      rec = self.step()
      if rec['state'] == State.RELEASED:
        released = k
        break
      assert rec['entries'] == [HOLD_ENTRY]
    assert released is not None, "the hold never released"
    assert (released * 0.05) >= C.GO_CONFIRM_S - EPS and released * 0.05 <= C.GO_CONFIRM_S + 0.05 + EPS, released

  def test_single_frame_stop_flicker_restarts_the_go(self):
    self.to_hold()
    for _ in range(25):
      self.step()
    self.sim.green = True
    for _ in range(6):  # 0.3 s of go
      assert self.step()['state'] == State.HOLD
    self.sim.model_stop_override = True  # one frame of "stop"
    assert self.step()['entries'] == [HOLD_ENTRY]
    self.sim.model_stop_override = None
    n = 0
    while self.step()['state'] == State.HOLD:
      n += 1
      assert n < 30
    assert n * 0.05 >= C.GO_CONFIRM_S - EPS, n  # a fresh 0.5 s after the flicker

  def test_single_frame_go_does_not_release(self):
    self.to_hold()
    for _ in range(30):
      self.step()
    self.sim.model_stop_override = False  # the measured 50-100 ms stop-flag dropout
    rec = self.step()
    assert rec['state'] == State.HOLD and rec['entries'] == [HOLD_ENTRY] and rec['out_stop']
    self.sim.model_stop_override = None
    for _ in range(40):
      rec = self.step()
      assert rec['state'] == State.HOLD and rec['entries'] == [HOLD_ENTRY]

  def test_hold_lasts_at_least_the_dwell(self):
    hold = self.until_state(State.HOLD)
    self.sim.green = True  # the light turns green just as the car stops
    rel = self.until_state(State.RELEASED, 100)
    assert rel['t'] - hold['t'] >= 1e9 * C.HOLD_MIN_S - 1

  def test_green_while_crawling_ramps_out_instead_of_stopping(self):
    # below LOW_V_GO the plan minimum is the car's own speed: green is the model asking for accel, held LOW_V_GO_S
    self.until_state(State.FINAL)
    self.run_until(lambda r: r['v'] < 1.0)
    self.sim.green = True
    rec = self.until_state(State.RAMP_OUT, 20)
    assert rec['reason'] == Reason.GREEN
    self.until_state(State.IDLE, 40)
    assert not any(r['state'] == State.HOLD for r in self.sim.records)


class TestGreenDuringApproach(_Base):
  def _dropout(self, rise_rate):
    self.until_state(State.APPROACH)
    for _ in range(20):
      self.step()
    a = self.sim.records[-1]['a_model']
    start = len(self.sim.records)
    self.sim.plan_override = plan_cruise(self.sim.v)
    for _ in range(80):
      a = min(a + rise_rate * 0.05, 0.5)
      self.sim.plan_override = plan_cruise(self.sim.v)
      self.sim.a_model_override = a
      rec = self.step()
      if rec['state'] in (State.IDLE, State.CONFIRM):
        break
    return self.sim.records[start:]

  def test_green_ramps_out_and_never_brakes_harder_than_the_model(self):
    recs = self._dropout(rise_rate=1.5)
    assert any(r['state'] == State.RAMP_OUT and r['reason'] == Reason.GREEN for r in recs)
    assert recs[-1]['state'] == State.IDLE
    for r in recs:
      for a, _, stop in r['entries']:
        assert a >= r['a_model'] - C.DROP_CLAMP - EPS, (a, r['a_model'])
        assert not stop

  def test_dropout_while_the_model_still_brakes_is_kept_then_lost(self):
    recs = self._dropout(rise_rate=0.0)
    ramp = [r for r in recs if r['state'] == State.RAMP_OUT]
    assert ramp and ramp[0]['reason'] == Reason.STOP_LOST
    kept = [r for r in recs if r['state'] in (State.APPROACH, State.FINAL)]
    assert (len(kept) + 1) * 0.05 > C.DROP_MAX_S - EPS  # kept for the whole 2.5 s
    for r in recs:
      for a, _, _ in r['entries']:
        assert a >= r['a_model'] - C.DROP_CLAMP - EPS

  def test_short_dropout_keeps_the_target(self):
    self.until_state(State.APPROACH)
    for _ in range(10):
      self.step()
    target = self.sa._tracker.target
    self.sim.plan_override = plan_cruise(self.sim.v)
    for _ in range(3):
      assert self.step()['state'] == State.APPROACH
    assert self.sa._tracker.target == target
    self.sim.plan_override = None
    for _ in range(5):
      assert self.step()['state'] == State.APPROACH


class TestExits(_Base):
  def test_lead_cut_in_ramps_out(self):
    self.until_state(State.APPROACH)
    for _ in range(10):
      self.step()
    self.sim.lead = (15.0, 0.0)
    rec = self.step()
    assert rec['state'] == State.RAMP_OUT and rec['reason'] == Reason.LEAD and rec['entries']
    rec = self.until_state(State.IDLE, 60)
    assert rec['reason'] == Reason.LEAD
    for _ in range(20):  # the lead keeps arming blocked
      assert self.step()['state'] == State.IDLE

  def test_gas_releases_and_blocks_rearm_until_cleared(self):
    self.until_state(State.APPROACH)
    for _ in range(10):
      self.step()
    self.sim.gas = True
    rec = self.step()
    assert rec['state'] == State.RELEASED and rec['entries'] == [] and rec['reason'] == Reason.GAS
    for _ in range(10):
      assert self.step()['state'] == State.RELEASED
    self.sim.gas = False
    # the same red light is still ahead and the model still plans it: no re-arm
    for _ in range(300):
      rec = self.step()
      assert rec['state'] == State.RELEASED and rec['entries'] == []
    assert self.sim.v < 0.5  # the model stopped the car on its own
    self.sim.green = True
    self.until_state(State.IDLE, 600)
    # a new stop ahead arms again
    self.sim.green = False
    self.sim.stop_at = self.sim.x + 5 * self.sim.v
    self.until_state(State.CONFIRM, 200)

  def test_override_lag_after_the_accelerator_keeps_released(self):
    self.until_state(State.APPROACH)
    self.sim.gas = True
    self.step()
    self.sim.gas = False
    self.sim.long_active = False  # longActive clears 10-20 ms after the accelerator is lifted
    assert self.step()['state'] == State.RELEASED
    self.sim.long_active = True
    assert self.step()['state'] == State.RELEASED

  def test_brake_goes_idle_at_once(self):
    self.until_state(State.APPROACH)
    for _ in range(10):
      self.step()
    self.sim.brake = True
    self.sim.long_active = False
    rec = self.step()
    assert rec['state'] == State.IDLE and rec['entries'] == [] and rec['reason'] == Reason.BRAKE

  def test_disengage_goes_idle_at_once(self):
    self.to_hold()
    self.sim.long_active = False
    self.sim.enabled = False
    rec = self.step()
    assert rec['state'] == State.IDLE and rec['entries'] == [] and rec['reason'] == Reason.DISENGAGED

  def test_setting_off_while_approaching_ramps_out_then_off(self):
    self.until_state(State.APPROACH)
    self.params.d["StopAssistLights"] = False
    rec = self.until_state(State.RAMP_OUT, 61)
    assert rec['reason'] == Reason.SETTING_OFF and rec['entries']
    rec = self.until_state(State.OFF, 60)
    assert rec['entries'] == []
    for _ in range(100):
      assert self.step()['entries'] == []

  def test_setting_off_during_hold_keeps_the_hold(self):
    self.to_hold()
    self.params.d["StopAssistLights"] = False
    for _ in range(80):  # past the next settings read
      rec = self.step()
      assert rec['state'] == State.HOLD and rec['entries'] == [HOLD_ENTRY]
    self.sim.green = True
    rec = self.until_state(State.OFF, 40)
    assert rec['entries'] == []

  def test_offset_change_applies_only_from_idle(self):
    self.until_state(State.APPROACH)
    target = self.sa._tracker.target
    self.params.d["StopAssistLightsOffsetFt"] = 10
    for _ in range(70):
      self.step()
    assert self.sa._offset_m == 3 * C.FT_TO_M
    assert abs(self.sa._tracker.target - target) < 1.6  # tracked, not shifted by the new offset
    self.to_hold()
    self.sim.green = True
    self.until_state(State.IDLE, 600)
    self.step()
    assert math.isclose(self.sa._offset_m, 10 * C.FT_TO_M)

  def test_model_gap_goes_idle(self):
    self.until_state(State.APPROACH)
    rec = self.step(dt_ns=int((C.MODEL_GAP_S + 0.05) * 1e9))
    assert rec['state'] == State.IDLE and rec['entries'] == [] and rec['reason'] == Reason.MODEL_GAP

  def test_model_switch_ramps_out_and_blocks_arming(self):
    self.sim.model_scale = 1.0
    self.until_state(State.APPROACH)
    self.sim.big = True
    rec = self.step()
    t_switch = rec['t']
    assert rec['state'] == State.RAMP_OUT and rec['reason'] == Reason.MODEL_SWITCH and rec['entries']
    rec = self.run_until(lambda r: r['state'] == State.CONFIRM, 200)
    assert rec['t'] - t_switch >= 1e9 * C.MODEL_SWITCH_BLOCK_S

  def test_model_switch_keeps_a_hold(self):
    self.to_hold()
    self.sim.big = True
    for _ in range(10):
      rec = self.step()
      assert rec['state'] == State.HOLD and rec['entries'] == [HOLD_ENTRY]

  def test_new_stop_beyond_the_target_ramps_out(self):
    self.until_state(State.APPROACH)
    self.sim.stop_at += 12.0  # the model now plans a different stop, well beyond ours
    rec = self.until_state(State.RAMP_OUT, 30)
    assert rec['reason'] == Reason.NEW_STOP

  def test_no_e2e_candidate_returns_nothing(self):
    self.until_state(State.APPROACH)
    self.sim.with_e2e = False
    rec = self.step()
    assert rec['entries'] == [] and rec['reason'] == Reason.NO_MODEL_CANDIDATE and rec['state'] == State.IDLE
    for _ in range(100):
      assert self.step()['entries'] == []


class TestArming(_Base):
  def _never(self, frames=80, each=None):
    for _ in range(frames):
      if each is not None:
        each()
      rec = self.step()
      assert rec['state'] in (State.OFF, State.IDLE) and rec['entries'] == [], rec['state']
    return rec

  def test_too_late_is_refused(self):
    self.sim.stop_at = 25.0  # 12 m/s with 25 m left needs more than MAX_ARM_NEED
    rec = self._never(3)
    assert rec['reason'] == Reason.TOO_LATE

  def test_speed_band(self):
    self.sim.v = C.ARM_V_MIN - 0.1  # an easy stop (need ~0.4 m/s^2), refused only for the speed
    self.sim.stop_at = 12.0
    self.sim.a_model_override = -0.2
    self._never()
    # above ARM_V_MAX every stop within 100 m is also too late, so check that the speed gate is the one that fired
    sa, ev, _ = new_sa()
    sim = Sim(sa, v0=C.ARM_V_MAX + 0.5, stop_at=90.0)
    sim.a_model_override = -0.2
    for _ in range(20):
      rec = sim.step()
      assert rec['state'] == State.IDLE and rec['reason'] == Reason.NONE, rec['reason']

  def test_model_not_braking_does_not_arm(self):
    self.sim.a_model_override = C.ARM_MODEL_ACCEL + 0.01
    self._never()

  def test_force_decel_blocks_arming(self):
    self.sim.force_decel = True
    self._never()

  def test_lead_blocks_arming(self):
    def queued():  # a car stopped 5 m before the line: the lead planner owns this stop
      self.sim.lead = (self.sim.stop_at - self.sim.x - 5.0, -self.sim.v)
    rec = self._never(each=queued)
    assert rec['reason'] == Reason.NONE

  def test_lead_beyond_the_stop_point_plus_margin_does_not_block(self):
    self.sim.lead = (55.0 + C.LEAD_MARGIN_M + 5.0, 0.0)
    self.until_state(State.APPROACH, 40)

  def test_lead_pulling_away_does_not_block(self):
    self.sim.lead = (40.0, C.LEAD_AWAY_VREL + 0.5)
    self.until_state(State.APPROACH, 40)

  def test_scattered_points_do_not_confirm(self):
    flip = [0]

    def scatter():
      flip[0] ^= 1
      d = 55.0 - self.sim.x + (4.0 if flip[0] else -4.0)
      return plan_stop(self.sim.v, d)
    for _ in range(40):
      self.sim.plan_override = scatter()
      rec = self.step()
      assert rec['state'] in (State.IDLE, State.CONFIRM) and rec['entries'] == []

  def test_setting_off_never_arms(self):
    self.params.d["StopAssistLights"] = False
    rec = self._never(300)
    assert rec['state'] == State.OFF and rec['reason'] == Reason.SETTING_OFF


class TestInert(unittest.TestCase):
  def test_no_openpilot_longitudinal_is_always_nothing(self):
    class Raising:
      def get_bool(self, k, block=False):
        raise AssertionError("params read while inert")
      get = get_bool
    sa = StopAssist(NS(openpilotLongitudinalControl=False), FakeEvents(), params=Raising())
    sim = Sim(sa, v0=12.0, stop_at=55.0)
    for _ in range(400):
      rec = sim.step()
      assert rec['entries'] == [] and rec['state'] == State.OFF
    assert not sa._faulted

  def test_a_broken_car_params_is_inert(self):
    sa = StopAssist(None, FakeEvents(), params=FakeParams())
    sim = Sim(sa, v0=12.0, stop_at=55.0)
    for _ in range(50):
      assert sim.step()['entries'] == []

  def test_constructor_reads_nothing(self):
    class Raising:
      def __getattr__(self, name):
        raise AssertionError("touched in the constructor")
    StopAssist(NS(openpilotLongitudinalControl=True), Raising(), params=Raising())


class TestDropoutNearTheStop(_Base):
  """Over the last seconds of every stop the model's action eases toward 0 by itself while it still brakes. A short
  plan dropout there is not a green unless the model has (nearly) stopped braking: the stop stays ours. (As built,
  a 0.1 m/s^2 rise during a 5-6 frame dropout ramped out, and the car then stopped where stock would.)"""

  def _drive_to(self, where):
    self.sim = Sim(self.sa, v0=11.0, stop_at=60.0)
    if where == State.APPROACH:
      self.run_until(lambda r: r['state'] == State.APPROACH and r['v'] < 8.0)
    else:
      self.run_until(lambda r: r['state'] == State.FINAL and r['v'] <= 2.6)
      assert self.sim.v >= C.LOW_V_GO  # the dropout rules apply here, not the low-speed go

  def test_easing_action_during_a_short_dropout_keeps_the_stop(self):
    for rate in (0.24, 0.5, 0.64):  # the logged end-of-stop easing is 0.46-0.64 m/s^3
      self.setUp()
      self._drive_to(State.FINAL)
      a = self.sim.records[-1]['a_model']
      assert a < C.DROP_GREEN_ACCEL - 0.1, a
      for _ in range(6):  # 0.3 s without a planned stop; the action rises 0.07-0.19 m/s^2 but still brakes
        a += rate * 0.05
        self.sim.plan_override = no_stop_plan(self.sim.v)
        self.sim.a_model_override = a
        rec = self.step()
        assert rec['state'] == State.FINAL and rec['entries'], (rate, rec['state'], a)
        assert rec['entries'][0][0] >= a - C.DROP_CLAMP - EPS  # the dropout clamp still applies
      self.sim.plan_override = self.sim.a_model_override = None
      rec = self.run_until(lambda r: r['state'] != State.FINAL, 400)
      assert rec['state'] == State.HOLD, (rate, rec['state'])
      target = 60.0 - 3 * C.FT_TO_M
      assert target - 1.0 <= self.sim.x <= target + 0.3, (rate, self.sim.x, target)

  def test_a_real_green_is_still_caught_within_a_few_frames(self):
    for where in (State.APPROACH, State.FINAL):
      self.setUp()
      self._drive_to(where)
      a = self.sim.records[-1]['a_model']
      for k in range(1, 30):
        a = SMOOTH * 0.8 + (1.0 - SMOOTH) * a  # the raw action jumps to +0.8
        self.sim.plan_override = no_stop_plan(self.sim.v)
        self.sim.a_model_override = a
        rec = self.step()
        assert rec['entries'] == [] or rec['entries'][0][0] >= a - C.DROP_CLAMP - EPS, (where, k)
        if rec['state'] == State.RAMP_OUT:
          break
      assert rec['state'] == State.RAMP_OUT and rec['reason'] == Reason.GREEN and k <= 4, (where, k, rec['state'])


class TestInvariantsRandom(unittest.TestCase):
  def test_random_frames_never_raise_and_entries_are_valid(self):
    rng = random.Random(1126)
    for _episode in range(30):
      sa, ev, params = new_sa(lights=rng.random() < 0.8, offset_ft=rng.choice([0, 3, 10]))
      t = 10 ** 9
      v = rng.uniform(0.0, 22.0)
      for _ in range(200):
        t += rng.choice([DT_NS, DT_NS, DT_NS, DT_NS + 7_000_000, 40_000_000, 300_000_000])
        kind = rng.random()
        if kind < 0.5:
          x, vv = plan_stop(max(v, 0.1), rng.uniform(1.0, 120.0))
        elif kind < 0.8:
          x, vv = plan_cruise(v)
        else:
          x, vv = plan_standstill_go(rng.uniform(0.0, 40.0))
        a_model = rng.uniform(-3.0, 1.5)
        stop = v < 0.3 and a_model < 0.1
        lead = (rng.uniform(1.0, 80.0), rng.uniform(-5.0, 5.0)) if rng.random() < 0.2 else None
        sm = make_sm(t, v, x, vv, a_model, stop, big=rng.random() < 0.02, standstill=v < 1e-3,
                     gas=rng.random() < 0.03, brake=rng.random() < 0.03, long_active=rng.random() < 0.97,
                     enabled=rng.random() < 0.99, force_decel=rng.random() < 0.02, lead=lead)
        cands = stock_candidates(v, a_model, stop, mpc_accel=rng.uniform(-3.5, 2.0), cruise_accel=rng.uniform(-0.5, 2.0),
                                 with_e2e=rng.random() < 0.97)
        out = sa.update(sm, cands)
        assert isinstance(out, list) and len(out) <= 1
        for a, src, s in out:
          assert type(a) is float and type(src) is int and type(s) is bool
          assert src == SOURCE_E2E and -C.A_CAP - EPS <= a <= C.A_MAX + EPS
        if rng.random() < 0.01:
          params.d["StopAssistLights"] = not params.d["StopAssistLights"]
        v = max(0.0, v + rng.uniform(-0.15, 0.1))
      assert not sa._faulted, "random but well-formed inputs must not fault"


if __name__ == "__main__":
  unittest.main()
