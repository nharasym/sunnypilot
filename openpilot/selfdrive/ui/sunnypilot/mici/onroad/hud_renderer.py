"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import math
import pyray as rl

from openpilot.cereal import log
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.startup_phases import BADGE_STYLE
from openpilot.selfdrive.ui.sunnypilot.onroad.blind_spot_indicators import BlindSpotIndicators
from openpilot.selfdrive.ui.ui_state import ChestnutState, ui_state
from openpilot.system.ui.lib.text_measure import measure_text_cached

ThermalStatus = log.DeviceState.ThermalStatus

# HL-FEAT(egpu-temp): GPU + CPU temp readout under the onroad GPU badge. The badge's
# color keeps meaning STATE (green=active, amber=uncompiled/failed, grey=loading) — the
# thermal zone is encoded in the temp TEXT color instead, so amber never becomes ambiguous
# ("hot" vs "failed").
# Layout (user-picked): a narrow right-aligned column under the badge — the GPU value first,
# under the badge it belongs to, CPU beneath. The right blind-spot indicator occupies this corner from
# rect.y+100 down (blind_spot_indicators.py BLIND_SPOT_Y_OFFSET) and renders AFTER the HUD,
# and the second line's ink reaches past y+100, so the CPU line is skipped while that
# indicator is showing — a whole number missing beats a half-covered one.
# Zones — GPU: amber 85 (early warning), red 100 = the point where upstream's chestnut
# status (system/hardware/chestnut/status.py) declares the card overheated and raises
# Offroad_ChestnutOverheated, so the HUD color agrees with the device's own alerting. CPU: no thresholds of our own — color straight off
# deviceState.thermalStatus, the verdict hardwared already publishes, so the number goes
# amber/red exactly when the device itself begins throttling and engagement gets gated.
# (Do NOT re-derive these from hardwared.THERMAL_BANDS: those min_temp values are the
# step-DOWN exits of a hysteresis machine, not entry points — reading them as entry
# thresholds paints red at 99C while thermalStatus is still ok and nothing is throttling.)
_TEMP_FONT_SIZE = 26
_TEMP_LINE_H = 28  # line advance; the scaled 26px box is ~30px tall, digits ink ~19px
_GPU_AMBER_C = 85
_GPU_RED_C = 100

# HL-FEAT(gpu-badge): the chestnut state used to be upstream's chestnut PNG, relocated to the
# top band and held permanently (formerly egpu-icon-persist, r17-r31). It is now a rounded word
# badge "GPU" in the same place, in the look Nick picked from a concept video on 2026-10-04:
# grey and breathing while the big model loads, green once it runs on the card, amber when
# uncompiled or failed. Same state source as before (ui_state.chestnut_state), same alert-band
# rule, same anchor (right edge, DMoji centreline); the badge is ~30 px tall against the icon's
# 37, so the temp lines under it sit ~4 px higher than before. Colours come from BADGE_STYLE in
# startup_phases.py as plain tuples: pyray's WHITE/BLACK are tuples without .r/.g/.b, and
# touching them that way kills the UI process on the first onroad frame.
_BADGE_FONT_SIZE = 22
_BADGE_PAD_X = 9
_BADGE_PAD_Y = 2


