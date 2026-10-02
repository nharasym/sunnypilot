"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(cruise-accel-scale). The property that matters most: default 1.0 and e2e mode
# are IDENTITY, so an unset or untouched param changes nothing on the road.
import math
import pytest

from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.cruise_accel_scale import (
  CruiseAccelScale, SCALE_MIN, SCALE_MAX, PARAM,
)


class _P:
  def __init__(self, v):
    self.v = v
  def get(self, k, return_default=False):
    assert k == PARAM
    return self.v


MAX_A, J, A_MIN = 1.6, 1.6, -1.2


class TestIdentity:
  def test_default_is_upstream_bit_for_bit(self):
    s = CruiseAccelScale(_P("1.0"))
    assert s.apply(False, MAX_A, J, A_MIN) == (MAX_A, J, A_MIN)
    assert s.apply(True, MAX_A, J, A_MIN) == (MAX_A, J, A_MIN)

  def test_e2e_mode_is_never_scaled(self):
    s = CruiseAccelScale(_P("0.6"))
    assert s.apply(True, MAX_A, J, A_MIN) == (MAX_A, J, A_MIN)

  def test_unset_or_garbage_param_falls_back_to_identity(self):
    # NaN / zero / negative are broken values, not softness requests
    for bad in (None, "", "abc", float("nan"), "0", "0.0", "-1"):
      s = CruiseAccelScale(_P(bad))
      assert s.scale == 1.0, bad

  def test_below_range_positive_is_a_softness_request_and_clamps(self):
    assert CruiseAccelScale(_P("0.3")).scale == SCALE_MIN


class TestScaling:
  def test_acc_mode_scales_ceiling_and_ramp_only(self):
    s = CruiseAccelScale(_P("0.7"))
    mx, j, mn = s.apply(False, MAX_A, J, A_MIN)
    assert math.isclose(mx, 1.12) and math.isclose(j, 1.12)
    assert mn == A_MIN, "braking floor must pass through: forceDecel and SLA descents depend on it"

  def test_never_amplifies(self):
    assert CruiseAccelScale(_P("1.5")).scale == SCALE_MAX

  def test_scale_clamps_so_cruise_can_still_move(self):
    assert CruiseAccelScale(_P("0.1")).scale == SCALE_MIN
    mx, j, mn = CruiseAccelScale(_P("0.1")).apply(False, MAX_A, J, A_MIN)
    assert mx >= 0.8 and j >= 0.8 and mn == A_MIN

  def test_floor_is_never_scaled(self):
    # the braking floor passes through at any scale (the forceDecel RAMP is handled at the
    # planner call site, which bypasses the dial entirely while forceDecel is set)
    for v in ("0.5", "0.7", "0.9"):
      assert CruiseAccelScale(_P(v)).apply(False, MAX_A, J, A_MIN)[2] == A_MIN

  def test_non_positive_ceiling_is_never_scaled(self):
    # belt and braces for the coast cap: a negative or zero ceiling must not move toward zero
    s = CruiseAccelScale(_P("0.5"))
    assert s.apply(False, -0.3, J, A_MIN)[0] == -0.3
    assert s.apply(False, 0.0, J, A_MIN)[0] == 0.0


class TestCadence:
  def test_param_is_reread_once_per_second_not_every_frame(self):
    p = _P("1.0")
    s = CruiseAccelScale(p)
    s.update()                      # frame 0: reads (same value)
    p.v = "0.6"
    for _ in range(int(1. / DT_MDL) - 1):
      s.update()                    # frames 1..19: must not re-read
    assert s.scale == 1.0, "must not re-read off-cadence"
    s.update()                      # frame 20: re-reads
    assert s.scale == 0.6

  def test_live_change_takes_effect_without_restart(self):
    p = _P("0.6")
    s = CruiseAccelScale(p)
    assert s.apply(False, MAX_A, J, A_MIN)[0] == pytest.approx(0.96)
    p.v = "1.0"
    for _ in range(int(1. / DT_MDL) + 1):
      s.update()
    assert s.apply(False, MAX_A, J, A_MIN) == (MAX_A, J, A_MIN)


class _CP:
  steerRatio = 16.0
  wheelbase = 2.82


@pytest.fixture
def planner():
  # The planner-level checks need the stock planner, which imports casadi/acados at import
  # time; that exists on-device and in CI, not necessarily on a dev Mac. Scoped here so a
  # missing solver skips ONLY this class, not the pure helper tests above.
  return pytest.importorskip("openpilot.selfdrive.controls.lib.longitudinal_planner")


class TestInPlanner:
  def _run(self, planner, scale, e2e, v_ego=0.0, allow_throttle=True, accel_coast=None):
    s = CruiseAccelScale(_P(str(scale)))
    # far below set speed on a straight: cruise candidate wants max accel, rate-limited from
    # a_prev=0 by j_cruise; one step from 0 is exactly j_cruise*dt
    return planner.get_cruise_accel(e2e, v_cruise=30.0, v_ego=v_ego, a_cruise_prev=0.0, angle_steers=0.0,
                                    CP=_CP(), dt=DT_MDL, accel_coast=planner.ACCEL_MAX if accel_coast is None else accel_coast,
                                    allow_throttle=allow_throttle, cruise_scale=s)

  def test_acc_mode_first_step_is_scaled_jerk(self, planner):
    j0 = planner.J_CRUISE_VALS[0]
    assert self._run(planner, 1.0, False) == pytest.approx(j0 * DT_MDL)
    assert self._run(planner, 0.7, False) == pytest.approx(0.7 * j0 * DT_MDL)

  def test_blended_mode_ignores_the_scale(self, planner):
    assert self._run(planner, 0.7, True) == self._run(planner, 1.0, True)

  def test_no_scale_object_is_upstream(self, planner):
    ref = planner.get_cruise_accel(False, 30.0, 0.0, 0.0, 0.0, _CP(), DT_MDL, planner.ACCEL_MAX, True)
    assert ref == self._run(planner, 1.0, False)

  def test_no_throttle_coast_cap_is_not_loosened_by_the_scale(self, planner):
    # with throttle disallowed at speed the ceiling is the (negative) coast accel; a scale must
    # never move it toward zero. Both scales must yield the identical first step.
    coast = planner.get_coast_accel(0.0)      # flat ground, about -0.3
    assert coast < 0
    a1 = self._run(planner, 1.0, False, v_ego=10.0, allow_throttle=False, accel_coast=coast)
    a7 = self._run(planner, 0.7, False, v_ego=10.0, allow_throttle=False, accel_coast=coast)
    assert a7 == a1
