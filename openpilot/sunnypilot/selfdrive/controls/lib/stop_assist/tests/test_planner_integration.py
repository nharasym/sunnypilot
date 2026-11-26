"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(stop-assist): the REAL stock LongitudinalPlanner and sunnypilot LongitudinalPlannerSP with only the acados
# solver replaced: a fake long_mpc module sits in sys.modules for the import and is taken out again afterwards, so a
# real planner import later in the same process (the on-device shim) is unaffected. This is the r20 crash class
# (plannerd crash-looped on an enum mismatch inside update()): update() and publish() run here in every state.
# Stop Assist always gets FakeParams, never the device's real params. unittest.TestCase, setUp only.
import importlib
import random
import sys
import types
import unittest

import numpy as np

import openpilot.cereal.messaging as messaging
from openpilot.cereal import custom, log
from opendbc.car.structs import car
from openpilot.selfdrive.modeld.constants import index_function
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import SOURCE_E2E
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.stop_assist import State
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.tests.sim_helpers import (
  FakeParams, T_IDXS, plan_cruise, plan_parked, plan_standstill_go, plan_stop, quiet_faults,
)

LONG_MPC = "openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc"
STOCK = "openpilot.selfdrive.controls.lib.longitudinal_planner"
Source = log.LongitudinalPlan.LongitudinalPlanSource
EVENT = int(custom.OnroadEventSP.EventName.stopAssist)
SERVICES = ("carControl", "carState", "controlsState", "radarState", "modelV2", "selfdriveState", "liveMapDataSP",
            "carStateSP", "selfdriveStateSP", "gpsLocationExternal", "gpsLocation")
DT = 0.05


def _fake_long_mpc():
  lm = types.ModuleType(LONG_MPC)
  lm.LongitudinalPlanSource = Source
  lm.T_IDXS = np.array([index_function(i, max_val=10.0, max_idx=12) for i in range(13)])

  class FakeMpc:
    """No lead: heads for the cruise speed (a +MPC_ACCEL plan, so its candidate is +MPC_ACCEL); like the real solver
    it resets its source to lead0 on every update."""
    MPC_ACCEL = 1.0

    def __init__(self, dt=DT):
      n = len(lm.T_IDXS)
      self.source = Source.cruise
      self.v_solution, self.a_solution, self.j_solution = np.zeros(n), np.zeros(n), np.zeros(n - 1)
      self.crash_cnt = 0
      self.solve_time = 0.0
      self._v = 0.0

    def set_weights(self, *args, **kwargs):
      pass

    def set_cur_state(self, v, a):
      self._v = float(v)

    def update(self, radarstate, personality=None):
      self.source = Source.lead0
      self.a_solution[:] = self.MPC_ACCEL
      self.v_solution[:] = self._v + self.MPC_ACCEL * lm.T_IDXS

  lm.LongitudinalMpc = FakeMpc
  return lm


def load_stock_planner_module():
  """The real stock planner module bound to the fake solver; sys.modules and the parent attribute are restored."""
  parent = importlib.import_module(STOCK.rsplit('.', 1)[0])
  saved = {name: sys.modules.get(name) for name in (LONG_MPC, STOCK)}
  had_attr, old_attr = hasattr(parent, 'longitudinal_planner'), getattr(parent, 'longitudinal_planner', None)
  try:
    sys.modules[LONG_MPC] = _fake_long_mpc()
    sys.modules.pop(STOCK, None)
    return importlib.import_module(STOCK)
  finally:
    for name, mod in saved.items():
      if mod is None:
        sys.modules.pop(name, None)
      else:
        sys.modules[name] = mod
    if had_attr:
      parent.longitudinal_planner = old_attr
    elif hasattr(parent, 'longitudinal_planner'):
      del parent.longitudinal_planner


LP = load_stock_planner_module()


