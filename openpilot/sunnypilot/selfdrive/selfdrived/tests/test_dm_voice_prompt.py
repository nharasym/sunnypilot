"""
HL-FEAT(dm-voice): the mici "Pay Attention" prompt speaks ("Hey! Pay attention!") instead of the
dm_warning chime. Three places must agree and this guards all of them: the SP enumerant in
custom.capnp, soundd's table (file, infinite loop, the format soundd asserts when it loads) and the
mici alert table (distracted speaks, "Touch Steering Wheel" keeps the chime).
"""
import importlib
import os
import unittest
import wave
from unittest.mock import patch

import numpy as np

from openpilot.cereal import custom, log
from openpilot.common.basedir import BASEDIR
from openpilot.common.hardware import HARDWARE
from openpilot.selfdrive.selfdrived import events as events_mod
from openpilot.selfdrive.selfdrived.events import ET
from openpilot.selfdrive.ui import soundd

AudibleAlert = log.SelfdriveState.AudibleAlert
AudibleAlertSP = custom.SelfdriveStateSP.AudibleAlert
VOICE = AudibleAlertSP.promptDistractedVoice
SOUNDS = os.path.join(BASEDIR, "openpilot/selfdrive/assets/sounds")
LOOP_PERIOD_MAX_S = 2.4   # soundd finishes the running loop before it stops: keep the silence after a clear short


def _read(path):
  with wave.open(path) as w:
    fmt = (w.getnchannels(), w.getsampwidth(), w.getframerate())
    x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
  return fmt, x


class TestDmVoicePrompt(unittest.TestCase):
  def test_enumerant_lives_in_the_sp_range(self):
    # soundd keys its table by the raw value, so the SP value must not shadow an upstream one
    assert int(VOICE) not in set(AudibleAlert.schema.enumerants.values())
    assert int(VOICE) > int(AudibleAlert.preAlert)

  def test_soundd_table_entry(self):
    filename, loops, volume = soundd.sound_list[VOICE]
    assert filename == "hihy/pay_attention.wav"
    assert loops is None                       # loops while the prompt stands, like dm_warning.wav
    assert volume == soundd.MAX_VOLUME

  def test_every_table_file_is_in_the_format_soundd_asserts(self):
    for filename, _, _ in soundd.sound_list.values():
      fmt, x = _read(os.path.join(SOUNDS, filename))
      assert fmt == (1, 2, soundd.SAMPLE_RATE), filename
      assert len(x) > 0, filename

  def test_voice_clip_shape(self):
    fmt, x = _read(os.path.join(SOUNDS, "hihy/pay_attention.wav"))
    assert len(x) / soundd.SAMPLE_RATE <= LOOP_PERIOD_MAX_S
    peak = float(np.abs(x).max())
    assert 0.6 <= peak <= 0.98, peak           # dm_warning peaks at 0.84; no clipping, not quiet
    active = x[np.abs(x) > 0.01]
    assert 0.12 <= float(np.sqrt((active ** 2).mean())) <= 0.35   # dm_warning's active rms is 0.25
    tail = x[-int(0.3 * soundd.SAMPLE_RATE):]
    assert float(np.abs(tail).max()) < 0.01    # ends in silence: the breath between repeats

  def test_mici_distracted_speaks_and_unresponsive_keeps_the_chime(self):
    # the mici alert table is applied at import time behind HARDWARE.get_device_type()
    with patch.object(HARDWARE, "get_device_type", return_value="mici"):
      mod = importlib.reload(events_mod)
    try:
      distracted = mod.EVENTS[mod.EventName.driverDistracted2][ET.PERMANENT]
      unresponsive = mod.EVENTS[mod.EventName.driverUnresponsive2][ET.PERMANENT]
      assert distracted.alert_text_1 == "Pay Attention"
      assert int(distracted.audible_alert) == int(VOICE)
      assert int(unresponsive.audible_alert) == int(AudibleAlert.promptDistracted)
    finally:
      importlib.reload(events_mod)


if __name__ == "__main__":
  unittest.main()
