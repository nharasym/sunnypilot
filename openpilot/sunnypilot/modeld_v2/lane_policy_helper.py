"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(lane-policy): EXPERIMENTAL, opt-in, default OFF (param LanePolicyControl).
#
# Symptom this targets: on long sustained corners the model hugs the INSIDE of the
# lane. That is learned behaviour — the training data is human driving, and humans cut
# corners — so it lives in the weights and no amount of lateral tuning downstream
# removes it. The only lever is to override the commanded curvature with one derived
# from where the lane actually is.
#
# Idea adapted from gm1500/openpilot PR #1 (lkas-lp-damp-oscillation). Reimplemented
# against modeld_v2 rather than cherry-picked: their patch targets
# selfdrive/modeld/modeld.py, which is the stock-model path and never runs on the
# chestnut/eGPU setup.
#
# Mechanism: fit y = ax^2 + bx + c to the midpoint of the two inner lane lines over
# 5..35 m, and build a curvature from it:
#
#     curvature = geometry + K_h * b / T + K_o * c / T^2
#
# where c is lateral offset and b is heading error at the lookahead T. That is a PD
# controller on lane offset (b is the derivative term), closed around a plant where
# lateral position is the double integral of curvature:
#
#     y'' + (K_h/T) y' + (K_o/T^2) y = 0     =>     zeta = K_h / (2*sqrt(K_o))
#
# GAIN NOTE, since this is the part most likely to be "fixed" wrongly later: the
# idealised zeta says K_h=K_o=2.0 is near-critically damped (0.71) and the 1.0/1.0
# used here is underdamped (0.50). Reality disagreed — 2.0/2.0 weaved on the road for
# gm1500's tester, because the real loop carries lag the ideal model ignores (the
# curvature filter, actuator and EPS response) and because noisy inner lines get
# amplified by loop gain. Road result beats the closed form, so we start at the
# road-tested 1.0/1.0. If it under-corrects, raise K_h RELATIVE to K_o (2.0/1.0 gives
# zeta 1.0) rather than raising both — raising both together lowers zeta.
#
# Everything is gated hard and the blend is ramped in and out; any gate failure or
# error path returns exactly the upstream E2E curvature.
import numpy as np

from openpilot.cereal import log
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
from openpilot.selfdrive.modeld.constants import ModelConstants, Plan

# lane geometry gates
MIN_LANE_WIDTH = 2.6            # m
MAX_LANE_WIDTH = 4.2            # m
MIN_WIDTH_EDGE = 0.15           # m inside the accepted width range
MAX_WIDTH_CHANGE = 0.18         # m peak-to-peak across the fitted horizon
ENTER_LINE_PROB = 0.60          # inner-line confidence to engage
HOLD_LINE_PROB = 0.40           # ...and the lower bar to stay engaged (hysteresis)
MAX_LANE_CHANGE_PROB = 0.10
MAX_PATH_DISAGREEMENT = 1.20    # m, hard bail

# blend dynamics
ENGAGE_TIME = 0.80              # s
RELEASE_TIME = 0.70             # s
HEADING_GAIN = 1.0              # K_h  (see GAIN NOTE above)
OFFSET_GAIN = 1.0               # K_o

MIN_SPEED = 8.0                 # m/s; below this lane lines are unreliable and it matters least


