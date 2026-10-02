"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

HL-FEAT(stock-hud-lines): which lane lines the cluster should draw as "seen".

Toyota's camera draws a lane line solid only while it sees the marker and as an outline
otherwise (2023 Highlander manual). Upstream openpilot has hardcoded hudControl.left/
rightLaneVisible = True since #22693 (2021), so every brand's cluster shows solid lines at
all times, parked included. This feeds the model's own lane-line confidence back in, with
enough filtering that the dash does not flicker on dashed or worn markings.

Rule: a side turns "seen" on any sample >= ON_PROB, and turns "unseen" on a sample < OFF_PROB
only if at least HOLD_S has passed since the last >= ON_PROB sample. Samples in between the
two thresholds neither extend the hold nor drop the line (so a marking the model rates ~0.5
stays as it was). This exact rule was replayed over 2.1 h of this car's logs (2026-10-02,
laneLineProbs[1]/[2] at 20 Hz): a bare 0.5 threshold flips 15-25 times a minute at city
speed and ~8/min at highway speed; this rule gives ~3.5/min and ~1-2.4/min while the solid
fraction barely moves (76%/58% L/R at 8-15 m/s, 99%/87% above 15 m/s, ~5% parked).
"""
import math

ON_PROB = 0.6       # a marker is "seen" once the model is this sure of it
OFF_PROB = 0.4      # ... and may only become unseen when the model is this unsure ...
HOLD_S = 2.0        # ... and that long has passed since the last confident sighting


class HudLaneVisibility:
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    """Outlines: what a parked car shows, and the honest state when the model is not running."""
    self.left = False
    self.right = False
    self._last_on = [-math.inf, -math.inf]

  def update(self, lane_line_probs, t: float) -> None:
    """lane_line_probs: modelV2.laneLineProbs (index 1 = left inner, 2 = right inner); t in seconds."""
    if len(lane_line_probs) < 3:
      return
    vis = [self.left, self.right]
    for side, idx in enumerate((1, 2)):
      p = float(lane_line_probs[idx])
      if p >= ON_PROB:
        vis[side] = True
        self._last_on[side] = t
      elif vis[side] and p < OFF_PROB and (t - self._last_on[side]) >= HOLD_S:
        vis[side] = False
    self.left, self.right = vis