class HudRendererSP(HudRenderer):
  def __init__(self):
    super().__init__()
    self.blind_spot_indicators = BlindSpotIndicators()
    self._badge_h = 0.0  # HL-FEAT(gpu-badge): set when drawn; the temps hang off its bottom edge

  def _update_state(self) -> None:
    super()._update_state()
    self.blind_spot_indicators.update()

  def _draw_model_source(self, rect: rl.Rectangle) -> None:
    # HL-FEAT(gpu-badge): full override; upstream's SP renderer has none and upstream's base
    # draws the chestnut PNG bottom-right (mici/onroad/hud_renderer.py _draw_model_source) --
    # re-check that mapping at each port. Alerts own the top band: the mici alert layer puts its
    # turn-signal/blind-spot glyphs at top-right and renders BEFORE the HUD.
    if ui_state.sm['selfdriveState'].alertSize != 0:
      return
    if ui_state.sm.recv_frame['selfdriveState'] < ui_state.started_frame:
      return
    style = BADGE_STYLE.get(ui_state.chestnut_state.value)
    if style is None:            # DISCONNECTED / READY: nothing to show, like the icon before
      return
    (bg_r, bg_g, bg_b), (fg_r, fg_g, fg_b), breathe = style
    opacity = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(rl.get_time() * 6.0)) if breathe else 1.0
    a = int(255 * opacity)

    text = "GPU"
    size = measure_text_cached(self._font_bold, text, _BADGE_FONT_SIZE)
    badge_w, badge_h = size.x + 2 * _BADGE_PAD_X, size.y + 2 * _BADGE_PAD_Y
    self._badge_h = badge_h
    # vertically centred on the DMoji centreline (rect.y + 40), right edge at right - 10 like the icon was
    x, y = rect.x + rect.width - 10 - badge_w, rect.y + 40 - badge_h / 2
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, badge_w, badge_h), 0.45, 8, rl.Color(bg_r, bg_g, bg_b, a))
    rl.draw_text_ex(self._font_bold, text, rl.Vector2(x + _BADGE_PAD_X, y + _BADGE_PAD_Y), _BADGE_FONT_SIZE, 0,
                    rl.Color(fg_r, fg_g, fg_b, a))
    self._draw_temps(rect)

  def _draw_temps(self, rect: rl.Rectangle) -> None:
    # HL-FEAT(egpu-temp): GPU metrics only flow while the big model is actually running on
    # the card (modeld gates the SMU read on that), so gate on ACTIVE + a live publisher +
    # a real reading; tempC is 0 until the first SMU refresh. CPU rides the same gate so the
    # corner stays empty with no dock — deliberate, since the readout belongs to the badge.
    if ui_state.chestnut_state != ChestnutState.ACTIVE or not ui_state.sm.alive['chestnutState']:
      return
    gpu_temp = ui_state.sm['chestnutState'].tempC
    if gpu_temp <= 0:
      return

    # badge bottom edge is its centerline (rect.y+40) plus half its height; both lines
    # right-aligned on the badge's right edge. "GPU" stays on the line even under the badge so the
    # number says what it is (road test 2026-10-05: a bare "56°" read as unlabelled).
    right = rect.x + rect.width - 10
    y = rect.y + 40 + self._badge_h / 2 + 4
    gpu_color = self._temp_color(gpu_temp >= _GPU_AMBER_C, gpu_temp >= _GPU_RED_C)
    self._draw_temp(right, y, "GPU", gpu_temp, gpu_color)

    # hottest core, colored by the device's own verdict (see the zone note at the top).
    # Skipped while the right blind-spot indicator shows (same test it renders on, toggle
    # included — its filter rises with carState even when the toggle is off and nothing is
    # drawn; getattr so an upstream rename degrades to "no gate", not a crash).
    if not ui_state.sm.alive['deviceState']:
      return
    right_bsm = getattr(self.blind_spot_indicators, "_blind_spot_right_alpha_filter", None)
    if ui_state.blindspot and right_bsm is not None and right_bsm.x > 0.01:
      return
    cpu_temp = max(ui_state.sm['deviceState'].cpuTempC, default=0.0)
    if cpu_temp > 0:
      status = ui_state.sm['deviceState'].thermalStatus
      cpu_color = self._temp_color(status == ThermalStatus.overheated, status == ThermalStatus.critical)
      self._draw_temp(right, y + _TEMP_LINE_H, "CPU", cpu_temp, cpu_color)

  def _temp_color(self, hot: bool, critical: bool) -> rl.Color:
    if critical:
      return rl.Color(255, 66, 66, 230)
    return rl.Color(255, 175, 3, 230) if hot else rl.Color(255, 255, 255, 230)

  def _draw_temp(self, right_x: float, y: float, label: str, temp: float, color: rl.Color) -> float:
    """HL-FEAT(egpu-temp): draw '<label> NN°' right-aligned at right_x; returns its left edge."""
    label_text, value_text = (f"{label} " if label else ""), f"{round(temp)}°"
    label_w = measure_text_cached(self._font_semi_bold, label_text, _TEMP_FONT_SIZE).x if label_text else 0.0
    value_w = measure_text_cached(self._font_semi_bold, value_text, _TEMP_FONT_SIZE).x
    x = right_x - (label_w + value_w)
    # label stays dim so the eye lands on the numbers; only the value carries the thermal color
    if label_text:
      rl.draw_text_ex(self._font_semi_bold, label_text, rl.Vector2(x, y), _TEMP_FONT_SIZE, 0,
                      rl.Color(255, 255, 255, 190))
    rl.draw_text_ex(self._font_semi_bold, value_text, rl.Vector2(x + label_w, y), _TEMP_FONT_SIZE, 0, color)
    return x

  def _render(self, rect: rl.Rectangle) -> None:
    super()._render(rect)
    self.blind_spot_indicators.render(rect)

  def _has_blind_spot_detected(self) -> bool:

    return self.blind_spot_indicators.detected