class FakeSM(dict):
  def __init__(self):
    super().__init__()
    self.logMonoTime, self.updated, self.alive, self.valid, self.freq_ok = {}, {}, {}, {}, {}
    self.recv_frame, self.recv_time, self.seen = {}, {}, {}
    self.frame = 0
    for s in SERVICES:
      self[s] = getattr(messaging.new_message(s), s)
      self.logMonoTime[s] = 0
      self.updated[s] = self.alive[s] = self.valid[s] = self.freq_ok[s] = self.seen[s] = True
      self.recv_frame[s] = 1
      self.recv_time[s] = 0.0

  def all_checks(self, service_list=None):
    return True

  def all_alive(self, service_list=None):
    return True

  def all_valid(self, service_list=None):
    return True


class FakePM:
  def __init__(self):
    self.sent = {}

  def send(self, name, msg):
    self.sent[name] = msg.to_bytes()

  def plan(self):
    with log.Event.from_bytes(self.sent['longitudinalPlan']) as ev:
      p = ev.longitudinalPlan
      return float(p.aTarget), bool(p.shouldStop), int(p.longitudinalPlanSource.raw), list(p.speeds), list(p.accels)

  def plan_sp(self):
    with log.Event.from_bytes(self.sent['longitudinalPlanSP']) as ev:
      sp = ev.longitudinalPlanSP
      sa = sp.stopAssist
      events = [(int(e.name.raw), bool(e.warning), bool(e.noEntry)) for e in sp.events]
      return {'state': sa.state.raw, 'active': sa.active, 'aCandidate': sa.aCandidate, 'shouldStop': sa.shouldStop,
              'events': events}


def make_frame(t_ns, v_ego, plan, a_model, model_stop, *, gas=False, brake=False, engaged=True, lead=None, big=False,
               v_cruise_kph=60.0) -> FakeSM:
  sm = FakeSM()
  sm.logMonoTime['modelV2'] = int(t_ns)
  cs = messaging.new_message('carState').carState
  cs.vEgo, cs.aEgo, cs.vCruise, cs.vCruiseCluster = float(v_ego), 0.0, v_cruise_kph, v_cruise_kph
  cs.standstill, cs.gasPressed, cs.brakePressed = bool(v_ego < 1e-3), bool(gas), bool(brake)
  sm['carState'] = cs
  active = engaged and not gas and not brake
  cc = messaging.new_message('carControl').carControl
  cc.enabled, cc.longActive = bool(engaged), bool(active)
  sm['carControl'] = cc
  ss = messaging.new_message('selfdriveState').selfdriveState
  ss.enabled = bool(engaged)
  sm['selfdriveState'] = ss
  ctl = messaging.new_message('controlsState').controlsState
  ctl.longControlState = 'pid' if active else 'off'
  sm['controlsState'] = ctl
  md = messaging.new_message('modelV2').modelV2
  md.position.x = [float(e) for e in plan[0]]
  md.velocity.x = [float(e) for e in plan[1]]
  md.orientationRate.z = [1e-4] * len(T_IDXS)
  md.action.desiredAcceleration = float(a_model)
  md.action.shouldStop = bool(model_stop)
  md.timestampEof = max(int(t_ns) - 55_000_000, 0)
  md.big = bool(big)
  sm['modelV2'] = md
  rs = messaging.new_message('radarState').radarState
  if lead is not None:
    rs.leadOne.present, rs.leadOne.dRel, rs.leadOne.vRel = True, float(lead[0]), float(lead[1])
  sm['radarState'] = rs
  return sm


def new_planner(lights: bool, offset_ft=3):
  CP = car.CarParams.new_message(openpilotLongitudinalControl=True, brand="toyota", longitudinalActuatorDelay=0.15)
  planner = LP.LongitudinalPlanner(CP, custom.CarParamsSP.new_message())
  planner.stop_assist.params = FakeParams(lights, offset_ft)  # before the first update: never the real params
  return planner


def spy(planner) -> dict:
  """Record the stock candidates (a copy, before our entry is appended) and our entries, every frame."""
  rec = {'candidates': [], 'entries': []}
  real = planner.stop_assist.update

  def update(sm, candidates):
    rec['candidates'] = list(candidates)
    out = real(sm, candidates)
    rec['entries'] = list(out)
    return out
  planner.stop_assist.update = update
  return rec


