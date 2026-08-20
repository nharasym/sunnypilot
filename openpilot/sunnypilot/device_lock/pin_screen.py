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
#
# The keypad is four controller-style symbols (cross / circle / triangle / square) rather than
# digits: six large targets in a 3x2 grid instead of twelve cramped ones, which matters a lot on
# mici. Glyphs are drawn with raylib primitives, not font characters - crisp at any size and no
# dependency on the bundled font covering the codepoints.

import pyray as rl

from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.widgets import Widget
from openpilot.system.ui.widgets.button import DEFAULT_BUTTON_FONT_SIZE, Button, ButtonStyle
from openpilot.system.ui.widgets.label import gui_label
from openpilot.sunnypilot.device_lock.constants import PIN_ALPHABET, PIN_MAX_LENGTH, PIN_MIN_LENGTH

BG_COLOR = rl.Color(20, 20, 20, 255)
TITLE_COLOR = rl.Color(255, 255, 255, 255)
BODY_COLOR = rl.Color(170, 170, 170, 255)
ERROR_COLOR = rl.Color(226, 44, 44, 255)
DOT_COLOR = rl.Color(255, 255, 255, 255)
DOT_EMPTY_COLOR = rl.Color(90, 90, 90, 255)

# Controller-style colours - they make a remembered pattern easier to recall and to distinguish
# at a glance on a small screen.
SYMBOL_COLORS = {
  "0": rl.Color(122, 170, 255, 255),  # cross    - blue
  "1": rl.Color(240, 110, 120, 255),  # circle   - red
  "2": rl.Color(120, 216, 160, 255),  # triangle - green
  "3": rl.Color(224, 150, 210, 255),  # square   - pink
}

CLEAR_KEY = "clear"
ENTER_KEY = "enter"
# 3x2 grid: the four symbols, then backspace + confirm
KEYPAD = ["0", "1", "2", ENTER_KEY, "3", CLEAR_KEY]
KEYPAD_COLS = 3
KEYPAD_ROWS = 2


def draw_symbol(key: str, rect: rl.Rectangle, thickness: float) -> None:
  """Draw one controller glyph centred in rect, using primitives (no font dependency)."""
  cx, cy = rect.x + rect.width / 2, rect.y + rect.height / 2
  r = min(rect.width, rect.height) / 2
  color = SYMBOL_COLORS[key]

  if key == "0":  # cross
    d = r * 0.70
    rl.draw_line_ex(rl.Vector2(cx - d, cy - d), rl.Vector2(cx + d, cy + d), thickness, color)
    rl.draw_line_ex(rl.Vector2(cx - d, cy + d), rl.Vector2(cx + d, cy - d), thickness, color)
  elif key == "1":  # circle
    rl.draw_ring(rl.Vector2(cx, cy), r - thickness, r, 0, 360, 64, color)
  elif key == "2":  # triangle
    top = rl.Vector2(cx, cy - r)
    left = rl.Vector2(cx - r * 0.92, cy + r * 0.72)
    right = rl.Vector2(cx + r * 0.92, cy + r * 0.72)
    for a, b in ((top, right), (right, left), (left, top)):
      rl.draw_line_ex(a, b, thickness, color)
  elif key == "3":  # square
    s = r * 1.45
    rl.draw_rectangle_lines_ex(rl.Rectangle(cx - s / 2, cy - s / 2, s, s), thickness, color)


