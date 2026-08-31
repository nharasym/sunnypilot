"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pyray as rl

from openpilot.cereal import log
from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.blind_spot_indicators import BlindSpotIndicators
from openpilot.selfdrive.ui.ui_state import ChestnutState, ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.text_measure import measure_text_cached

ThermalStatus = log.DeviceState.ThermalStatus

# HL-FEAT(egpu-temp): GPU + CPU temp readout under the onroad chestnut icon. The icon's
# color keeps meaning STATE (green=active, orange=uncompiled/failed, white=loading) — the
# thermal zone is encoded in the temp TEXT color instead, so orange never becomes ambiguous
# ("hot" vs "failed").
# Layout (user-picked): a narrow right-aligned column under the icon — GPU first, on the
# icon it belongs to, CPU beneath. The right blind-spot indicator occupies this corner from
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

# HL-FEAT(egpu-icon-persist): sized to match the HOME screen's eGPU icon per user
# preference — the DMoji-glyph-height version (52px tall) read too large onroad, 37px
# tall reads right. Upstream's chestnut icons are 44px tall natively (green 60x44,
# orange 75x44), so scale by 37/44 preserving each icon's aspect. Vertical centering
# happens on the DMoji centerline (rect.y+40, see _draw_model_source) and is
# icon-height-independent.
_CHESTNUT_SCALE = 37 / 44


class _AlwaysVisible:
  """HL-FEAT(egpu-icon-persist): drop-in for the chestnut icon's FirstOrderFilter alpha gate.

  Upstream shows the onroad chestnut status icon only while loading or for ~2.5s after a
  state change, then fades it out (hud_renderer._draw_model_source). Replacing the
  filter with a constant keeps the icon permanently on screen in its state color
  (green=big model active, pulsing white=loading, orange=uncompiled/failed) while
  leaving ALL of upstream's state logic untouched — the icon still disappears entirely
  in the DISCONNECTED and READY states, which _draw_model_source never draws.
  """
  x = 1.0

  def update(self, _):
    return 1.0


class HudRendererSP(HudRenderer):
  def __init__(self):
    super().__init__()
    self.blind_spot_indicators = BlindSpotIndicators()
    self._chestnut_alpha_filter = _AlwaysVisible()  # HL-FEAT(egpu-icon-persist)
    # HL-FEAT(egpu-icon-persist): home-screen-sized textures (see _CHESTNUT_SCALE above);
    # upstream's position math reads icon.width/height so it self-adjusts to these
    self._txt_chestnut = gui_app.texture('icons_mici/chestnut.png', round(60 * _CHESTNUT_SCALE), 37)
    self._txt_chestnut_green = gui_app.texture('icons_mici/chestnut_green.png', round(60 * _CHESTNUT_SCALE), 37)
    self._txt_chestnut_orange = gui_app.texture('icons_mici/chestnut_orange.png', round(75 * _CHESTNUT_SCALE), 37)

  def _update_state(self) -> None:
    super()._update_state()
    self.blind_spot_indicators.update()

  def _draw_model_source(self, rect: rl.Rectangle) -> None:
    # HL-FEAT(egpu-icon-persist): alerts own the top band — the mici alert layer puts its
    # turn-signal/blind-spot glyphs at top-right and renders BEFORE the HUD, so the
    # now-permanent icon would paint over them (upstream never hit this: its icon was
    # bottom-anchored and transient). Same alert-active check the alert renderer uses.
    if ui_state.sm['selfdriveState'].alertSize != 0:
      return
    # HL-FEAT(egpu-icon-persist): relocate the now-permanent chestnut icon to the TOP of the
    # view, vertically centered on the DMoji's centerline (rect.y + 40). Upstream
    # bottom-anchors it:
    #   pos.y = rect.y + H - 14 - (wheel_h + icon_h) / 2      (wheel_h = 50)
    # and uses rect for nothing else in the method (verified at mici/onroad/hud_renderer.py
    # _draw_model_source). Solving for the icon's center at y+40:
    #   H = 40 - icon_h/2 + 14 + (50 + icon_h)/2 = 40 + 14 + 25 = 79
    # (icon_h cancels, so the math is independent of which state icon is showing.)
    super()._draw_model_source(rl.Rectangle(rect.x, rect.y, rect.width, 79))
    self._draw_temps(rect)

  def _draw_temps(self, rect: rl.Rectangle) -> None:
    # HL-FEAT(egpu-temp): GPU metrics only flow while the big model is actually running on
    # the card (modeld gates the SMU read on that), so gate on ACTIVE + a live publisher +
    # a real reading; tempC is 0 until the first SMU refresh. CPU rides the same gate so the
    # corner stays empty with no dock — deliberate, since the readout belongs to the icon.
    if ui_state.chestnut_state != ChestnutState.ACTIVE or not ui_state.sm.alive['chestnutState']:
      return
    gpu_temp = ui_state.sm['chestnutState'].tempC
    if gpu_temp <= 0:
      return

    # icon bottom edge is its centerline (rect.y+40) plus half of the 37px display height;
    # both lines right-aligned on the icon's own right edge (upstream anchors it at right - 10)
    right = rect.x + rect.width - 10
    y = rect.y + 40 + 37 / 2 + 4
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
    label_text, value_text = f"{label} ", f"{round(temp)}°"
    label_w = measure_text_cached(self._font_semi_bold, label_text, _TEMP_FONT_SIZE).x
    value_w = measure_text_cached(self._font_semi_bold, value_text, _TEMP_FONT_SIZE).x
    x = right_x - (label_w + value_w)
    # label stays dim so the eye lands on the numbers; only the value carries the thermal color
    rl.draw_text_ex(self._font_semi_bold, label_text, rl.Vector2(x, y), _TEMP_FONT_SIZE, 0,
                    rl.Color(255, 255, 255, 190))
    rl.draw_text_ex(self._font_semi_bold, value_text, rl.Vector2(x + label_w, y), _TEMP_FONT_SIZE, 0, color)
    return x

  def _render(self, rect: rl.Rectangle) -> None:
    super()._render(rect)
    self.blind_spot_indicators.render(rect)

  def _has_blind_spot_detected(self) -> bool:

    return self.blind_spot_indicators.detected
