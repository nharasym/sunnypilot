"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

HL-FEAT(stop-assist): test helpers. A fake SubMaster made of plain objects holding numpy values (as plannerd's
numpy-heavy code would hand them over), synthetic model plans, and a small closed-loop car that follows the stock
planner's rule (minimum accel over the candidates, OR of the stop flags), so whole stops can be driven.
No pytest, no fixtures: the on-device unittest shim imports this module too.
"""
from types import SimpleNamespace as NS

import numpy as np

from openpilot.selfdrive.modeld.constants import ModelConstants
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import SOURCE_E2E
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import constants as C

T_IDXS = np.array(ModelConstants.T_IDXS, dtype=np.float64)
DT_S = 0.05
DT_NS = 50_000_000
CRUISE = 0   # log.LongitudinalPlan.LongitudinalPlanSource.cruise
LEAD0 = 1    # ... .lead0
EPS = 1e-6


class FakeParams:
  """Stands in for Params. An unknown key raises, as the real Params does (UnknownKeyName)."""

  def __init__(self, lights: bool = True, offset_ft=3):
    self.d = {"StopAssistLights": lights, "StopAssistLightsOffsetFt": offset_ft}

  def get_bool(self, key, block=False):
    return bool(self.d[key])

  def get(self, key, block=False, return_default=False):
    return self.d[key]


class FakeEvents:
  def __init__(self):
    self.events: list[int] = []

  def add(self, event, static=False):
    self.events.append(int(event))

  def has(self, event):
    return int(event) in self.events

  def clear(self):
    self.events = []


class FakeSM(dict):
  def __init__(self):
    super().__init__()
    self.logMonoTime: dict[str, int] = {}


def plan_stop(v0: float, d_stop: float):
  """A constant-deceleration plan from v0 that stops d_stop metres ahead and stays there."""
  d_stop = max(float(d_stop), 0.05)
  a = v0 * v0 / (2.0 * d_stop)
  t_stop = v0 / a if a > 0.0 else 0.0
  t = T_IDXS
  v = np.where(t < t_stop, v0 - a * t, 0.0)
  x = np.where(t < t_stop, v0 * t - 0.5 * a * t * t, d_stop)
  return x.astype(np.float32), v.astype(np.float32)


def plan_cruise(v0: float):
  return (v0 * T_IDXS).astype(np.float32), np.full(len(T_IDXS), v0, dtype=np.float32)


def plan_standstill_go(x_end: float = 30.0):
  """The merged-plan regression shape: velocity.x[0..3] = 0 at a standstill, then a launch ending x_end ahead.
  Its minimum is the car's own ~0 speed, so a plan-minimum release rule would never fire on it."""
  t0 = T_IDXS[3]
  a = 2.0 * x_end / (T_IDXS[-1] - t0) ** 2
  tt = np.maximum(T_IDXS - t0, 0.0)
  return (0.5 * a * tt * tt).astype(np.float32), (a * tt).astype(np.float32)


def plan_parked():
  return np.zeros(len(T_IDXS), dtype=np.float32), np.zeros(len(T_IDXS), dtype=np.float32)


def make_sm(t_ns: int, v_ego: float, x, v, a_model: float, model_stop: bool, *, eof_age_s: float = 0.055,
            big: bool = False, standstill: bool = False, gas: bool = False, brake: bool = False,
            long_active: bool = True, enabled: bool = True, force_decel: bool = False, lead=None) -> FakeSM:
  """lead: None or (dRel, vRel). Every scalar is a numpy type on purpose."""
  sm = FakeSM()
  sm.logMonoTime['modelV2'] = int(t_ns)
  sm['modelV2'] = NS(position=NS(x=np.asarray(x, dtype=np.float32)), velocity=NS(x=np.asarray(v, dtype=np.float32)),
                     action=NS(desiredAcceleration=np.float32(a_model), shouldStop=np.bool_(model_stop)),
                     timestampEof=np.uint64(max(int(t_ns) - int(eof_age_s * 1e9), 0)), big=np.bool_(big))
  sm['carState'] = NS(vEgo=np.float32(v_ego), aEgo=np.float32(0.0), standstill=np.bool_(standstill),
                      gasPressed=np.bool_(gas), brakePressed=np.bool_(brake))
  sm['carControl'] = NS(longActive=np.bool_(long_active))
  sm['selfdriveState'] = NS(enabled=np.bool_(enabled))
  sm['controlsState'] = NS(forceDecel=np.bool_(force_decel))
  present = lead is not None
  d_rel, v_rel = lead if present else (0.0, 0.0)
  sm['radarState'] = NS(leadOne=NS(present=np.bool_(present), dRel=np.float32(d_rel), vRel=np.float32(v_rel)))
  return sm


