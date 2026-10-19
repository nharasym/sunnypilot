"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import math

import pyray as rl

from openpilot.selfdrive.ui.mici.layouts.home import MiciHomeLayout
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.startup_phases import HOME_BADGE_STYLE, HOME_BADGE_TEXT
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.hihy_release import get_hihy_release  # HL-FEAT(release-stamp)
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.label import UnifiedLabel

# HL-FEAT(egpu-badge-home): geometry of the drawn eGPU pill in the home status bar. 40 px tall like
# the chestnut PNGs it replaces (IconWidget(..., (54|68, 40)) in mici/layouts/home.py), bold text
# sized so the letters fill the pill the way the PNG's did.
_PILL_H = 40
_PILL_FONT = 26
_PILL_PAD_X = 10
_PILL_ROUNDNESS = 0.4
_BANG_GAP = 6


class EgpuBadge(Widget):
  """The home screen's eGPU status as a drawn pill: white breathing while the big model loads,
  green when it is ready/active, orange with a bang when it failed to compile or load."""

  def __init__(self):
    super().__init__()
    self._font = gui_app.font(FontWeight.BOLD)
    self._text_w = measure_text_cached(self._font, HOME_BADGE_TEXT, _PILL_FONT).x
    self._bang_w = measure_text_cached(self._font, "!", _PILL_FONT).x
    self._style: tuple[tuple[int, int, int], tuple[int, int, int], bool, bool] | None = None
    self.set_enabled(False)
    self.set_state(None)

  @property
  def pill_w(self) -> float:
    return self._text_w + 2 * _PILL_PAD_X

  def set_state(self, state: str | None) -> None:
    # state is a ui_state.ChestnutState value, or None to hide (usb state unknown)
    style = HOME_BADGE_STYLE.get(state) if state is not None else None
    self._style = style
    self.set_visible(style is not None)
    # the HBoxLayout reads rect.width BEFORE rendering a child, so the width must be right here
    w = self.pill_w + (_BANG_GAP + self._bang_w if style is not None and style[3] else 0)
    if w != self._rect.width or self._rect.height != _PILL_H:
      self.set_rect(rl.Rectangle(self._rect.x, self._rect.y, w, _PILL_H))

  def _render(self, _) -> None:
    if self._style is None:
      return
    (bg_r, bg_g, bg_b), (fg_r, fg_g, fg_b), breathe, bang = self._style
    a = int(255 * (0.35 + 0.65 * (0.5 - 0.5 * math.cos(rl.get_time() * 6.0)))) if breathe else 255
    x, y = self._rect.x, self._rect.y
    text_y = y + (_PILL_H - _PILL_FONT) / 2
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, self.pill_w, _PILL_H), _PILL_ROUNDNESS, 8, rl.Color(bg_r, bg_g, bg_b, a))
    rl.draw_text_ex(self._font, HOME_BADGE_TEXT, rl.Vector2(x + _PILL_PAD_X, text_y), _PILL_FONT, 0, rl.Color(fg_r, fg_g, fg_b, a))
    if bang:
      rl.draw_text_ex(self._font, "!", rl.Vector2(x + self.pill_w + _BANG_GAP, text_y), _PILL_FONT, 0, rl.Color(bg_r, bg_g, bg_b, a))


class MiciHomeLayoutSP(MiciHomeLayout):
  def __init__(self):
    super().__init__()
    self._openpilot_label = UnifiedLabel("sunnypilot", font_size=88, font_weight=FontWeight.AUDIOWIDE, max_width=480, wrap_text=False)

    # HL-FEAT(egpu-badge-home): upstream's three chestnut PNGs ("GPU") leave the status bar and the
    # drawn eGPU pill takes their slot. Registered through _child like any inline child, then moved
    # into place so the bar order (settings, network, usb, eGPU, body, mic) is unchanged.
    self._egpu_badge = self._status_bar_layout._child(EgpuBadge())
    children = self._status_bar_layout.widgets
    slot = children.index(self._chestnut_icon)
    for icon in (self._chestnut_icon, self._chestnut_loading_icon, self._chestnut_failed_icon):
      icon.set_visible(False)
      children.remove(icon)
    children.remove(self._egpu_badge)
    children.insert(slot, self._egpu_badge)

  def _get_version_text(self) -> tuple[str, str, str, str] | None:
    # HL-FEAT(release-stamp): "2026.003.000 r23" -- the fork's generation after the version.
    # Everything else (branch, commit, date) is upstream's tuple untouched.
    text = super()._get_version_text()
    if text is None:
      return None
    tag = get_hihy_release()
    version, branch, commit, date_str = text
    return (f"{version} {tag}" if tag else version), branch, commit, date_str

  def _set_chestnut_visibility(self):
    # called from upstream's _render under gui_app.sunnypilot_ui(); the PNG icons are gone, the
    # pill shows the same states they did (hidden while the usb state is unknown or disconnected)
    usb_unknown = ui_state.usb_unknown
    self._usb_icon.set_visible(ui_state.usb_connected and usb_unknown)
    self._egpu_badge.set_state(None if usb_unknown else ui_state.chestnut_state.value)
