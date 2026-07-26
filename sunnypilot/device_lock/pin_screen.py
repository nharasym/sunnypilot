"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): shared PIN-entry screen.
#
# Base for both the lock screen (LockedOverlay) and the set-PIN flow (LockSetupDialog) so the
# keypad exists once. Sizing is proportional to the given rect, so one implementation serves
# both the big UI (2160x1080) and mici (536x240).

import pyray as rl

from openpilot.system.ui.lib.application import FontWeight
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import Button, ButtonStyle
from openpilot.system.ui.widgets.label import gui_label
from openpilot.sunnypilot.device_lock.constants import PIN_MAX_LENGTH, PIN_MIN_LENGTH

BG_COLOR = rl.Color(20, 20, 20, 255)
TITLE_COLOR = rl.Color(255, 255, 255, 255)
BODY_COLOR = rl.Color(170, 170, 170, 255)
ERROR_COLOR = rl.Color(226, 44, 44, 255)
DOT_COLOR = rl.Color(255, 255, 255, 255)
DOT_EMPTY_COLOR = rl.Color(90, 90, 90, 255)

KEYPAD = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "clear", "0", "enter"]


class PinScreen(Widget):
  """Full-screen numeric PIN entry. Subclasses override title/subtitle/on_submit."""

  def __init__(self, cancel_key: str | None = None, on_cancel=None):
    super().__init__()
    self._entry = ""
    self._error = ""
    self._on_cancel = on_cancel
    self._buttons: dict[str, Button] = {}
    for key in KEYPAD:
      label = {"clear": "⌫", "enter": "✓"}.get(key, key)
      style = ButtonStyle.PRIMARY if key == "enter" else ButtonStyle.NORMAL
      self._buttons[key] = Button(label, lambda k=key: self._on_key(k), button_style=style)

  # --- subclass hooks ---

  def title(self) -> str:
    raise NotImplementedError

  def subtitle(self) -> tuple[str, rl.Color]:
    """Returns (message, colour) shown under the title."""
    return "", BODY_COLOR

  def on_submit(self, pin: str) -> None:
    raise NotImplementedError

  def input_enabled(self) -> bool:
    return True

  def before_render(self) -> None:
    """Called each frame before drawing."""

  # --- entry state (for subclasses) ---

  @property
  def entry(self) -> str:
    return self._entry

  def clear_entry(self) -> None:
    self._entry = ""

  def set_error(self, msg: str) -> None:
    self._error = msg

  # --- input ---

  def _on_key(self, key: str) -> None:
    self._error = ""
    if key == "clear":
      self._entry = self._entry[:-1]
    elif key == "enter":
      if len(self._entry) < PIN_MIN_LENGTH:
        self._error = f"PIN must be at least {PIN_MIN_LENGTH} digits"
        return
      pin, self._entry = self._entry, ""
      self.on_submit(pin)
    elif len(self._entry) < PIN_MAX_LENGTH:
      self._entry += key

  # --- render ---

  def _render(self, rect: rl.Rectangle) -> None:
    self.before_render()

    rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height), BG_COLOR)

    pad = rect.height * 0.05
    title_h = rect.height * 0.14
    body_h = rect.height * 0.10
    dots_h = rect.height * 0.12

    y = rect.y + pad
    gui_label(rl.Rectangle(rect.x, y, rect.width, title_h), self.title(),
              font_size=int(title_h * 0.72), color=TITLE_COLOR, font_weight=FontWeight.BOLD,
              alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER)
    y += title_h

    msg, color = (self._error, ERROR_COLOR) if self._error else self.subtitle()
    gui_label(rl.Rectangle(rect.x, y, rect.width, body_h), msg,
              font_size=int(body_h * 0.60), color=color,
              alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER)
    y += body_h

    self._render_dots(rl.Rectangle(rect.x, y, rect.width, dots_h))
    y += dots_h

    self._render_keypad(rl.Rectangle(rect.x + pad, y, rect.width - 2 * pad,
                                     rect.y + rect.height - y - pad))

  def _render_dots(self, rect: rl.Rectangle) -> None:
    """Masked entry indicator - one dot per entered digit, never the digits themselves."""
    shown = max(len(self._entry), PIN_MIN_LENGTH)
    radius = min(rect.height * 0.22, rect.width / (shown * 4))
    gap = radius * 3
    cx = rect.x + rect.width / 2 - (gap * (shown - 1)) / 2
    cy = rect.y + rect.height / 2
    for i in range(shown):
      rl.draw_circle(int(cx + i * gap), int(cy), radius,
                     DOT_COLOR if i < len(self._entry) else DOT_EMPTY_COLOR)

  def _render_keypad(self, rect: rl.Rectangle) -> None:
    cols, rows = 3, 4
    gap = min(rect.width, rect.height) * 0.04
    bw = (rect.width - gap * (cols - 1)) / cols
    bh = (rect.height - gap * (rows - 1)) / rows
    enabled = self.input_enabled()

    for idx, key in enumerate(KEYPAD):
      r, c = divmod(idx, cols)
      btn = self._buttons[key]
      btn.set_enabled(enabled)
      btn.render(rl.Rectangle(rect.x + c * (bw + gap), rect.y + r * (bh + gap), bw, bh))
