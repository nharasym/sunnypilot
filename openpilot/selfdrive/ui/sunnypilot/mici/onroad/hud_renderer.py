"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pyray as rl

from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.blind_spot_indicators import BlindSpotIndicators
from openpilot.system.ui.lib.application import gui_app

# HL-FEAT(egpu-icon-persist): match the DMoji's visual weight. The mici DMoji is a 60x60
# widget at (rect.x+16, rect.y+10) whose glyph renders at 52px (mici/onroad/driver_state.py
# BASE_SIZE=60, cone_and_person_size=52), so its centerline sits at rect.y+40. Upstream's
# eGPU icons are 60x44 (crossed 60x52); scaling heights by 52/44 gives the same glyph
# height as the DMoji.
_EGPU_SCALE = 52 / 44


class _AlwaysVisible:
  """HL-FEAT(egpu-icon-persist): drop-in for the eGPU icon's FirstOrderFilter alpha gate.

  Upstream shows the onroad eGPU status icon only while loading or for ~2.5s after a
  state change, then fades it out (hud_renderer._draw_model_source). Replacing the
  filter with a constant keeps the icon permanently on screen in its state color
  (green=big model active, pulsing white=loading, orange=trouble, crossed=engaged on
  the small model) while leaving ALL of upstream's state logic untouched — the icon
  still disappears entirely when no chestnut is attached (the usbgpu gate runs first).
  """
  x = 1.0

  def update(self, _):
    return 1.0


class HudRendererSP(HudRenderer):
  def __init__(self):
    super().__init__()
    self.blind_spot_indicators = BlindSpotIndicators()
    self._egpu_alpha_filter = _AlwaysVisible()  # HL-FEAT(egpu-icon-persist)
    # HL-FEAT(egpu-icon-persist): DMoji-sized textures (see _EGPU_SCALE derivation above);
    # upstream's position math reads icon.width/height so it self-adjusts to these
    self._txt_egpu = gui_app.texture('icons_mici/egpu.png', round(60 * _EGPU_SCALE), 52)
    self._txt_egpu_green = gui_app.texture('icons_mici/egpu_green.png', round(60 * _EGPU_SCALE), 52)
    self._txt_egpu_orange = gui_app.texture('icons_mici/egpu_orange.png', round(60 * _EGPU_SCALE), 52)
    self._txt_egpu_crossed = gui_app.texture('icons_mici/egpu_crossed.png', round(60 * _EGPU_SCALE), round(52 * _EGPU_SCALE))

  def _update_state(self) -> None:
    super()._update_state()
    self.blind_spot_indicators.update()

  def _draw_model_source(self, rect: rl.Rectangle) -> None:
    # HL-FEAT(egpu-icon-persist): relocate the now-permanent eGPU icon to the TOP of the view,
    # vertically centered on the DMoji's centerline (rect.y + 40). Upstream bottom-anchors it:
    #   pos.y = rect.y + H - 14 - (wheel_h + icon_h) / 2      (wheel_h = 50)
    # and uses rect for nothing else in the method (verified at mici/onroad/hud_renderer.py:199-232).
    # Solving for the icon's center at y+40 with the 52px icons above:
    #   H = 40 - icon_h/2 + 14 + (50 + icon_h)/2 = 40 + 14 + 25 = 79
    # (icon_h cancels, so the taller crossed icon stays centered too.)
    super()._draw_model_source(rl.Rectangle(rect.x, rect.y, rect.width, 79))

  def _render(self, rect: rl.Rectangle) -> None:
    super()._render(rect)
    self.blind_spot_indicators.render(rect)

  def _has_blind_spot_detected(self) -> bool:

    return self.blind_spot_indicators.detected
