"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(stop-assist): the pure helpers. unittest.TestCase, setUp only (runs under the on-device shim too).
import math
import unittest

import numpy as np

from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import constants as C
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.stop_point import (
  StopPointTracker, blocking_lead, median, model_go, offset_m_from_ft, planned_stop, points_agree, required_decel,
)
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.tests.sim_helpers import (
  T_IDXS, plan_cruise, plan_parked, plan_standstill_go, plan_stop,
)


class TestPlannedStop(unittest.TestCase):
  def test_stop_present(self):
    x, v = plan_stop(12.0, 40.0)
    d = planned_stop(x, v)
    assert d is not None and abs(d - 40.0) < 1e-3, d
    assert type(d) is float

  def test_no_stop_when_the_plan_keeps_speed(self):
    assert planned_stop(*plan_cruise(12.0)) is None

  def test_slow_down_that_stays_above_the_stop_speed_is_not_a_stop(self):
    v = np.linspace(10.0, 2.0, len(T_IDXS))
    x = np.cumsum(v) * 0.3
    assert planned_stop(x, v) is None

  def test_stop_speed_threshold(self):
    v = np.linspace(10.0, C.LIGHTS_STOP_V, len(T_IDXS))
    x = np.linspace(0.0, 60.0, len(T_IDXS))
    assert planned_stop(x, v) is None  # exactly 1.0 m/s is not below it
    v[-1] = C.LIGHTS_STOP_V - 0.01
    assert planned_stop(x, v) == 60.0

  def test_beyond_100_m_is_ignored(self):
    x, v = plan_stop(25.0, 120.0)  # stops inside the horizon, 120 m out
    assert float(np.min(v)) == 0.0
    assert planned_stop(x, v) is None

  def test_exactly_100_m_counts(self):
    x, v = plan_stop(25.0, C.MAX_STOP_DIST)
    assert planned_stop(x, v) == C.MAX_STOP_DIST

  def test_minimum_at_the_horizon(self):
    # still slowing at 10 s: the slowest point is the last one, below the stop speed and within 100 m
    v = np.linspace(9.0, 0.5, len(T_IDXS))
    x = np.linspace(0.0, 48.0, len(T_IDXS))
    assert planned_stop(x, v) == 48.0

  def test_minimum_at_the_first_point_is_not_a_planned_stop(self):
    assert planned_stop(*plan_parked()) is None
    assert planned_stop(*plan_standstill_go(30.0)) is None  # v[0..3] = 0: the minimum is the car's own speed

  def test_first_of_equal_minima_is_the_stop_point(self):
    x, v = plan_stop(10.0, 30.0)
    i = int(np.argmax(v == 0.0))
    assert i >= 1 and planned_stop(x, v) == float(x[i])

  def test_broken_plans(self):
    assert planned_stop([], []) is None
    assert planned_stop([1.0], [0.0]) is None
    x, v = plan_stop(12.0, 40.0)
    v = v.copy()
    v[5] = np.nan
    assert planned_stop(x, v) is None
    x2 = x.copy()
    x2[:] = np.nan
    assert planned_stop(x2, plan_stop(12.0, 40.0)[1]) is None
    x3, v3 = plan_stop(12.0, 40.0)
    assert planned_stop(-x3, v3) is None  # a stop behind the car


class TestModelGo(unittest.TestCase):
  def test_standstill_go_is_the_model_stop_flag(self):
    assert model_go(False, -0.5, 0.0)  # at a standstill modeld drops shouldStop only for raw accel >= 0.1
    assert not model_go(True, 0.5, 0.0)

  def test_moving_needs_the_model_to_ask_for_accel(self):
    # modeld's shouldStop is v_ego < 0.3 and accel < 0.1, so it is always False while moving
    assert not model_go(False, 0.0, 1.0)
    assert not model_go(False, 0.09, 0.5)
    assert model_go(False, C.GO_ACCEL, 1.0)
    assert not model_go(False, float('nan'), 1.0)


class TestRequiredDecel(unittest.TestCase):
  def test_value(self):
    v, rem = 10.0, 30.0
    eff = rem - C.LAT_S * v - C.RESERVE_M
    assert math.isclose(required_decel(v, rem), v * v / (2 * eff))

  def test_floor_keeps_it_finite_at_and_past_the_target(self):
    assert math.isclose(required_decel(1.0, 0.0), 1.0 / (2 * C.MIN_EFF_M))
    assert math.isclose(required_decel(1.0, -3.0), 1.0 / (2 * C.MIN_EFF_M))
    assert required_decel(0.0, 5.0) == 0.0