def stock_candidates(v_ego: float, a_model: float, model_stop: bool, mpc_accel: float = 0.6, cruise_accel: float = 0.4,
                     with_e2e: bool = True) -> list:
  """The stock planner's three candidates with no lead: the MPC and cruise want to keep speed, the model brakes."""
  def stop(a):
    return bool(v_ego < C.STOP_V and a < C.GO_ACCEL)
  cands = [(np.float64(mpc_accel), LEAD0, stop(mpc_accel)), (float(cruise_accel), CRUISE, stop(cruise_accel))]
  if with_e2e:
    cands.append((float(a_model), SOURCE_E2E, bool(model_stop)))
  return cands


class Sim:
  """A light the model plans a stop for (world position stop_at), and a car that follows the planner's output
  through a first-order lag. The model sees the world at camera time (t - eof_age), so a registered stop point
  lands on stop_at exactly when the age correction is right. The model's own action brakes late on purpose:
  `model_scale` of what its plan needs (the owner's complaint)."""

  def __init__(self, sa, v0: float = 12.0, stop_at: float | None = 55.0, t0_ns: int = 10 ** 9, model_scale: float = 0.8,
               lag_s: float = 0.2):
    self.sa = sa
    self.t = int(t0_ns)
    self.x = 0.0
    self.v = float(v0)
    self.a = 0.0
    self.stop_at = stop_at
    self.green = False
    self.model_scale = model_scale
    self.lag_s = lag_s
    self.eof_age_s = 0.055
    # driver and system inputs
    self.gas = False
    self.brake = False
    self.long_active = True
    self.enabled = True
    self.force_decel = False
    self.big = False
    self.lead = None
    self.with_e2e = True
    self.mpc_accel = 0.6     # the stock MPC and cruise candidates (no lead: both want to keep speed)
    self.cruise_accel = 0.4
    # per-frame overrides of the model
    self.a_model_override = None
    self.model_stop_override = None
    self.plan_override = None
    self.records: list[dict] = []
    self.checker = EntryChecker()

  def model(self):
    """(x, v, a_model, model_stop) of this frame's model output."""
    if self.green or self.stop_at is None:
      x, v = plan_standstill_go() if self.v < C.STOP_V else plan_cruise(self.v)
      a_model = 0.8 if self.green else 0.0
    else:
      d = self.stop_at - (self.x - self.v * self.eof_age_s)  # distance seen at camera time
      if self.v > 0.05 and d > 0.05:
        x, v = plan_stop(self.v, d)
        a_model = -self.model_scale * self.v * self.v / (2.0 * d)
      else:
        x, v = plan_parked()
        a_model = -0.3
    if self.plan_override is not None:
      x, v = self.plan_override
    if self.a_model_override is not None:
      a_model = self.a_model_override
    model_stop = bool(self.v < C.STOP_V and a_model < C.GO_ACCEL)
    if self.model_stop_override is not None:
      model_stop = self.model_stop_override
    return x, v, a_model, model_stop

  def step(self, dt_ns: int = DT_NS) -> dict:
    self.t += int(dt_ns)
    v_in = self.v
    x, v, a_model, model_stop = self.model()
    sm = make_sm(self.t, v_in, x, v, a_model, model_stop, eof_age_s=self.eof_age_s, big=self.big,
                 standstill=v_in < 1e-3, gas=self.gas, brake=self.brake, long_active=self.long_active,
                 enabled=self.enabled, force_decel=self.force_decel, lead=self.lead)
    cands = stock_candidates(v_in, a_model, model_stop, mpc_accel=self.mpc_accel, cruise_accel=self.cruise_accel,
                             with_e2e=self.with_e2e)
    stock_min = min(float(c[0]) for c in cands)
    entries = self.sa.update(sm, cands)
    allc = cands + list(entries)
    out_a = min(float(c[0]) for c in allc)
    out_stop = any(bool(c[2]) for c in allc)

    # the car: longcontrol's stopping state below 0.3 m/s while any stop flag is up, else the output through a lag
    dt = dt_ns / 1e9
    a_cmd = -2.0 if (out_stop and v_in < C.STOP_V) else out_a
    if self.gas:
      a_cmd = 1.0
    self.a += (a_cmd - self.a) * min(dt / self.lag_s, 1.0)
    self.v = max(v_in + self.a * dt, 0.0)
    if self.v == 0.0 and self.a < 0.0:
      self.a = 0.0  # a stopped car does not roll back
    self.x += 0.5 * (v_in + self.v) * dt

    rec = {'t': self.t, 'state': self.sa.state, 'reason': self.sa.reason, 'entries': list(entries), 'stock_min': stock_min,
           'out_a': out_a, 'out_stop': out_stop, 'a_model': float(a_model), 'v': v_in, 'x': self.x, 'dt': dt}
    self.records.append(rec)
    self.checker.check(rec)
    return rec

  def run(self, n: int) -> list[dict]:
    return [self.step() for _ in range(n)]

  def run_until(self, pred, max_frames: int = 2000) -> dict:
    for _ in range(max_frames):
      rec = self.step()
      if pred(rec):
        return rec
    raise AssertionError("condition not reached")