class Drive:
  """A light ahead at stop_at; the car follows the planner's output (the stopping state below 0.3 m/s)."""

  def __init__(self, planner, v0=12.0, stop_at=50.0):
    self.p, self.pm, self.spy = planner, FakePM(), spy(planner)
    self.t, self.x, self.v, self.stop_at = 10 ** 9, 0.0, float(v0), stop_at
    self.green, self.lead, self.gas = False, None, False
    self.log: list[dict] = []

  def step(self):
    self.t += int(DT * 1e9)
    if self.green or self.stop_at is None:
      plan, a_model = (plan_standstill_go() if self.v < 0.3 else plan_cruise(self.v)), 0.8
    else:
      d = self.stop_at - (self.x - 0.055 * self.v)
      plan, a_model = (plan_stop(self.v, d), -0.8 * self.v ** 2 / (2 * d)) if self.v > 0.05 and d > 0.05 else (plan_parked(), -0.3)
    model_stop = self.v < 0.3 and a_model < 0.1
    sm = make_frame(self.t, self.v, plan, a_model, model_stop, lead=self.lead, gas=self.gas)
    self.p.update(sm)
    self.p.publish(sm, self.pm)
    a = -2.0 if (self.p.output_should_stop and self.v < 0.3) else float(self.p.output_a_target)
    v_new = max(self.v + a * DT, 0.0)
    self.x += 0.5 * (self.v + v_new) * DT
    self.v = v_new
    rec = {'state': self.p.stop_assist.state, 'entries': self.spy['entries'], 'candidates': self.spy['candidates'],
           'plan': self.pm.plan(), 'sp': self.pm.plan_sp()}
    self.log.append(rec)
    return rec

  def until(self, state, max_frames=1500):
    for _ in range(max_frames):
      rec = self.step()
      if rec['state'] == state:
        return rec
    raise AssertionError(f"state {state} not reached")


def _random_frames(seed: int, n: int):
  rng = random.Random(seed)
  t, v = 10 ** 9, 10.0
  for _ in range(n):
    t += int(DT * 1e9)
    kind = rng.random()
    plan = plan_stop(max(v, 0.1), rng.uniform(2.0, 90.0)) if kind < 0.5 else plan_cruise(v) if kind < 0.8 else plan_standstill_go()
    a_model = rng.uniform(-2.5, 1.0)
    lead = (rng.uniform(5.0, 80.0), rng.uniform(-4.0, 4.0)) if rng.random() < 0.2 else None
    yield make_frame(t, v, plan, a_model, v < 0.3 and a_model < 0.1, gas=rng.random() < 0.03, brake=rng.random() < 0.02,
                     engaged=rng.random() < 0.95, lead=lead, big=rng.random() < 0.02, v_cruise_kph=rng.choice([30.0, 60.0, 90.0]))
    v = min(max(0.0, v + rng.uniform(-0.3, 0.2)), 25.0)


def _outputs(planner, pm):
  return (float(planner.output_a_target), bool(planner.output_should_stop), int(planner.mpc.source)), pm.plan()


