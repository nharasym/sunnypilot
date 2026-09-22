"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(lane-policy): the safety-relevant property is "disabled or gated == exactly
# upstream E2E, bit for bit". Everything else is comfort. Tests are ordered accordingly.
import numpy as np

from openpilot.cereal import log
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.modeld.constants import ModelConstants, Plan
from openpilot.sunnypilot.modeld_v2.lane_policy_helper import (
  LanePolicyHelper, MIN_SPEED, ENGAGE_TIME, MAX_PATH_DISAGREEMENT,
)

X = np.asarray(ModelConstants.X_IDXS, dtype=np.float64)
E2E = 0.010          # the "model" curvature under test
V_EGO = 25.0


def _model_output(offset=0.0, curvature=0.0, left_prob=0.9, right_prob=0.9,
                  half_width=1.8, lane_change=0.0, plan_offset=None):
  """Straight-ish lane whose midpoint sits `offset` m to the left of the car."""
  n = len(ModelConstants.X_IDXS)
  mid = offset + 0.5 * curvature * X ** 2
  lane_lines = np.zeros((1, 4, n, 2), dtype=np.float32)
  lane_lines[0, 1, :, 0] = mid + half_width      # left inner
  lane_lines[0, 2, :, 0] = mid - half_width      # right inner

  # probs are interleaved; the helper de-interleaves with [0, 1::2] like fill_model_msg
  probs = np.zeros((1, 8), dtype=np.float32)
  probs[0, 3] = left_prob      # -> index 1 after [1::2]
  probs[0, 5] = right_prob     # -> index 2 after [1::2]

  desire = np.zeros((1, log.Desire.schema.node.enum.enumerants.__len__() + 8), dtype=np.float32)
  desire[0, log.Desire.laneChangeLeft] = lane_change

  plan_y = mid if plan_offset is None else (plan_offset + 0.5 * curvature * X ** 2)
  pos = np.zeros((1, n, 3), dtype=np.float32)
  pos[0, :, 0] = X
  pos[0, :, 1] = plan_y
  return {
    'lane_lines': lane_lines,
    'lane_lines_prob': probs,
    'desire_state': desire,
    'plan': _plan_with_position(pos, n),
  }


def _plan_with_position(pos, n):
  """Build a plan array whose Plan.POSITION slice holds pos."""
  width = Plan.POSITION.stop if isinstance(Plan.POSITION, slice) else 3
  plan = np.zeros((1, n, max(width, 3)), dtype=np.float32)
  plan[0, :, Plan.POSITION] = pos[0]
  return plan


def _settle(h, mo, n=400, **kw):
  out = E2E
  for _ in range(n):
    out = h.apply(mo, E2E, kw.get('v_ego', V_EGO))
  return out


class TestLanePolicyDisabledIsUpstream:
  def test_disabled_returns_e2e_exactly(self):
    h = LanePolicyHelper()
    h.set_state(enabled=False, blinkers_active=False)
    mo = _model_output(offset=0.5)
    for _ in range(50):
      assert h.apply(mo, E2E, V_EGO) == E2E
    assert h.mode == "off"

  def test_disabled_never_accumulates_weight(self):
    h = LanePolicyHelper()
    h.set_state(enabled=False, blinkers_active=False)
    _settle(h, _model_output(offset=0.5))
    assert h.weight == 0.0


