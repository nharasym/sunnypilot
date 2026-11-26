"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

HL-FEAT(stop-assist): pure helpers for Stop Assist (no cereal, no Params): where the model plans to stop, whether
it asks to go, the deceleration a stop needs, the blocking-lead test and the stop point tracker.

Road frame: s is an odometer (vEgo integrated between model frames, m). A stop the model plans d metres ahead is
registered at s - vEgo * age + d, where age is how old the camera frame is: modelV2 positions are relative to the
car at camera time, and plannerd runs about 55 ms later.
"""
import math

from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.constants import (
  CONFIRM_SPREAD, FAR_FWD_RATE, FT_TO_M, GO_ACCEL, LAT_S, LEAD_AWAY_VREL, LEAD_MARGIN_M, LIGHTS_STOP_V, MAX_STOP_DIST,
  MEDIAN_N, MIN_EFF_M, NEAR_BACK, NEAR_FWD, NEAR_M, OFFSET_DEFAULT_FT, OFFSET_MAX_FT, OFFSET_MIN_FT, RESERVE_M, STOP_V,
)


def planned_stop(x, v) -> float | None:
  """Distance (m) to the model's planned stop in one frame, or None.

  x, v: modelV2.position.x and velocity.x (any sequence). The stop is where the planned speed is lowest
  (the first such point), if that speed is below LIGHTS_STOP_V, it is not the first point (the plan starts at the
  car's own speed) and it lies within (0, MAX_STOP_DIST]. A plan with a non-finite speed has no stop.
  """
  n = min(len(x), len(v))
  if n < 2:
    return None
  speeds = [float(v[k]) for k in range(n)]
  if not all(math.isfinite(s) for s in speeds):
    return None
  i = min(range(n), key=speeds.__getitem__)
  if i < 1 or not speeds[i] < LIGHTS_STOP_V:
    return None
  d = float(x[i])
  if not (math.isfinite(d) and 0.0 < d <= MAX_STOP_DIST):
    return None
  return d


def model_go(should_stop: bool, a_model: float, v_ego: float) -> bool:
  """The model asks to go.

  At a standstill this is `not action.shouldStop`, which modeld computes as v_ego < 0.3 and raw accel < 0.1, so it
  is the model asking for at least +0.1 m/s^2: the stock release signal. Moving at or above STOP_V that flag is
  always false, so the published action itself must ask for GO_ACCEL. The plan minimum is never used here: below
  ~1.5 m/s it is the car's own speed and would never say go.
  """
  if should_stop:
    return False
  return v_ego < STOP_V or a_model >= GO_ACCEL


def required_decel(v_ego: float, remaining: float) -> float:
  """Constant deceleration (positive, m/s^2) that stops the car `remaining` metres ahead, after the command lag
  (LAT_S) and the stopping-state reserve (RESERVE_M). The effective distance is floored at MIN_EFF_M."""
  eff = remaining - LAT_S * v_ego - RESERVE_M
  return v_ego * v_ego / (2.0 * max(eff, MIN_EFF_M))


def blocking_lead(present: bool, d_rel: float, v_rel: float, remaining: float) -> bool:
  """A lead nearer than our stop point + LEAD_MARGIN_M blocks (the lead planner owns that stop), unless it is pulling
  away by LEAD_AWAY_VREL or more."""
  return bool(present) and d_rel < remaining + LEAD_MARGIN_M and v_rel < LEAD_AWAY_VREL


def median(values) -> float:
  ordered = sorted(float(e) for e in values)
  n = len(ordered)
  if n == 0:
    raise ValueError("median of no values")
  mid = n // 2
  return ordered[mid] if n % 2 else 0.5 * (ordered[mid - 1] + ordered[mid])


def points_agree(points, spread: float = CONFIRM_SPREAD) -> bool:
  """The points lie within `spread` of each other after dropping the single worst one (farthest from the median)."""
  pts = [float(p) for p in points]
  if len(pts) <= 2:
    return len(pts) > 0
  med = median(pts)
  worst = max(range(len(pts)), key=lambda k: abs(pts[k] - med))
  rest = pts[:worst] + pts[worst + 1:]
  return max(rest) - min(rest) <= spread


def offset_m_from_ft(raw) -> float:
  """StopAssistLightsOffsetFt as metres: clamped to [OFFSET_MIN_FT, OFFSET_MAX_FT]; unreadable -> the default."""
  try:
    ft = float(raw)
  except (TypeError, ValueError):
    ft = float(OFFSET_DEFAULT_FT)
  if not math.isfinite(ft):
    ft = float(OFFSET_DEFAULT_FT)
  return min(max(ft, float(OFFSET_MIN_FT)), float(OFFSET_MAX_FT)) * FT_TO_M


class StopPointTracker:
  """Target stop position in road coordinates: the median of the last MEDIAN_N raw stop points minus the offset.

  Far phase (remaining > NEAR_M): a move toward the car is followed at once, a move away at most FAR_FWD_RATE.
  Near phase: the anchor is the target when remaining first drops to NEAR_M; from then the target stays within
  [anchor - NEAR_BACK, anchor + NEAR_FWD]. freeze() stops all movement (FINAL).
  """

  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self._points: list[float] = []
    self._offset = 0.0
    self.target: float | None = None
    self.anchor: float | None = None
    self.frozen = False

  def start(self, points, offset_m: float, s_now: float) -> float:
    """Seed from the confirmed points (oldest first)."""
    self.reset()
    self._offset = float(offset_m)
    self._points = [float(p) for p in points][-MEDIAN_N:]
    self.target = median(self._points) - self._offset
    self._check_anchor(s_now)
    return self.target

  def update(self, s_raw: float | None, s_now: float, dt: float | None) -> float | None:
    """One model frame. s_raw None (no planned stop this frame) keeps the target."""
    if self.target is None or self.frozen:
      return self.target
    if s_raw is not None:
      self._points.append(float(s_raw))
      del self._points[:-MEDIAN_N]
      cand = median(self._points) - self._offset
      if self.anchor is None:
        if cand <= self.target:
          self.target = cand
        else:
          self.target = min(cand, self.target + FAR_FWD_RATE * max(dt or 0.0, 0.0))
      else:
        self.target = min(max(cand, self.anchor - NEAR_BACK), self.anchor + NEAR_FWD)
    self._check_anchor(s_now)
    return self.target

  def freeze(self) -> None:
    self.frozen = True

  def _check_anchor(self, s_now: float) -> None:
    if self.anchor is None and self.target is not None and self.target - s_now <= NEAR_M:
      self.anchor = self.target
