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
    # With throttle disallowed at speed the CEILING is the (negative) coast accel. The scale must
    # never move that ceiling toward zero -- the r20 review found a version that did, permitting
    # MORE throttle where the model predicted a lift. What IS allowed to differ is the ramp:
    # the scaled candidate approaches the same -0.3 more slowly (j_cruise is scaled), which is
    # the accepted "release ramps slower" tradeoff. Measured on-device 2026-10-02: first step
    # -0.06 (1.0) vs -0.042 (0.7), both converging to -0.3. So assert the ceiling and the
    # steady state, not the first step.
    coast = planner.get_coast_accel(0.0)      # flat ground, -0.3
    assert coast < 0
    def converge(scale, n=80):
      s = CruiseAccelScale(_P(str(scale)))
      a = 0.0
      path = []
      for _ in range(n):
        a = planner.get_cruise_accel(False, 30.0, 10.0, a, 0.0, _CP(), DT_MDL, coast, False, cruise_scale=s)
        path.append(a)
      return path
    p1, p7 = converge(1.0), converge(0.7)
    assert p1[-1] == pytest.approx(coast) and p7[-1] == pytest.approx(coast), "ceiling must be the unscaled coast accel"
    assert all(a <= 0.0 for a in p7), "scaled path must never command throttle when the cap is negative"
    assert min(p7) >= coast - 1e-9, "scaled path must never undershoot the coast cap either"


class TestSccBypassEnumIsTheRightOne:
  """r20 regression. Two enums share the name LongitudinalPlanSource: the capnp one the SP
  planner uses (has sccVision/sccMap) and long_mpc's (lead0/lead1/cruise/e2e, no scc members).
  The stock planner imports the latter, so it must never name an scc member -- it reads the
  bool the SP planner resolves. These run on the Mac; the on-device planner-gate tests are
  the real gate (see ~/.claude/skills/sunnypilot-port/ondevice_planner_tests.sh)."""

  def test_scc_members_exist_on_the_capnp_enum_the_sp_planner_resolves_against(self):
    from openpilot.cereal import custom
    E = custom.LongitudinalPlanSP.LongitudinalPlanSource
    for m in ("sccVision", "sccMap", "cruise"):
      assert hasattr(E, m), m

  def test_stock_planner_never_names_an_scc_source_member(self):
    import os
    import openpilot
    p = os.path.join(os.path.dirname(openpilot.__file__), "selfdrive", "controls", "lib", "longitudinal_planner.py")
    src = open(p).read()
    assert "LongitudinalPlanSource.scc" not in src, "stock planner must read SP's source_is_scc bool, not enum members"
    assert "self.source_is_scc" in src