class LanePolicyHelper:
  """HL-FEAT(lane-policy): blends a lane-midpoint curvature over the model's E2E curvature."""

  def __init__(self):
    self.enabled = False
    self.blinkers_active = False
    self.weight = 0.0
    self.lane_curvature = 0.0
    self.has_lane_curvature = False
    self.full_active = False
    self.mode = "off"
    self._error_logged = False

  def reset(self):
    self.weight = 0.0
    self.lane_curvature = 0.0
    self.has_lane_curvature = False
    self.full_active = False

  def set_state(self, enabled: bool, blinkers_active: bool):
    self.enabled = enabled
    self.blinkers_active = blinkers_active

  def _lane_target(self, model_output: dict[str, np.ndarray], e2e_curvature: float, v_ego: float):
    """Returns (target_weight, lane_curvature | None, mode). Never raises."""
    # Lane-line axes are [batch, lane, distance, coordinate]. The inner lines are lane 1
    # (left) and lane 2 (right); fill_model_msg.py uses the same indices.
    left_y = model_output['lane_lines'][0, 1, :, 0].astype(np.float64)
    right_y = model_output['lane_lines'][0, 2, :, 0].astype(np.float64)
    # lane_lines_prob is interleaved; fill_model_msg.py:130 de-interleaves with [0, 1::2]
    # before publishing modelV2.laneLineProbs, so the inner lines land at 1 and 2 only
    # AFTER that slice (raw indices 3 and 5). Use the identical slice so our thresholds
    # are in the same units as the probabilities shown everywhere else.
    probs = model_output['lane_lines_prob'][0, 1::2]
    left_prob, right_prob = float(probs[1]), float(probs[2])

    desire_state = model_output['desire_state'][0]
    lane_change_prob = float(desire_state[log.Desire.laneChangeLeft] +
                             desire_state[log.Desire.laneChangeRight])
    if lane_change_prob > MAX_LANE_CHANGE_PROB:
      return 0.0, None, "e2e: lane-change intent"

    x = np.asarray(ModelConstants.X_IDXS, dtype=np.float64)
    lookahead = float(np.clip(1.5 * v_ego, 12.0, 30.0))
    fit = (x >= 5.0) & (x <= 35.0)
    lane_width = left_y - right_y

    if not (np.count_nonzero(fit) >= 3 and
            np.isfinite(left_prob) and np.isfinite(right_prob) and
            np.all(np.isfinite(left_y[fit])) and np.all(np.isfinite(right_y[fit])) and
            np.all((lane_width[fit] >= MIN_LANE_WIDTH) & (lane_width[fit] <= MAX_LANE_WIDTH))):
      self.full_active = False
      return 0.0, None, "e2e: lane geometry"

    mean_width = float(np.mean(lane_width[fit]))
    width_change = float(np.ptp(lane_width[fit]))
    width_edge = min(mean_width - MIN_LANE_WIDTH, MAX_LANE_WIDTH - mean_width)
    # hysteresis: harder to engage than to stay engaged, so we don't flicker at the bar
    required_prob = HOLD_LINE_PROB if self.full_active else ENTER_LINE_PROB

    if min(left_prob, right_prob) < required_prob:
      self.full_active = False
      return 0.0, None, "e2e: lane confidence"
    if width_edge < MIN_WIDTH_EDGE or width_change > MAX_WIDTH_CHANGE:
      self.full_active = False
      return 0.0, None, "e2e: lane-width stability"

    a, b, c = np.polyfit(x[fit], 0.5 * (left_y[fit] + right_y[fit]), 2)
    slope = 2.0 * a * lookahead + b
    geometry_curvature = 2.0 * a / ((1.0 + slope * slope) ** 1.5)
    lane_curvature = (geometry_curvature +
                      HEADING_GAIN * b / lookahead +
                      OFFSET_GAIN * c / (lookahead * lookahead))

    plan = model_output['plan'][0, :, Plan.POSITION]
    plan_x, plan_y = plan[:, 0], plan[:, 1]
    horizon = (plan_x >= 0.0) & (plan_x <= max(lookahead + 5.0, 20.0))
    hx, hy = plan_x[horizon], plan_y[horizon]
    if not (hx.size >= 2 and np.all(np.isfinite(hx)) and np.all(np.isfinite(hy)) and
            hx[0] <= lookahead <= hx[-1] and np.all(np.diff(hx) > 0.0) and
            np.isfinite(lane_curvature)):
      self.full_active = False
      return 0.0, None, "e2e: path unavailable"

    # NOTE(disagreement gate): model corner-cutting IS path disagreement, so this gate is
    # partly anti-correlated with the symptom we are chasing — too tight and it bails out
    # exactly when the cut is worst. gm1500 used 0.75 m; raised to 1.20 m here so an
    # ordinary sustained-corner cut stays inside the gate while a genuinely divergent
    # lane fit (construction, forks, merges) still hands control back to E2E.
    e2e_y = float(np.interp(lookahead, hx, hy))
    lane_y = float(np.polyval((a, b, c), lookahead))
    if abs(e2e_y - lane_y) > MAX_PATH_DISAGREEMENT:
      self.full_active = False
      return 0.0, None, "e2e: path disagreement"

    self.full_active = True
    return 1.0, float(lane_curvature), "lane-midpoint"

  def apply(self, model_output: dict[str, np.ndarray], e2e_curvature: float, v_ego: float) -> float:
    """Returns the curvature to command. Exactly e2e_curvature whenever weight is 0."""
    # Off with no leftover weight is byte-identical upstream behaviour. With leftover
    # weight we keep running so the blend ramps out instead of snapping — a step in
    # commanded curvature is a yank at the wheel.
    if not self.enabled and self.weight <= 1e-3:
      self.reset()
      self.mode = "off"
      return float(e2e_curvature)

    force_release = (not self.enabled) or self.blinkers_active or v_ego < MIN_SPEED
    if force_release:
      self.full_active = False
      target_weight, candidate = 0.0, None
      mode = ("off" if not self.enabled else
              "e2e: blinker" if self.blinkers_active else "e2e: low speed")
    else:
      try:
        target_weight, candidate, mode = self._lane_target(model_output, e2e_curvature, v_ego)
      except (KeyError, IndexError, TypeError, ValueError,
              FloatingPointError, np.linalg.LinAlgError) as err:
        self.full_active = False
        target_weight, candidate, mode = 0.0, None, "e2e: input error"
        if not self._error_logged:
          cloudlog.warning(f"lane-policy input error: {type(err).__name__}: {err}")
          self._error_logged = True

    time_constant = ENGAGE_TIME if target_weight > self.weight else RELEASE_TIME
    self.weight += (target_weight - self.weight) * DT_MDL / time_constant
    self.weight = float(np.clip(self.weight, 0.0, 1.0))

    if candidate is not None:
      self.lane_curvature = candidate
      self.has_lane_curvature = True

    if self.weight <= 1e-3 or not self.has_lane_curvature:
      self.mode = mode
      if self.weight <= 1e-3:
        self.reset()
      return float(e2e_curvature)

    self.mode = f"{mode} (w={self.weight:.2f})" if mode != "lane-midpoint" else f"lane-midpoint (w={self.weight:.2f})"
    return float((1.0 - self.weight) * e2e_curvature + self.weight * self.lane_curvature)
