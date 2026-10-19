"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

HL-FEAT(long-indicator-corner): upstream's longitudinal policy indicator (lead car + personality bars,
#39040) draws in the LEFT column under the DMoji, stacked over the steering-wheel icon. This fork moves it
to the BOTTOM-RIGHT corner of the content area -- the spot upstream's transient chestnut icon used before
our GPU badge moved it to the top -- so the driver-facing items (face, wheel) keep the left edge and the
status cluster (badge, temps above; lead/gap below) keeps the right. Only the placement changes: the base
widget's state, crossfades, personality flash and override blink are untouched; it still hides under
alerts and the set-speed readout and only exists while engaged with longitudinal control.
Geometry on the 536 x 240 mici view: column centred 46 px from the content area's right edge (mirrors
upstream's 46 px from the left), stack shifted down 60 px so the car sits at y 160 and the bars at
182 / 196 / 212, i.e. the stack ends ~22 px above the bottom edge.
"""
import pyray as rl

from openpilot.selfdrive.ui.mici.onroad.long_indicator import LongIndicator

_X_FROM_RIGHT = 46   # upstream centres on rect.x + 46; mirror it from the right edge
_Y_SHIFT = 60        # car 100 -> 160, bars 122/136/152 -> 182/196/212


class LongIndicatorSP(LongIndicator):
  def _draw_centered(self, texture: rl.Texture, rect: rl.Rectangle, y: float, alpha: float) -> None:  # type: ignore[override]
    # the base class calls self._draw_centered(texture, rect, y, alpha) for the car and every bar,
    # so overriding the one placement helper moves the whole stack
    pos = rl.Vector2(rect.x + rect.width - _X_FROM_RIGHT - texture.width / 2, rect.y + y + _Y_SHIFT - texture.height / 2)
    rl.draw_texture_ex(texture, pos, 0.0, 1.0, rl.Color(255, 255, 255, round(255 * alpha)))
