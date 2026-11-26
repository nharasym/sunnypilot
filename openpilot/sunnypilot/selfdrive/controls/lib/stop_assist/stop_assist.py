"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

HL-FEAT(stop-assist): Stop Assist phase 1, "lights and crosswalks".

With openpilot longitudinal and no car ahead, the model's action is the only thing that brakes for a red light, and
on the owner's logs the car came to rest beyond the model's own planned stop point. When the model plans a stop and
is already braking, Stop Assist locks that point (where the planned speed is lowest), brakes steadily to stop there
minus the user offset, and holds the stop until the model asks to go. It does not make braking start earlier.

It is one extra candidate for the stock planner (openpilot/selfdrive/controls/lib/longitudinal_planner.py), which
takes the minimum accel over its candidates and ORs their stop flags, so an entry can only add braking or hold a
stop: it can never make the car speed up or pull away. With the setting off it returns [] and the planner is stock.

Contract: update(sm, candidates) returns [] or [(float accel, SOURCE_E2E, bool should_stop)]; fill(struct) writes
LongitudinalPlanSP.stopAssist. Neither raises. Any exception turns the feature off for the rest of the process; a
stop being held then stays held until the accelerator, the brake or a disengage.

States: OFF -> IDLE -> CONFIRM -> APPROACH -> FINAL -> HOLD -> RELEASED -> IDLE. RAMP_OUT eases an entry out
instead of deleting it; only the accelerator, the brake, a disengage or a model gap end an entry at once (as stock).
Never import long_mpc, either longitudinal planner or longcontrol here: the Mac cannot import the compiled MPC.
"""
import math

from openpilot.cereal import custom, log
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import constants as C
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.stop_point import (
  StopPointTracker, blocking_lead, median, model_go, offset_m_from_ft, planned_stop, points_agree, required_decel,
)

# The stock planner's e2e source as a plain int. The package never touches long_mpc's enum object: in the sunnypilot
# planner the same name is a different enum (the r20 plannerd crash loop).
SOURCE_E2E = int(log.LongitudinalPlan.LongitudinalPlanSource.e2e)

PARAMS_EVERY_FRAMES = int(PARAMS_UPDATE_PERIOD / DT_MDL)
FAULT_ALERT_FRAMES = int(round(C.FAULT_ALERT_S / DT_MDL))


def _ns(seconds: float) -> int:
  return int(round(seconds * 1e9))


def _clip(a: float, lo: float, hi: float) -> float:
  return min(max(a, lo), hi)


class State:
  """Plain ints equal to custom.LongitudinalPlanSP.StopAssist.State (pinned by test_telemetry_and_events)."""
  OFF = 0
  IDLE = 1
  CONFIRM = 2
  APPROACH = 3
  FINAL = 4
  HOLD = 5
  RELEASED = 6
  RAMP_OUT = 7
  FAULTED = 8


class Reason:
  """Plain ints equal to custom.LongitudinalPlanSP.StopAssist.Reason (pinned by test_telemetry_and_events)."""
  NONE = 0
  GREEN = 1
  STOP_LOST = 2
  LEAD = 3
  GAS = 4
  BRAKE = 5
  DISENGAGED = 6
  SETTING_OFF = 7
  MODEL_GAP = 8
  MODEL_SWITCH = 9
  NEW_STOP = 10
  TOO_LATE = 11
  FAULT = 12
  NO_MODEL_CANDIDATE = 13


class _Frame:
  """One model frame's inputs as plain Python values."""
  __slots__ = ('t', 'dt', 'gap', 'v', 's', 'standstill', 'gas', 'brake', 'long_active', 'enabled', 'force_decel',
               'a_model', 'model_stop', 'big', 'switched', 'lead_present', 'lead_drel', 'lead_vrel', 'd_raw', 's_raw')


