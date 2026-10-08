"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

HL-FEAT(startup-rail): a vertical strip in the right side panel of the mici onroad view that
lights one rounded segment per boot step (processes and model loads) and names the step in progress beside it,
rotated to read top-to-bottom. Turns green with a bigger "ready" once the big model is live on
the eGPU, then turns the drum through the loaded model names tagged "big"/"small", then fades. Amber for a failed big model or a dead process.
State logic lives in startup_phases.py (pure, tested); this file only reads ui_state and draws.

Draws only inside the 60 px side panel (outside the camera scissor), so it can never cover an
alert. Hidden while the confidence ball owns the panel (anything but DISENGAGED). The bookmark
icon, if swiped in during boot, simply draws on top of it.
"""
import math
import pyray as rl

from openpilot.cereal import log
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad import SIDE_PANEL_WIDTH
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.startup_phases import (
  SEGMENTS, LABELS, BIG_MODEL_STEP, DeadProcessTracker, RailInputs, RailMode, RailState, StartupPhases,
)
from openpilot.selfdrive.ui.ui_state import ChestnutState, UIStatus, ui_state
from openpilot.system.ui.lib.application import FONT_SCALE, FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

EventName = log.OnroadEvent.EventName

_LABEL_SIZE = 26           # road test 2026-10-07: 18 px was unreadable from the driver's seat
_READY_SIZE = 30
_LABEL_SLIDE_S = 0.3            # the drum: old label slides up and out, new one slides in from below
_LABEL_SLIDE_PX = 40
_COL_W = 14                # narrower bars buy the label a 36 px band (60 - 6 - 14 - 4)
_GUTTER = 6
_MARGIN_Y = 12
_GAP = 6
# text band is [panel.x + _BAND_X, col_x); the rotated glyph box is size * FONT_SCALE wide
_BAND_X = 4

_COLOR_UNLIT = rl.Color(50, 50, 50, 255)
_COLOR_LIT = rl.Color(255, 255, 255, 255)
_COLOR_READY = rl.Color(0, 255, 38, 255)        # the confidence ball's green
_COLOR_FAULT = rl.Color(255, 175, 3, 255)       # the amber our temps already use


def _with_alpha(c: rl.Color, a: float) -> rl.Color:
  return rl.Color(c.r, c.g, c.b, int(c.a * max(0.0, min(1.0, a))))


class StartupRail(Widget):
  def __init__(self, demo: bool = False):
    super().__init__()
    self._demo = demo
    self._phases = StartupPhases()
    self._dead = DeadProcessTracker()
    self._dead_name: str | None = None
    self._procs_ok: frozenset[str] = frozenset()
    self._model_names: tuple[str, ...] | None = None
    self._state = RailState(RailMode.HIDDEN, 0, "", None, False)
    self._alpha = FirstOrderFilter(0.0, 0.1, 1 / gui_app.target_fps)
    self._font = gui_app.font(FontWeight.SEMI_BOLD)
    self._label = ""
    self._prev_label = ""
    self._label_t = 0.0
    self._started_frame_seen = -1

  # ---- inputs ----

  def _new_session(self) -> None:
    self._phases.reset()
    self._dead.reset()
    self._dead_name = None
    self._procs_ok = frozenset()
    self._model_names = None
    self._alpha.x = 0.0
    self._label = self._prev_label = ""
    self._label_t = 0.0

  @staticmethod
  def _read_model_names() -> tuple[str, str]:
    """(big, small) display names of the active bundles, "" for a slot with no bundle: the
    position is what the drum tags "big"/"small", so a missing big model must not promote the
    small one. Read once per session at the ready edge (two small param reads, never per
    frame); never raises inside the render loop."""
    names = ["", ""]
    for slot, chestnut in enumerate((True, False)):
      try:
        from openpilot.sunnypilot.models.helpers import get_active_bundle
        b = get_active_bundle(ui_state.params, chestnut=chestnut)
        if b is not None and b.displayName:
          names[slot] = str(b.displayName)
      except Exception:
        pass
    return names[0], names[1]

  def _inputs(self, t: float) -> RailInputs:
    sm = ui_state.sm
    sf = ui_state.started_frame
    rf = sm.recv_frame
    if sm.updated['managerState']:           # 2 Hz message; only rescan the process list when it changes
      if sm.alive['managerState'] and rf.get('managerState', 0) > sf:
        procs = sm['managerState'].processes
        missing = {p.name for p in procs if p.shouldBeRunning and not p.running}
        # a process listed as neither running nor expected was not scheduled this session (the manager
        # starts the whole onroad set in one pass before it publishes), so its step counts as done
        self._procs_ok = frozenset(p.name for p in procs if p.running or not p.shouldBeRunning)
      else:
        missing, self._procs_ok = set(), frozenset()
      self._dead_name = self._dead.update(missing, t)
    camera_seen = any(rf.get(s, 0) > sf for s in ("narrowRoadCameraState", "wideRoadCameraState"))
    # sm[...] keeps the last list ever received, so gate on this session like every other signal: a
    # stale bigModelLoading from an ignition cut mid-load would otherwise narrate the whole next boot wrong
    loading = ui_state.chestnut_loading or (rf.get('onroadEvents', 0) > sf and
                                            any(e.name == EventName.bigModelLoading for e in sm['onroadEvents']))
    model_seen = rf.get('modelV2', 0) > sf
    big = bool(sm['modelV2'].big)
    if model_seen and big and self._model_names is None:
      self._model_names = self._read_model_names()
    return RailInputs(
      t=t,
      started=ui_state.started,
      chestnut_present=ui_state.chestnut_present,
      camera_seen=camera_seen,
      selfdrive_seen=rf.get('selfdriveState', 0) > sf,
      big_model_loading=loading,
      chestnut_active=ui_state.chestnut_active,
      chestnut_failed=ui_state.chestnut_state == ChestnutState.FAILED,
      model_big_seen=model_seen and big,
      model_small_seen=model_seen and not big,
      chestnut_uncompiled=ui_state.chestnut_state == ChestnutState.UNCOMPILED,
      dead_process=self._dead_name,
      procs_ok=self._procs_ok,
      model_names=self._model_names or (),
    )

  def _demo_state(self, t: float) -> RailState:
    # layout iteration on the device without a car: walk every look, 2 s each
    steps = [RailState(RailMode.STARTING, n, LABELS[n], (t % 2.0) * 9 if n == BIG_MODEL_STEP else None, True) for n in range(SEGMENTS)]
    steps += [RailState(RailMode.READY, SEGMENTS, "ready", None, True),
              RailState(RailMode.READY, SEGMENTS, "big Cinque Terre V2", None, True),     # the real drum's labels, tag included,
              RailState(RailMode.READY, SEGMENTS, "small CD210", None, True),            # so the fit-to-panel shrink is on show
              RailState(RailMode.FAULT, SEGMENTS, "dmonitoringmodeld not running", None, True),
              RailState(RailMode.FAILED, SEGMENTS, "big model failed", None, True)]
    return steps[int(t / 2.0) % len(steps)]

  def _update_state(self) -> None:
    t = rl.get_time()
    if self._demo:
      self._state = self._demo_state(t)
    else:
      if ui_state.started_frame != self._started_frame_seen:
        self._new_session()               # a new onroad session: forget the last boot
        self._started_frame_seen = ui_state.started_frame
      self._state = self._phases.update(self._inputs(t))

    label = self._state.label
    if label != self._label:
      self._prev_label, self._label_t = self._label, t      # a real step change turns the drum
      self._label = label

  # ---- drawing ----

  def _render(self, rect: rl.Rectangle) -> None:
    if self._demo:
      visible = True
    else:
      visible = (self._state.visible and ui_state.started and ui_state.status == UIStatus.DISENGAGED)
    alpha = self._alpha.update(1.0 if visible else 0.0)
    if alpha < 1e-2:
      return

    t = rl.get_time()
    panel = rl.Rectangle(rect.x + rect.width - SIDE_PANEL_WIDTH, rect.y, SIDE_PANEL_WIDTH, rect.height)
    mode = self._state.mode
    lit_color = {RailMode.READY: _COLOR_READY, RailMode.FAULT: _COLOR_FAULT, RailMode.FAILED: _COLOR_FAULT}.get(mode, _COLOR_LIT)

    # segments, bottom to top
    col_x = panel.x + panel.width - _GUTTER - _COL_W
    seg_h = (panel.height - 2 * _MARGIN_Y - (SEGMENTS - 1) * _GAP) / SEGMENTS
    pulse = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(t * 6.0))
    for n in range(SEGMENTS):
      y = panel.y + panel.height - _MARGIN_Y - (n + 1) * seg_h - n * _GAP
      seg = rl.Rectangle(col_x, y, _COL_W, seg_h)
      if n < self._state.lit:
        color = _with_alpha(lit_color, alpha)
      elif n == self._state.lit and mode == RailMode.STARTING:
        color = _with_alpha(_COLOR_LIT, alpha * pulse)       # the step in progress breathes
      else:
        color = _with_alpha(_COLOR_UNLIT, alpha)
      rl.draw_rectangle_rounded(seg, 0.5, 6, color)

    # label, rotated to read top-to-bottom in the band left of the column
    if mode == RailMode.READY:
      size, color = _READY_SIZE, _COLOR_READY
    elif mode in (RailMode.FAULT, RailMode.FAILED):
      size, color = _LABEL_SIZE, _COLOR_FAULT
    else:
      size, color = _LABEL_SIZE, _COLOR_LIT
    text = self._label
    if self._state.elapsed_s is not None:
      text = f"{text} {int(self._state.elapsed_s)}s"      # the counter ticks without turning the drum
    band_w = col_x - (panel.x + _BAND_X)
    p = min(1.0, (t - self._label_t) / _LABEL_SLIDE_S) if self._label_t else 1.0
    self._draw_rotated(text, panel, band_w, size, _with_alpha(color, alpha * p), (1.0 - p) * _LABEL_SLIDE_PX)
    if p < 1.0 and self._prev_label:
      self._draw_rotated(self._prev_label, panel, band_w, _LABEL_SIZE, _with_alpha(_COLOR_LIT, alpha * (1.0 - p)), -p * _LABEL_SLIDE_PX)

  def _draw_rotated(self, text: str, panel: rl.Rectangle, band_w: float, size: int, color: rl.Color, offset: float) -> None:
    if not text:
      return
    # measure_text_cached already applies FONT_SCALE; draw_text_pro does NOT (only draw_text_ex is
    # patched), so the drawn size is scaled here. The text reads TOP-TO-BOTTOM (+90 deg): the driver
    # sits left of the device and tilts the head right, toward the screen, to read it (road test
    # 2026-10-07; the concept video's bottom-to-top direction needed a tilt away from the screen).
    # With +90 deg the glyph box extends to the LEFT of the anchor, so anchor at the band's right
    # edge; the measured width becomes the on-screen height. Shrink to fit the band and the panel.
    scale = min(1.0, band_w / (size * FONT_SCALE))
    w = measure_text_cached(self._font, text, size).x * scale
    avail = panel.height - 2 * _MARGIN_Y
    if w > avail:
      scale *= avail / w
      w = avail
    x = panel.x + _BAND_X + band_w - 2
    y = max(panel.y + 4, panel.y + (panel.height - w) / 2 - offset)   # the drum: a positive offset sits higher
    rl.draw_text_pro(self._font, text, rl.Vector2(x, y), rl.Vector2(0, 0), 90.0, size * FONT_SCALE * scale, 0, color)