class EntryChecker:
  """Invariants on every returned entry, checked frame by frame (no tearDown: the device shim never calls it):
  at most one entry; exact types (float, int, bool); source SOURCE_E2E; accel within [-A_CAP, A_MAX]. Between
  consecutive braking entries: never more than JERK_IN toward more braking, and at most JERK_OUT toward less braking
  unless the dropout clamp (never harsher than the model's action - DROP_CLAMP) lifted it. The first entry after
  none is no step below the stock output. Hold entries (0.0, stop) at a standstill are a stop request, not a slew."""

  def __init__(self):
    self.prev = None

  def check(self, rec: dict) -> None:
    entries = rec['entries']
    assert len(entries) <= 1, entries
    if not entries:
      self.prev = None
      return
    a, src, stop = entries[0]
    assert type(a) is float and type(src) is int and type(stop) is bool, (type(a), type(src), type(stop))
    assert src == SOURCE_E2E
    assert -C.A_CAP - EPS <= a <= C.A_MAX + EPS, a
    hold = stop and a == 0.0 and rec['v'] < C.STOP_V
    if self.prev is None:
      if not hold:
        assert a >= rec['stock_min'] - C.JERK_IN * rec['dt'] - EPS, ("step at engagement", a, rec['stock_min'])
    elif not hold and not self.prev['hold']:
      da = a - self.prev['a']
      assert da >= -C.JERK_IN * rec['dt'] - EPS, ("braking built faster than JERK_IN", da)
      clamped = abs(a - (rec['a_model'] - C.DROP_CLAMP)) <= 1e-4
      assert da <= C.JERK_OUT * rec['dt'] + EPS or clamped, ("braking released faster than JERK_OUT", da)
    self.prev = {'a': a, 'hold': hold}


def check_entries(records) -> None:
  checker = EntryChecker()
  for rec in records:
    checker.check(rec)