class TestPlannerIntegration(unittest.TestCase):
  def test_setting_off_is_bit_identical_to_no_hook(self):
    off, ref = new_planner(lights=False), new_planner(lights=False)
    ref.stop_assist.update = lambda sm, candidates: []
    pm_off, pm_ref = FakePM(), FakePM()
    for sm in _random_frames(1126, 600):
      for p, pm in ((off, pm_off), (ref, pm_ref)):
        p.update(sm)
        p.publish(sm, pm)
      assert _outputs(off, pm_off) == _outputs(ref, pm_ref)
    assert off.stop_assist.state == State.OFF and not off.stop_assist._faulted

  def test_setting_on_brakes_for_the_planned_stop_with_the_e2e_source(self):
    drive = Drive(new_planner(lights=True))
    drive.until(State.APPROACH)
    won = 0
    for _ in range(60):
      rec = drive.step()
      if rec['state'] not in (State.APPROACH, State.FINAL):
        break
      assert len(rec['entries']) == 1
      a, src, stop = rec['entries'][0]
      assert type(a) is float and type(src) is int and type(stop) is bool and src == SOURCE_E2E
      e2e = min(c[0] for c in rec['candidates'] if c[1] == Source.e2e)
      a_target, should_stop, source, _, _ = rec['plan']
      assert a_target <= e2e + 1e-6
      assert rec['sp']['state'] in (State.APPROACH, State.FINAL) and rec['sp']['active']
      assert (EVENT, True, False) in rec['sp']['events']
      if a <= min(c[0] for c in rec['candidates']):
        won += 1
        assert source == SOURCE_E2E and abs(a_target - a) < 1e-5
    assert won > 20, won

  def test_update_and_publish_in_every_state(self):
    drive = Drive(new_planner(lights=True))
    hold = drive.until(State.HOLD)
    assert hold['entries'] == [(0.0, SOURCE_E2E, True)] and hold['plan'][1]
    assert drive.v == 0.0 and drive.x < drive.stop_at
    for _ in range(30):
      rec = drive.step()
      assert rec['state'] == State.HOLD and rec['plan'][1]  # shouldStop held
    drive.green = True
    drive.until(State.RELEASED, 60)
    drive.until(State.IDLE, 600)
    drive.green, drive.stop_at = False, drive.x + 5 * drive.v + 10.0
    drive.until(State.APPROACH, 200)
    drive.lead = (8.0, 0.0)
    rec = drive.step()
    assert rec['state'] == State.RAMP_OUT and rec['sp']['state'] == State.RAMP_OUT
    seen = {r['sp']['state'] for r in drive.log}
    assert {State.CONFIRM, State.APPROACH, State.FINAL, State.HOLD, State.RELEASED, State.IDLE, State.RAMP_OUT} <= seen, seen

  def test_a_raising_stop_assist_leaves_the_outputs_stock(self):
    broken, ref = new_planner(lights=True), new_planner(lights=True)
    ref.stop_assist.update = lambda sm, candidates: []

    def boom(*args, **kwargs):
      raise RuntimeError("injected")
    broken.stop_assist._read_frame = boom
    pm_b, pm_r = FakePM(), FakePM()
    with quiet_faults() as log:
      for sm in _random_frames(7, 200):
        for p, pm in ((broken, pm_b), (ref, pm_r)):
          p.update(sm)
          p.publish(sm, pm)
        assert _outputs(broken, pm_b) == _outputs(ref, pm_r)
    assert broken.stop_assist.state == State.FAULTED and log.exceptions == 1
    assert pm_b.plan_sp()['state'] == State.FAULTED

  def test_a_broken_fill_still_publishes(self):
    drive = Drive(new_planner(lights=True))
    drive.until(State.APPROACH)
    drive.p.stop_assist._out = [(object(), SOURCE_E2E, True)]  # float(object()) raises inside fill()
    with quiet_faults() as log:
      drive.p.publish_longitudinal_plan_sp(FakeSM(), drive.pm)
      assert drive.pm.plan_sp()['state'] == State.FAULTED
      rec = drive.step()
    assert rec['entries'] == [] and rec['state'] == State.FAULTED and log.exceptions == 1

  def test_candidates_without_e2e_produce_no_entry(self):
    drive = Drive(new_planner(lights=True))
    drive.until(State.APPROACH)
    # e.g. an upstream sync that offers the model's candidate only in Experimental mode: never brake without it
    frame = make_frame(drive.t + int(DT * 1e9), drive.v, plan_stop(drive.v, 30.0), -1.0, False)
    no_model = [(0.0, Source.lead0, False), (0.3, Source.cruise, False)]
    assert drive.p.stop_assist.update(frame, no_model) == []
    assert drive.p.stop_assist.state == State.IDLE
