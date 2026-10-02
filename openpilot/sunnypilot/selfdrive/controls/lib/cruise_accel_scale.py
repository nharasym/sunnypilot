"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(cruise-accel-scale): one dial for the CRUISE candidate's accel ceiling and its
# ramp (onset AND release). Opt-in via param CruiseAccelScale (FLOAT, default 1.0 == upstream).
# Set on-device with  python3 -c 'from openpilot.common.params import Params; Params().put("CruiseAccelScale", 0.7)'
# or  echo -n 0.7 > /data/params/d/CruiseAccelScale  (the CLI's string put raises TypeError for FLOAT keys).
#
# Why the cruise candidate and not the model: measured on 91 min of driving (routes
# 000000d4/d5, 2026-10-01), with longitudinal engaged DEC sat in 'acc' mode 93% of the
# time, the plan source was 'cruise' 73% of the time, and EVERY brake onset (12/12) plus
# 4/5 accel onsets came from the cruise candidate -- peak decel never exceeded A_CRUISE_MIN
# (max -1.20, median -0.84) and onset jerk of 1.3-1.9 m/s^3 matched J_CRUISE_VALS. The E2E
# model was in control ~7% of the
# time. So the "accelerates/brakes harder than the Toyota system" feel is comma's cruise
# constants, not the weights, and not the personality toggle (relaxed has the same jerk
# factor as standard).
#
# Why acc-mode only: in 'blended' mode the cruise candidate competes with the model's E2E
# accel in min(); slowing the cruise ramp there would win the min() at a green light and
# undo the launch behaviour Cinque was selected for. Blended mode is left byte-identical.
#
# Why this is safe to scale: this planner version does not feed these constants into the
# MPC (there is no set_accel_limits call), so lead-following braking keeps full stock
# authority. Only the no-lead / set-speed path softens. And because the planner output is
# min(candidates), a lower cruise ceiling caps everything above it.
#
# Why the braking FLOOR is deliberately NOT scaled: controlsState.forceDecel (driver
# monitoring escalation) sets v_cruise = 0 and relies on the cruise candidate reaching
# A_CRUISE_MIN to bring the car to a stop; Speed Limit Assist descents use the same floor.
# The complaint is onset ("initial braking"), which is j_cruise; the measured peaks merely
# reached -1.2 and are left alone. So: ceiling and ramp scale, floor stays stock. The call
# site also bypasses the dial entirely while forceDecel is set (so the forced ramp is stock
# too) and for Smart Cruise Control curve/map descents, whose anticipation horizon the
# slower ramp would eat into.
#
# ORDER MATTERS: apply() must run on the TABLE ceiling (get_max_accel) BEFORE the planner's
# lateral-accel and no-throttle coast caps. The coast cap can be negative (-0.3 on the flat);
# scaling a composite that already includes it would move it toward zero and permit MORE
# throttle where the model predicted the driver would lift. apply() also refuses to scale a
# non-positive ceiling as a belt-and-braces guard.
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL

SCALE_MIN = 0.5   # launch ceiling 0.8 m/s^2 and half the onset jerk at the floor -- still drives
SCALE_MAX = 1.0   # never amplify beyond upstream
PARAM = "CruiseAccelScale"


class CruiseAccelScale:
  def __init__(self, params: Params | None = None):
    self._params = params or Params()
    self.scale = 1.0
    self._frame = 0
    self._read()

  def _read(self) -> None:
    try:
      v = float(self._params.get(PARAM, return_default=True))
    except (TypeError, ValueError):
      v = 1.0
    # NaN, zero and negatives are not a softness request, they are a broken value: identity.
    # Below-range positives (0.3) are a softness request: clamp to the floor.
    if v != v or v <= 0.0:
      v = 1.0
    self.scale = float(min(SCALE_MAX, max(SCALE_MIN, v)))

  def update(self) -> None:
    # own counter, same 1 s cadence DEC uses (dec.py _read_params). Not sm.frame: the planner's
    # test MockSubMaster has no .frame, and SubMaster.frame also ticks on timeout frames.
    if self._frame % int(1. / DT_MDL) == 0:
      self._read()
    self._frame += 1

  def apply(self, e2e: bool, max_accel: float, j_cruise: float, a_min: float) -> tuple[float, float, float]:
    """Scale the cruise candidate's ceiling and ramp; the floor passes through untouched.
    Identity in e2e mode or at 1.0."""
    if e2e or self.scale == 1.0:
      return max_accel, j_cruise, a_min
    # never scale a non-positive ceiling (see ORDER MATTERS above)
    return (max_accel * self.scale if max_accel > 0.0 else max_accel), j_cruise * self.scale, a_min