class PinScreen(Widget):
  """Full-screen symbol PIN entry. Subclasses override title/subtitle/on_submit."""

  def __init__(self):
    super().__init__()
    self._entry = ""
    self._error = ""
    # Button bakes its font size in at construction (no setter), and the 60px default overruns
    # the small mici keys - "back" ran outside its box. Scale off the screen height so one
    # implementation still fits both mici (240 -> ~20) and the big UI (1080 -> capped at default).
    label_font = max(14, min(DEFAULT_BUTTON_FONT_SIZE, int(gui_app.height * 0.085)))

    self._buttons: dict[str, Button] = {}
    for key in KEYPAD:
      # symbol keys draw their glyph on top of an empty button, so Button keeps its own
      # press/touch handling and we only add the artwork
      label = {CLEAR_KEY: "back", ENTER_KEY: "OK"}.get(key, "")
      style = ButtonStyle.PRIMARY if key == ENTER_KEY else ButtonStyle.NORMAL
      self._buttons[key] = Button(label, lambda k=key: self._on_key(k), button_style=style,
                                  font_size=label_font)

  # --- subclass hooks ---

  def title(self) -> str:
    raise NotImplementedError

  def subtitle(self) -> tuple[str, rl.Color]:
    return "", BODY_COLOR

  def on_submit(self, pin: str) -> None:
    raise NotImplementedError

  def on_back_empty(self) -> None:
    """Back pressed with nothing entered.

    Default is deliberately a no-op: the LOCK SCREEN must never be dismissible, that is the whole
    point of it. Only the setup flow overrides this to back out.
    """

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
    if key == CLEAR_KEY:
      if not self._entry:
        # nothing left to delete - let the screen decide whether back means "leave"
        self.on_back_empty()
        return
      self._entry = self._entry[:-1]
    elif key == ENTER_KEY:
      if len(self._entry) < PIN_MIN_LENGTH:
        self._error = f"PIN must be at least {PIN_MIN_LENGTH} symbols"
        return
      pin, self._entry = self._entry, ""
      self.on_submit(pin)
    elif len(self._entry) < PIN_MAX_LENGTH:
      self._entry += key

  # --- render ---

  def _render(self, rect: rl.Rectangle) -> None:
    self.before_render()

    rl.draw_rectangle(int(rect.x), int(rect.y), int(rect.width), int(rect.height), BG_COLOR)

    pad = rect.height * 0.04
    title_h = rect.height * 0.13
    body_h = rect.height * 0.09
    dots_h = rect.height * 0.13

    y = rect.y + pad
    gui_label(rl.Rectangle(rect.x, y, rect.width, title_h), self.title(),
              font_size=int(title_h * 0.70), color=TITLE_COLOR, font_weight=FontWeight.BOLD,
              alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER)
    y += title_h

    msg, color = (self._error, ERROR_COLOR) if self._error else self.subtitle()
    gui_label(rl.Rectangle(rect.x, y, rect.width, body_h), msg,
              font_size=int(body_h * 0.62), color=color,
              alignment=rl.GuiTextAlignment.TEXT_ALIGN_CENTER)
    y += body_h

    self._render_entry(rl.Rectangle(rect.x, y, rect.width, dots_h))
    y += dots_h

    self._render_keypad(rl.Rectangle(rect.x + pad, y, rect.width - 2 * pad,
                                     rect.y + rect.height - y - pad))

  def _render_entry(self, rect: rl.Rectangle) -> None:
    """Show the entered symbols as small glyphs, with placeholders for the remaining slots.

    Unlike a numeric PIN this is not masked: the symbols ARE the secret, but showing them is
    what makes a 4-symbol pattern usable, and the threat model is casual misuse rather than
    shoulder-surfing.
    """
    slots = max(len(self._entry), PIN_MIN_LENGTH)
    size = min(rect.height, rect.width / (slots * 1.8))
    gap = size * 1.6
    x = rect.x + rect.width / 2 - (gap * (slots - 1)) / 2
    cy = rect.y + rect.height / 2
    thickness = max(2.0, size * 0.10)

    for i in range(slots):
      cell = rl.Rectangle(x + i * gap - size / 2, cy - size / 2, size, size)
      if i < len(self._entry):
        draw_symbol(self._entry[i], cell, thickness)
      else:
        rl.draw_circle(int(cell.x + size / 2), int(cy), max(2.0, size * 0.10), DOT_EMPTY_COLOR)

  def _render_keypad(self, rect: rl.Rectangle) -> None:
    gap = min(rect.width, rect.height) * 0.05
    bw = (rect.width - gap * (KEYPAD_COLS - 1)) / KEYPAD_COLS
    bh = (rect.height - gap * (KEYPAD_ROWS - 1)) / KEYPAD_ROWS
    enabled = self.input_enabled()

    for idx, key in enumerate(KEYPAD):
      r, c = divmod(idx, KEYPAD_COLS)
      btn_rect = rl.Rectangle(rect.x + c * (bw + gap), rect.y + r * (bh + gap), bw, bh)
      btn = self._buttons[key]
      btn.set_enabled(enabled)
      btn.render(btn_rect)

      if key in PIN_ALPHABET:
        # glyph inset inside the button face
        inset = min(bw, bh) * 0.28
        glyph = rl.Rectangle(btn_rect.x + inset, btn_rect.y + inset,
                             bw - 2 * inset, bh - 2 * inset)
        draw_symbol(key, glyph, max(3.0, min(bw, bh) * 0.06))
