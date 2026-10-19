"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

HL-FEAT(startup-rail): a strip across the TOP of the mici onroad view (r47; r32-r46 used the right
side panel with rotated text, which read badly from the seat) that lights one rounded segment per boot step
(processes, model loads, then micd and soundd) left to right and names the step in progress in plain
horizontal text under the bars. Turns green with a bigger "ready" once the models are live and the speaker is
open, then turns the drum through the loaded model names tagged "big"/"small", then fades. Amber for a failed
big model or a dead process. Shown without the eGPU dock too, with the small model as the long step.
State logic lives in startup_phases.py (pure, tested); this file only reads ui_state and draws.
Geometry: the strip sits between the DMoji (top-left, ~52 px) and the GPU badge + temps column (top-right),
inside the content area (left of the 60 px ball panel). It hides while an alert is up (alerts own the top
band) and is only visible while DISENGAGED, so it never meets the set-speed readout or the long indicator.
"""
import math
import pyray as rl

from openpilot.cereal import log
from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.mici.onroad import SIDE_PANEL_WIDTH
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.startup_phases import (
  STEPS, STEPS_NO_DOCK, TIMED_STEPS, DeadProcessTracker, RailInputs, RailMode, RailState, StartupPhases,
  audio_streams_running,
)
from openpilot.selfdrive.ui.ui_state import ChestnutState, UIStatus, ui_state
from openpilot.system.ui.lib.application import FontWeight, gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

EventName = log.OnroadEvent.EventName

_LABEL_SIZE = 26           # road test 2026-10-07: 18 px was unreadable from the driver's seat
_READY_SIZE = 30
_LABEL_SLIDE_S = 0.3            # the drum: old label slides left and out, new one slides in from the right
_LABEL_SLIDE_PX = 40
# strip geometry (536 x 240 mici view): clear of the DMoji on the left and of the GPU badge + temps on the right
# r49: centred in the band between the DMoji and the GPU badge. The face spans x 26..78 (renderer at 16/10 plus
# its own inset); the badge's left edge is at right - 10 - ~58. The temps sit BELOW the badge, not beside the
# strip, so only the badge needs clearing: 90..(content - 80) is symmetric about that band's centre.
_STRIP_X0 = 90             # 12 px right of the DMoji's edge
_BADGE_RESERVE = 96        # 'eGPU' badge (~72 px) + its 10 px right margin + ~14 px gap
_BAR_Y = 8                 # segment row: top edge, height, gap between segments
_BAR_H = 10
_BAR_GAP = 4
_LABEL_Y = 22              # label top; ~26 px tall, so the strip ends well above the long indicator (y 100+)
_AUDIO_POLL_S = 0.5        # /proc/asound reads while the audio steps are still dark; none after both are seen

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
    self._audio = (False, False)
    self._audio_poll_t = 0.0

  # ---- inputs ----

  def _new_session(self) -> None:
    self._phases.reset()
    self._dead.reset()
    self._dead_name = None
    self._procs_ok = frozenset()
    self._model_names = None
    self._audio = (False, False)
    self._audio_poll_t = 0.0
    self._alpha.x = 0.0
    self._label = self._prev_label = ""
    self._label_t = 0.0

  @staticmethod
  def _read_model_names(dock: bool = True) -> tuple[str, str]:
    """(big, small) display names of the active bundles, "" for a slot with no bundle: the
    position is what the drum tags "big"/"small", so a missing big model must not promote the
    small one. Without the dock the big slot stays empty: the selected big bundle is not running.
    Read once per session at the first model frame (two small param reads, never per frame);
    never raises inside the render loop."""
    names = ["", ""]
    for slot, chestnut in enumerate((True, False)):
      if chestnut and not dock:
        continue
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
    if model_seen and (big or not ui_state.chestnut_present) and self._model_names is None:
      # a big frame proves the big model runs even if the dock enumerated after the onroad edge
      self._model_names = self._read_model_names(ui_state.chestnut_present or big)
    if not all(self._audio) and t - self._audio_poll_t >= _AUDIO_POLL_S:
      self._audio_poll_t = t
      mic, spk = audio_streams_running()
      self._audio = (self._audio[0] or mic, self._audio[1] or spk)
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
      mic_running=self._audio[0],
      speaker_running=self._audio[1],
    )

  def _demo_state(self, t: float) -> RailState:
    # layout iteration on the device without a car: walk every look of both step lists, 2 s each
    looks = []
    for steps in (STEPS, STEPS_NO_DOCK):
      n = len(steps)
      looks += [RailState(RailMode.STARTING, k, label, (t % 2.0) * 9 if key in TIMED_STEPS[id(steps)] else None, True, n)
                for k, (key, label) in enumerate(steps)]
      looks += [RailState(RailMode.READY, n, "ready", None, True, n)]
    n = len(STEPS)
    looks += [RailState(RailMode.READY, n, "big Cinque Terre V2", None, True, n),     # the real drum's labels, tag included,
              RailState(RailMode.READY, n, "small CD210", None, True, n),            # so the fit-to-panel shrink is on show
              RailState(RailMode.FAULT, n, "dmonitoringmodeld not running", None, True, n),
              RailState(RailMode.FAILED, n, "big model failed", None, True, n)]
    return looks[int(t / 2.0) % len(looks)]

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
    # alerts own the top band (the mici alert layer renders before the HUD); the state keeps ticking underneath
    if not self._demo and ui_state.sm['selfdriveState'].alertSize != 0:
      return

    t = rl.get_time()
    content_w = rect.width - SIDE_PANEL_WIDTH
    x0 = rect.x + _STRIP_X0
    strip_w = max(80.0, content_w - _STRIP_X0 - _BADGE_RESERVE)
    mode = self._state.mode
    lit_color = {RailMode.READY: _COLOR_READY, RailMode.FAULT: _COLOR_FAULT, RailMode.FAILED: _COLOR_FAULT}.get(mode, _COLOR_LIT)

    # segments, left to right
    segments = max(1, self._state.segments)
    seg_w = (strip_w - (segments - 1) * _BAR_GAP) / segments
    pulse = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(t * 6.0))
    for n in range(segments):
      seg = rl.Rectangle(x0 + n * (seg_w + _BAR_GAP), rect.y + _BAR_Y, seg_w, _BAR_H)
      if n < self._state.lit:
        color = _with_alpha(lit_color, alpha)
      elif n == self._state.lit and mode == RailMode.STARTING:
        color = _with_alpha(_COLOR_LIT, alpha * pulse)       # the step in progress breathes
      else:
        color = _with_alpha(_COLOR_UNLIT, alpha)
      rl.draw_rectangle_rounded(seg, 0.5, 6, color)

    # label, plain horizontal text under the bars
    if mode == RailMode.READY:
      size, color = _READY_SIZE, _COLOR_READY
    elif mode in (RailMode.FAULT, RailMode.FAILED):
      size, color = _LABEL_SIZE, _COLOR_FAULT
    else:
      size, color = _LABEL_SIZE, _COLOR_LIT
    text = self._label
    if self._state.elapsed_s is not None:
      text = f"{text} {int(self._state.elapsed_s)}s"      # the counter ticks without turning the drum
    y = rect.y + _LABEL_Y
    p = min(1.0, (t - self._label_t) / _LABEL_SLIDE_S) if self._label_t else 1.0
    self._draw_label(text, x0, y, strip_w, size, _with_alpha(color, alpha * p), (1.0 - p) * _LABEL_SLIDE_PX)
    if p < 1.0 and self._prev_label:
      self._draw_label(self._prev_label, x0, y, strip_w, _LABEL_SIZE, _with_alpha(_COLOR_LIT, alpha * (1.0 - p)), -p * _LABEL_SLIDE_PX)

  def _draw_label(self, text: str, x: float, y: float, max_w: float, size: int, color: rl.Color, offset: float) -> None:
    if not text:
      return
    # draw_text_ex is the FONT_SCALE-patched call and measure_text_cached applies the same scale, so the
    # measured width is the on-screen width. Shrink the size to fit the strip rather than clipping a name.
    w = measure_text_cached(self._font, text, size).x
    if w > max_w:
      size = max(14, int(size * max_w / w))
      w = measure_text_cached(self._font, text, size).x
    # centred under the bars (r48); the drum: a positive offset enters from the right, a negative one leaves left
    rl.draw_text_ex(self._font, text, rl.Vector2(x + (max_w - w) / 2 + offset, y), size, 0, color)