class StopAssist:
  def __init__(self, CP, events_sp, params=None):
    """Reads nothing and cannot raise. params: anything with get_bool()/get() (tests); None -> Params(), made on
    the first settings read inside update()'s try."""
    self.events_sp = events_sp
    self.params = params
    try:
      self._inert = not bool(CP.openpilotLongitudinalControl)
    except Exception:
      self._inert = True

    self._frame = -1
    self._faulted = False
    self._latch = False
    self._fault_frames = 0

    self._setting_on = False
    self._offset_setting_m = C.OFFSET_DEFAULT_FT * C.FT_TO_M
    self._offset_m = self._offset_setting_m
    self._state = State.OFF
    self._reason = Reason.NONE

    # odometer, model timing, arming block
    self._s = 0.0
    self._t_prev: int | None = None
    self._v_prev = 0.0
    self._t_last_stop: int | None = None
    self._big_prev: bool | None = None
    self._arm_block_until = 0

    # episode
    self._tracker = StopPointTracker()
    self._window: list[float | None] = []
    self._confirm_t0 = 0
    self._a_cmd = 0.0
    self._new_stop_t0: int | None = None
    self._drop_a0: float | None = None
    self._go_t0: int | None = None
    self._hold_t0 = 0
    self._ramp_clamp = False
    self._released_s0 = 0.0
    self._released_t0 = 0

    # telemetry of the last frame
    self._out: list = []
    self._a_need = 0.0
    self._remaining = 0.0
    self._plan_stop_m = -1.0
    self._a_model = 0.0
    self._big = False
    self._hold_s = 0.0

  @property
  def state(self) -> int:
    return State.FAULTED if self._faulted else self._state

  @property
  def reason(self) -> int:
    return self._reason

  # ---------------------------------------------------------------- planner interface

  def update(self, sm, candidates) -> list:
    """Once per model frame, right after the stock planner builds its candidates (list of (accel, source, stop)).
    Returns [] or one entry (float, SOURCE_E2E, bool). Never raises."""
    try:
      if not self._faulted:
        try:
          return self._step(sm, candidates)
        except Exception:
          self._fault("update")
      return self._faulted_update(sm)
    except Exception:
      return []

  def fill(self, sa) -> None:
    """Write LongitudinalPlanSP.stopAssist (every field, every frame). Never raises: every value is cast (numpy
    scalars raise inside pycapnp), and an error turns the feature off and writes state=faulted."""
    try:
      out = self._out[0] if self._out else None
      sa.state = int(State.FAULTED if self._faulted else self._state)
      sa.enabled = bool(self._setting_on)
      sa.active = out is not None
      sa.offsetM = float(self._offset_m)
      sa.remainingM = float(self._remaining)
      sa.planStopM = float(self._plan_stop_m)
      sa.aCandidate = float(out[0]) if out is not None else 0.0
      sa.aNeed = float(self._a_need)
      sa.shouldStop = bool(out[2]) if out is not None else False
      sa.modelAccel = float(self._a_model)
      sa.holdS = float(self._hold_s)
      sa.reason = int(self._reason)
      sa.modelBig = bool(self._big)
    except Exception:
      self._fault("fill")
      try:
        sa.state = int(State.FAULTED)
      except Exception:
        pass

  # ---------------------------------------------------------------- one frame

  def _step(self, sm, candidates) -> list:
    self._frame += 1
    self._out = []
    self._a_need = 0.0
    if self._inert:
      self._state = State.OFF
      return []
    if self._frame % PARAMS_EVERY_FRAMES == 0:
      self._read_settings()

    f = self._read_frame(sm)
    out = self._run(f, candidates)

    self._out = out
    if self._state in (State.APPROACH, State.FINAL):
      self._add_event()
    self._remaining = self._tracker.target - f.s if self._tracker.target is not None else 0.0
    self._hold_s = (f.t - self._hold_t0) / 1e9 if self._state == State.HOLD else 0.0
    return out

  def _read_settings(self) -> None:
    if self.params is None:
      self.params = Params()
    self._setting_on = bool(self.params.get_bool("StopAssistLights"))
    self._offset_setting_m = offset_m_from_ft(self.params.get("StopAssistLightsOffsetFt", return_default=True))

  def _read_frame(self, sm) -> _Frame:
    f = _Frame()
    md = sm['modelV2']
    cs = sm['carState']
    lead = sm['radarState'].leadOne
    f.t = int(sm.logMonoTime['modelV2'])
    f.v = float(cs.vEgo)
    f.standstill = bool(cs.standstill)
    f.gas = bool(cs.gasPressed)
    f.brake = bool(cs.brakePressed)
    f.long_active = bool(sm['carControl'].longActive)
    f.enabled = bool(sm['selfdriveState'].enabled)
    f.force_decel = bool(sm['controlsState'].forceDecel)
    f.a_model = float(md.action.desiredAcceleration)
    f.model_stop = bool(md.action.shouldStop)
    f.big = bool(md.big)
    f.lead_present = bool(lead.present)
    f.lead_drel = float(lead.dRel)
    f.lead_vrel = float(lead.vRel)

    # model-frame timing; the odometer integrates with the real dt between model frames
    f.dt = None if self._t_prev is None else (f.t - self._t_prev) / 1e9
    f.gap = f.dt is not None and not (0.0 < f.dt <= C.MODEL_GAP_S)
    if f.dt is not None and not f.gap and math.isfinite(f.v) and math.isfinite(self._v_prev):
      self._s += 0.5 * (f.v + self._v_prev) * f.dt
    self._t_prev, self._v_prev = f.t, f.v
    f.s = self._s

    # this frame's planned stop, registered in road coordinates at the camera time
    f.d_raw = planned_stop(md.position.x, md.velocity.x)
    f.s_raw = None
    if f.d_raw is not None:
      age = _clip((f.t - int(md.timestampEof)) / 1e9, 0.0, C.EOF_AGE_MAX_S)
      s_raw = f.s - f.v * age + f.d_raw
      if math.isfinite(s_raw):
        f.s_raw = s_raw
        self._t_last_stop = f.t
      else:
        f.d_raw = None

    # a switch between the big and the small model blocks arming for a while
    f.switched = self._big_prev is not None and f.big != self._big_prev
    self._big_prev = f.big
    if f.switched:
      self._arm_block_until = f.t + _ns(C.MODEL_SWITCH_BLOCK_S)

    self._a_model = f.a_model
    self._big = f.big
    self._plan_stop_m = f.d_raw if f.d_raw is not None else -1.0
    return f

  def _run(self, f: _Frame, candidates) -> list:
    # 1. never brake in a mode where the planner ignores the model (e.g. upstream re-gating it behind Experimental)
    e2e = [float(c[0]) for c in candidates if c[1] == SOURCE_E2E]
    if not e2e:
      return self._end(State.IDLE, Reason.NO_MODEL_CANDIDATE)
    e2e_accel = min(e2e)

    # 2. the accelerator overrides; no re-arm until RELEASED clears
    if f.gas:
      return self._release(f, Reason.GAS) if self._state != State.OFF else []

    # 3. the brake or a disengage: the stock planner takes over at once
    if f.brake or not f.long_active or not f.enabled:
      if self._state == State.RELEASED and not f.brake and f.enabled:
        return []  # longActive clears 10-20 ms after the accelerator is lifted; that lag must not end RELEASED
      return self._end(State.IDLE, Reason.BRAKE if f.brake else Reason.DISENGAGED)

    # 4. a gap in the model stream
    if f.gap:
      return self._end(State.IDLE, Reason.MODEL_GAP)

    # 5. big <-> small model switch (the arming block is set in _read_frame); a hold is kept
    if f.switched:
      if self._state == State.CONFIRM:
        return self._end(State.IDLE, Reason.MODEL_SWITCH)
      if self._state in (State.APPROACH, State.FINAL):
        self._begin_ramp(Reason.MODEL_SWITCH)

    # settings: off ramps a braking entry out and keeps a hold to its normal end; on and the offset apply from IDLE/OFF
    if not self._setting_on:
      if self._state in (State.APPROACH, State.FINAL):
        self._begin_ramp(Reason.SETTING_OFF)
      elif self._state in (State.OFF, State.IDLE, State.CONFIRM, State.RELEASED):
        return self._end(State.OFF, Reason.SETTING_OFF)  # CONFIRM returns no entry, so there is nothing to ramp
    elif self._state == State.OFF:
      self._state = State.IDLE
      self._reason = Reason.NONE
    if self._state in (State.OFF, State.IDLE):
      self._offset_m = self._offset_setting_m

    if self._state == State.IDLE:
      return self._idle(f)
    if self._state == State.CONFIRM:
      return self._confirm(f, candidates, e2e_accel)
    if self._state == State.APPROACH:
      return self._approach(f, e2e_accel)
    if self._state == State.FINAL:
      return self._final(f, e2e_accel)
    if self._state == State.HOLD:
      return self._hold(f)
    if self._state == State.RELEASED:
      return self._released(f)
    if self._state == State.RAMP_OUT:
      return self._ramp_out(f, e2e_accel)
    return []

  # ---------------------------------------------------------------- states

  def _arm_check(self, f: _Frame) -> tuple[bool, int]:
    if not (C.ARM_V_MIN <= f.v <= C.ARM_V_MAX) or f.force_decel or f.t < self._arm_block_until:
      return False, Reason.NONE
    if f.d_raw is None or not f.a_model <= C.ARM_MODEL_ACCEL:
      return False, Reason.NONE
    remaining = f.d_raw - self._offset_m
    if blocking_lead(f.lead_present, f.lead_drel, f.lead_vrel, remaining):
      return False, Reason.LEAD
    if not required_decel(f.v, remaining) <= C.MAX_ARM_NEED:
      return False, Reason.TOO_LATE
    return True, Reason.NONE

  def _idle(self, f: _Frame) -> list:
    ok, why = self._arm_check(f)
    if not ok:
      if why == Reason.TOO_LATE:
        self._reason = Reason.TOO_LATE
      return []
    self._state = State.CONFIRM
    self._reason = Reason.NONE
    self._confirm_t0 = f.t
    self._window = [f.s_raw]
    return []

  def _confirm(self, f: _Frame, candidates, e2e_accel: float) -> list:
    self._window.append(f.s_raw)
    del self._window[:-C.CONFIRM_FRAMES]
    points = [p for p in self._window if p is not None]
    if not points:
      return self._end(State.IDLE, Reason.NONE)  # no planned stop in the whole window: nothing left to confirm
    # any frame where the speed, lead, forceDecel or need gates fail ends the confirmation
    if not (C.ARM_V_MIN <= f.v <= C.ARM_V_MAX) or f.force_decel:
      return self._end(State.IDLE, Reason.NONE)
    remaining = (f.d_raw if f.d_raw is not None else median(points) - f.s) - self._offset_m
    if blocking_lead(f.lead_present, f.lead_drel, f.lead_vrel, remaining):
      return self._end(State.IDLE, Reason.LEAD)
    if f.d_raw is not None and not required_decel(f.v, remaining) <= C.MAX_ARM_NEED:
      return self._end(State.IDLE, Reason.TOO_LATE)

    if f.t - self._confirm_t0 < _ns(C.CONFIRM_S) or len(points) < C.CONFIRM_MIN or not points_agree(points):
      return []
    if not self._arm_check(f)[0]:
      return []

    # confirmed: start from the planner's current output so the first entry is no step
    self._tracker.start(points, self._offset_m, f.s)
    self._a_cmd = _clip(min(float(c[0]) for c in candidates), -C.A_CAP, C.A_MAX)
    self._state = State.APPROACH
    return self._approach(f, e2e_accel, seeded=True)

  def _approach(self, f: _Frame, e2e_accel: float, seeded: bool = False) -> list:
    tr = self._tracker
    if blocking_lead(f.lead_present, f.lead_drel, f.lead_vrel, tr.target - f.s):
      return self._ramp(Reason.LEAD, f, e2e_accel)

    lost = f.s_raw is None
    if not lost:
      self._drop_a0 = None
      if f.s_raw - self._offset_m > tr.target + C.NEW_STOP_M:
        if self._new_stop_t0 is None:
          self._new_stop_t0 = f.t
        if f.t - self._new_stop_t0 >= _ns(C.NEW_STOP_S):
          return self._ramp(Reason.NEW_STOP, f, e2e_accel)
      else:
        self._new_stop_t0 = None
      if not seeded:
        tr.update(f.s_raw, f.s, f.dt)
    else:
      self._new_stop_t0 = None
      why = self._dropout_end(f)
      if why is not None:
        return self._ramp(why, f, e2e_accel, clamp=True)
      tr.update(None, f.s, f.dt)  # keeps the target; may record the anchor

    if tr.target - f.s <= C.FREEZE_M or f.v < C.FREEZE_V:
      self._state = State.FINAL
      tr.freeze()
    return self._law(f, clamp=lost)

  def _final(self, f: _Frame, e2e_accel: float) -> list:
    if f.standstill:
      self._state = State.HOLD
      self._hold_t0 = f.t
      self._go_t0 = None
      return self._hold(f)

    lost = False
    if f.v < C.LOW_V_GO:
      # the plan minimum is the car's own speed down here: green is the model's go, held
      self._drop_a0 = None
      if model_go(f.model_stop, f.a_model, f.v):
        if self._go_t0 is None:
          self._go_t0 = f.t
        if f.t - self._go_t0 >= _ns(C.LOW_V_GO_S):
          return self._ramp(Reason.GREEN, f, e2e_accel)
      else:
        self._go_t0 = None
    else:
      self._go_t0 = None
      if f.s_raw is None:
        why = self._dropout_end(f)
        if why is not None:
          return self._ramp(why, f, e2e_accel, clamp=True)
        lost = True
      else:
        self._drop_a0 = None
    return self._law(f, clamp=lost)

  def _hold(self, f: _Frame) -> list:
    if model_go(f.model_stop, f.a_model, f.v):
      if self._go_t0 is None:
        self._go_t0 = f.t
    else:
      self._go_t0 = None
    if f.t - self._hold_t0 >= _ns(C.HOLD_MIN_S) and self._go_t0 is not None and f.t - self._go_t0 >= _ns(C.GO_CONFIRM_S):
      return self._release(f, Reason.GREEN)
    return [self._entry(0.0, True)]

  def _released(self, f: _Frame) -> list:
    clear_since = self._released_t0 if self._t_last_stop is None else max(self._t_last_stop, self._released_t0)
    if (f.s - self._released_s0 >= C.RELEASED_TRAVEL_M and f.v >= C.RELEASED_V_MIN and
        f.t - clear_since >= _ns(C.RELEASED_CLEAR_S)):
      self._state = State.IDLE
    return []

  def _ramp_out(self, f: _Frame, e2e_accel: float) -> list:
    a = min(self._a_cmd + C.RAMP_OUT_JERK * self._dt(f), C.A_MAX)
    # the plan has lost the stop (a ramp that began in a dropout, or no planned stop at a speed where that test means
    # something): never harsher than the model's action - DROP_CLAMP. Below LOW_V_GO the plan is degenerate: no clamp.
    lost = self._ramp_clamp or (f.s_raw is None and f.v >= C.LOW_V_GO)
    if lost and f.a_model - C.DROP_CLAMP > a:
      a = f.a_model - C.DROP_CLAMP
    a = _clip(a, -C.A_CAP, C.A_MAX)
    self._a_cmd = a
    if a >= e2e_accel + C.RAMP_END_MARGIN or a >= C.RAMP_END_ACCEL:
      return self._end(State.IDLE, self._reason)
    return [self._entry(a, False)]

  # ---------------------------------------------------------------- helpers

  def _dropout_end(self, f: _Frame) -> int | None:
    """A frame with no planned stop during APPROACH/FINAL: green, stop lost, or keep (None)."""
    if self._drop_a0 is None:
      self._drop_a0 = f.a_model
    drop_s = (f.t - (self._t_last_stop if self._t_last_stop is not None else f.t)) / 1e9
    if (drop_s >= C.DROP_GREEN_S and f.a_model >= C.DROP_GREEN_ACCEL) or f.a_model - self._drop_a0 > C.DROP_RISE:
      return Reason.GREEN
    if drop_s > C.DROP_MAX_S:
      return Reason.STOP_LOST
    return None

  def _law(self, f: _Frame, clamp: bool) -> list:
    """Brake steadily to the target: a_law = -min(required_decel, A_CAP), slewed JERK_IN in / JERK_OUT out."""
    need = required_decel(f.v, self._tracker.target - f.s)
    a_law = -min(need, C.A_CAP)
    dt = self._dt(f)
    a = self._a_cmd
    if a_law < a:
      a = max(a_law, a - C.JERK_IN * dt)
    else:
      a = min(a_law, a + C.JERK_OUT * dt)
    if clamp and f.a_model - C.DROP_CLAMP > a:
      a = f.a_model - C.DROP_CLAMP  # the plan has lost the stop: never harsher than the model's action - DROP_CLAMP
    a = _clip(a, -C.A_CAP, C.A_MAX)
    self._a_cmd = a
    self._a_need = need
    return [self._entry(a, f.v < C.STOP_V and a < C.GO_ACCEL)]

  def _begin_ramp(self, reason: int, clamp: bool = False) -> None:
    """clamp: the ramp continues a plan dropout, so the dropout clamp stays on for the whole ramp."""
    self._state = State.RAMP_OUT
    self._reason = reason
    self._ramp_clamp = clamp

  def _ramp(self, reason: int, f: _Frame, e2e_accel: float, clamp: bool = False) -> list:
    self._begin_ramp(reason, clamp)
    return self._ramp_out(f, e2e_accel)

  def _release(self, f: _Frame, reason: int) -> list:
    if self._state != State.RELEASED:
      self._released_s0 = f.s
      self._released_t0 = f.t
    return self._end(State.RELEASED, reason)

  def _end(self, state: int, reason: int) -> list:
    """Leave the episode at once (no ramp); with the setting off every end is OFF."""
    if not self._setting_on:
      state, reason = State.OFF, Reason.SETTING_OFF
    self._state = state
    self._reason = reason
    self._tracker.reset()
    self._window = []
    self._a_cmd = 0.0
    self._new_stop_t0 = None
    self._drop_a0 = None
    self._go_t0 = None
    self._ramp_clamp = False
    return []

  @staticmethod
  def _dt(f: _Frame) -> float:
    return f.dt if f.dt is not None and f.dt > 0.0 else DT_MDL

  @staticmethod
  def _entry(accel: float, stop: bool) -> tuple[float, int, bool]:
    accel = float(accel)
    if not math.isfinite(accel):
      raise ValueError("stop_assist: non-finite accel")
    return (accel, SOURCE_E2E, bool(stop))

  def _add_event(self) -> None:
    event = int(custom.OnroadEventSP.EventName.stopAssist)
    if not self.events_sp.has(event):
      self.events_sp.add(event)

  # ---------------------------------------------------------------- faults

  def _fault(self, where: str) -> None:
    """Off for the rest of the process. Called from an except block, so the traceback is logged (once)."""
    if self._faulted:
      return
    self._faulted = True
    self._latch = self._state == State.HOLD
    self._fault_frames = 0
    self._reason = Reason.FAULT
    try:
      cloudlog.exception(f"stop_assist: {where} raised, Stop Assist is off for this drive")
    except Exception:
      pass

  def _faulted_update(self, sm) -> list:
    """After a fault: the 'Stop Assist Off' event for FAULT_ALERT_S, and a held stop stays held until the driver
    acts (accelerator, brake or a disengage). If even that cannot be read, nothing is returned."""
    out: list = []
    try:
      self._fault_frames += 1
      if self._fault_frames <= FAULT_ALERT_FRAMES:
        self._add_event()
    except Exception:
      pass
    if self._latch:
      try:
        cs = sm['carState']
        if bool(cs.gasPressed) or bool(cs.brakePressed) or not bool(sm['carControl'].longActive):
          self._latch = False
        else:
          out = [(0.0, SOURCE_E2E, True)]
      except Exception:
        self._latch = False
        out = []
    self._out = out
    return out