class TestLanePolicyEngages:
  def test_centred_lane_leaves_curvature_near_e2e(self):
    h = LanePolicyHelper()
    h.set_state(enabled=True, blinkers_active=False)
    out = _settle(h, _model_output(offset=0.0, curvature=0.0))
    # perfectly centred straight lane -> lane curvature ~0, blended against e2e
    assert abs(out) < abs(E2E) + 1e-6

  def test_offset_lane_steers_toward_midpoint(self):
    # At full weight the output REPLACES e2e with the lane curvature, so the meaningful
    # comparison is offset-vs-centred, not offset-vs-e2e.
    left, centre, right = LanePolicyHelper(), LanePolicyHelper(), LanePolicyHelper()
    for h in (left, centre, right):
      h.set_state(enabled=True, blinkers_active=False)

    out_left = _settle(left, _model_output(offset=0.4))     # midpoint 0.4 m left of us
    out_centre = _settle(centre, _model_output(offset=0.0))
    out_right = _settle(right, _model_output(offset=-0.4))

    # lane to our left -> steer left (positive curvature), and vice versa
    assert out_left > out_centre > out_right
    assert out_left > 0.0 > out_right
    assert left.weight > 0.9

  def test_offset_term_magnitude_matches_the_design(self):
    # c / T^2 with K_o = 1.0: 0.4 m offset at the 30 m lookahead cap (1.5 * 25 m/s)
    h = LanePolicyHelper()
    h.set_state(enabled=True, blinkers_active=False)
    out = _settle(h, _model_output(offset=0.4))
    assert abs(out - 0.4 / (30.0 ** 2)) < 1e-5

  def test_engage_is_ramped_not_stepped(self):
    h = LanePolicyHelper()
    h.set_state(enabled=True, blinkers_active=False)
    mo = _model_output(offset=0.4)
    h.apply(mo, E2E, V_EGO)
    assert h.weight <= DT_MDL / ENGAGE_TIME + 1e-6


class TestLanePolicyGates:
  def test_low_confidence_lines_fall_back(self):
    h = LanePolicyHelper()
    h.set_state(enabled=True, blinkers_active=False)
    out = _settle(h, _model_output(offset=0.4, left_prob=0.1, right_prob=0.1))
    assert out == E2E
    assert "lane confidence" in h.mode

  def test_implausible_lane_width_falls_back(self):
    h = LanePolicyHelper()
    h.set_state(enabled=True, blinkers_active=False)
    out = _settle(h, _model_output(offset=0.4, half_width=4.0))  # 8 m "lane"
    assert out == E2E

  def test_lane_change_intent_falls_back(self):
    h = LanePolicyHelper()
    h.set_state(enabled=True, blinkers_active=False)
    out = _settle(h, _model_output(offset=0.4, lane_change=0.9))
    assert out == E2E

  def test_low_speed_falls_back(self):
    h = LanePolicyHelper()
    h.set_state(enabled=True, blinkers_active=False)
    out = _settle(h, _model_output(offset=0.4), v_ego=MIN_SPEED - 1.0)
    assert out == E2E

  def test_extreme_path_disagreement_falls_back(self):
    h = LanePolicyHelper()
    h.set_state(enabled=True, blinkers_active=False)
    mo = _model_output(offset=0.0, plan_offset=MAX_PATH_DISAGREEMENT + 1.0)
    out = _settle(h, mo)
    assert out == E2E

  def test_malformed_input_falls_back_without_raising(self):
    h = LanePolicyHelper()
    h.set_state(enabled=True, blinkers_active=False)
    assert h.apply({}, E2E, V_EGO) == E2E
    mo = _model_output(offset=0.4)
    mo['lane_lines'][:] = np.nan
    assert h.apply(mo, E2E, V_EGO) == E2E


class TestLanePolicyReleaseIsRamped:
  def test_blinker_release_ramps_out_not_snaps(self):
    h = LanePolicyHelper()
    h.set_state(enabled=True, blinkers_active=False)
    mo = _model_output(offset=0.4)
    engaged = _settle(h, mo)
    assert h.weight > 0.9 and engaged != E2E

    h.set_state(enabled=True, blinkers_active=True)
    first = h.apply(mo, E2E, V_EGO)
    # one frame after the blinker: still blended, NOT snapped back to raw e2e
    assert first != E2E
    assert h.weight < 1.0

    for _ in range(400):
      h.apply(mo, E2E, V_EGO)
    assert h.apply(mo, E2E, V_EGO) == E2E

  def test_toggle_off_ramps_out_then_is_exact(self):
    h = LanePolicyHelper()
    h.set_state(enabled=True, blinkers_active=False)
    mo = _model_output(offset=0.4)
    _settle(h, mo)
    h.set_state(enabled=False, blinkers_active=False)
    assert h.apply(mo, E2E, V_EGO) != E2E      # ramping, not snapping
    for _ in range(400):
      h.apply(mo, E2E, V_EGO)
    assert h.apply(mo, E2E, V_EGO) == E2E
    assert h.weight == 0.0
