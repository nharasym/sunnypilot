"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot.selfdrive.controls.lib.hud_lane_visibility import HudLaneVisibility, ON_PROB, OFF_PROB, HOLD_S


def _probs(left, right):
  # modelV2.laneLineProbs has four entries: outer-left, left, right, outer-right
  return [0.0, left, right, 0.0]


class TestHudLaneVisibility:
  def test_starts_as_outlines(self):
    h = HudLaneVisibility()
    assert (h.left, h.right) == (False, False)

  def test_seen_above_on_threshold_per_side(self):
    h = HudLaneVisibility()
    h.update(_probs(ON_PROB, OFF_PROB), 0.0)
    assert (h.left, h.right) == (True, False)

  def test_between_thresholds_keeps_state(self):
    h = HudLaneVisibility()
    h.update(_probs(0.9, 0.9), 0.0)
    h.update(_probs(0.5, 0.5), 10.0)          # ambiguous, long after the hold
    assert (h.left, h.right) == (True, True)

  def test_drops_only_after_hold_below_off_threshold(self):
    h = HudLaneVisibility()
    h.update(_probs(0.9, 0.9), 0.0)
    h.update(_probs(0.1, 0.1), 0.0 + HOLD_S - 0.05)
    assert (h.left, h.right) == (True, True)  # a dashed-line gap: held
    h.update(_probs(0.1, 0.1), 0.0 + HOLD_S)
    assert (h.left, h.right) == (False, False)

  def test_a_fresh_sighting_restarts_the_hold(self):
    h = HudLaneVisibility()
    h.update(_probs(0.9, 0.9), 0.0)
    h.update(_probs(0.9, 0.9), 0.75 * HOLD_S)
    h.update(_probs(0.1, 0.1), 1.5 * HOLD_S)             # 0.75*HOLD_S after the last sighting
    assert h.left and h.right
    h.update(_probs(0.1, 0.1), 1.75 * HOLD_S)            # HOLD_S after it
    assert not h.left and not h.right

  def test_the_gap_between_thresholds_never_turns_a_side_on(self):
    h = HudLaneVisibility()
    h.update(_probs(ON_PROB - 1e-3, OFF_PROB + 1e-3), 0.0)
    assert (h.left, h.right) == (False, False)
    h.update(_probs(ON_PROB - 1e-3, 0.9), 100.0)
    assert (h.left, h.right) == (False, True)

  def test_ambiguous_samples_do_not_extend_the_hold(self):
    # the rule the log replay measured: the hold runs from the last CONFIDENT sighting
    h = HudLaneVisibility()
    h.update(_probs(0.9, 0.9), 0.0)
    h.update(_probs(0.5, 0.5), HOLD_S - 0.1)
    h.update(_probs(0.1, 0.1), HOLD_S)
    assert (h.left, h.right) == (False, False)

  def test_exactly_off_prob_does_not_drop(self):
    h = HudLaneVisibility()
    h.update(_probs(0.9, 0.9), 0.0)
    h.update(_probs(OFF_PROB, OFF_PROB), HOLD_S + 1.0)
    assert (h.left, h.right) == (True, True)

  def test_reset_returns_to_outlines(self):
    h = HudLaneVisibility()
    h.update(_probs(0.9, 0.9), 0.0)
    h.reset()
    assert (h.left, h.right) == (False, False)
    h.update(_probs(0.1, 0.1), 0.0)                      # and the hold is gone too
    assert (h.left, h.right) == (False, False)

  def test_short_prob_list_is_ignored(self):
    h = HudLaneVisibility()
    h.update(_probs(0.9, 0.9), 0.0)
    h.update([], 10.0)
    assert (h.left, h.right) == (True, True)

  def test_sides_are_independent(self):
    h = HudLaneVisibility()
    h.update(_probs(0.9, 0.9), 0.0)
    h.update(_probs(0.9, 0.1), 5.0)
    assert (h.left, h.right) == (True, False)