class TestBlockingLead(unittest.TestCase):
  def test_rules(self):
    assert blocking_lead(True, 25.0, 0.0, 20.0)       # before the stop point
    assert blocking_lead(True, 29.0, 0.0, 20.0)       # within the 10 m margin past it
    assert not blocking_lead(True, 31.0, 0.0, 20.0)   # beyond stop + 10 m
    assert not blocking_lead(True, 15.0, C.LEAD_AWAY_VREL, 20.0)  # pulling away by 2 m/s does not block
    assert blocking_lead(True, 15.0, 1.9, 20.0)
    assert not blocking_lead(False, 5.0, -3.0, 20.0)


class TestOffset(unittest.TestCase):
  def test_conversion_and_clamp(self):
    assert offset_m_from_ft(0) == 0.0
    assert math.isclose(offset_m_from_ft(3), 3 * 0.3048)
    assert math.isclose(offset_m_from_ft(10), 10 * 0.3048)
    assert math.isclose(offset_m_from_ft(25), 10 * 0.3048)
    assert offset_m_from_ft(-4) == 0.0
    assert math.isclose(offset_m_from_ft(b"7"), 7 * 0.3048)

  def test_unreadable_is_the_default(self):
    for raw in (None, "x", float('nan'), float('inf')):
      assert math.isclose(offset_m_from_ft(raw), C.OFFSET_DEFAULT_FT * 0.3048), raw


class TestAgreement(unittest.TestCase):
  def test_worst_point_is_dropped(self):
    pts = [40.0, 40.5, 39.8, 40.2, 40.1, 39.9, 40.3, 47.0]
    assert points_agree(pts, 3.0)
    pts2 = [40.0, 40.5, 39.8, 40.2, 40.1, 39.9, 44.0, 47.0]
    assert not points_agree(pts2, 3.0)

  def test_median(self):
    assert median([3.0, 1.0, 2.0]) == 2.0
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5


class TestTracker(unittest.TestCase):
  def setUp(self):
    self.tr = StopPointTracker()

  def test_target_is_the_median_minus_the_offset(self):
    pts = [50.0, 50.4, 49.8, 50.2, 54.4, 50.1, 49.9]  # one 4.4 m jump
    t = self.tr.start(pts, 0.9, s_now=0.0)
    assert math.isclose(t, 50.1 - 0.9)
    assert type(t) is float

  def test_far_moves_toward_the_car_at_once(self):
    self.tr.start([60.0] * 7, 0.0, s_now=0.0)
    for _ in range(4):
      self.tr.update(55.0, 0.0, 0.05)  # the median flips after 4 of 7
    assert math.isclose(self.tr.target, 55.0)

  def test_far_moves_away_are_rate_limited(self):
    self.tr.start([60.0] * 7, 0.0, s_now=0.0)
    for _ in range(4):
      self.tr.update(63.0, 0.0, 0.05)
    assert math.isclose(self.tr.target, 60.0 + C.FAR_FWD_RATE * 0.05)  # only since the median moved
    for _ in range(20):
      self.tr.update(63.0, 0.0, 0.05)
    assert math.isclose(self.tr.target, 60.0 + C.FAR_FWD_RATE * 0.05 * 21)

  def test_dropout_keeps_the_target(self):
    self.tr.start([60.0] * 7, 0.0, s_now=0.0)
    self.tr.update(None, 10.0, 0.05)
    assert self.tr.target == 60.0

  def test_near_phase_bounds(self):
    self.tr.start([30.0] * 7, 0.0, s_now=0.0)
    assert self.tr.anchor is None
    self.tr.update(30.0, 10.5, 0.05)  # remaining 19.5 <= NEAR_M: anchored
    assert self.tr.anchor == 30.0
    for _ in range(7):
      self.tr.update(40.0, 11.0, 0.05)
    assert math.isclose(self.tr.target, 30.0 + C.NEAR_FWD)
    for _ in range(7):
      self.tr.update(20.0, 11.0, 0.05)
    assert math.isclose(self.tr.target, 30.0 - C.NEAR_BACK)

  def test_anchor_at_start_when_already_near(self):
    self.tr.start([15.0] * 7, 0.0, s_now=0.0)
    assert self.tr.anchor == 15.0

  def test_freeze(self):
    self.tr.start([30.0] * 7, 0.0, s_now=25.0)
    self.tr.freeze()
    for _ in range(10):
      self.tr.update(28.0, 26.0, 0.05)
    assert self.tr.target == 30.0

  def test_reset(self):
    self.tr.start([30.0] * 7, 0.0, s_now=0.0)
    self.tr.reset()
    assert self.tr.target is None and self.tr.anchor is None and not self.tr.frozen
    assert self.tr.update(30.0, 0.0, 0.05) is None
