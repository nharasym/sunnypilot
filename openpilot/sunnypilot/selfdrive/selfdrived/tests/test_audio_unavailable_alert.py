"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FIX(audio-retry): soundd keeps retrying the speaker; past upstream's 30 s budget it flags
# AudioUnavailable and selfdrived shows a permanent, silent, non-blocking warning
from openpilot.cereal import log, custom
from openpilot.selfdrive.selfdrived.events import ET
from openpilot.selfdrive.ui.soundd import MIC_STALE_S, Soundd
from openpilot.sunnypilot.selfdrive.selfdrived import events as ev

AlertSize = log.SelfdriveState.AlertSize
EventNameSP = custom.OnroadEventSP.EventName


class _Params:
  def __init__(self):
    self.d: dict[str, bool] = {}
    self.puts = 0

  def get_bool(self, k):
    return bool(self.d.get(k, False))

  def put_bool(self, k, v, block=False):
    self.d[k] = v
    self.puts += 1
    self.blocking = block

  def remove(self, k):
    self.d.pop(k, None)


class TestAudioUnavailableAlert:
  def test_enum_is_appended_not_renumbered(self):
    # a port that lands an upstream @27 must renumber ours, not silently collide
    assert int(EventNameSP.audioUnavailable) == 27
    assert ev.EVENT_NAME_SP[int(EventNameSP.audioUnavailable)] == "audioUnavailable"

  def test_permanent_only_so_it_never_blocks_or_ends_engagement(self):
    assert set(ev.EVENTS_SP[EventNameSP.audioUnavailable]) == {ET.PERMANENT}

  def test_visible_and_silent(self):
    a = ev.EVENTS_SP[EventNameSP.audioUnavailable][ET.PERMANENT]
    assert (a.alert_text_1, a.alert_text_2) == ("Audio Unavailable", "Alerts are silent")
    assert a.alert_size == AlertSize.mid
    assert a.audible_alert == ev.AudibleAlert.none


class TestSounddFlag:
  def _soundd(self):
    s = Soundd.__new__(Soundd)  # no wav loading, no real params
    s.params = _Params()
    return s

  def test_flag_is_written_once_while_it_stays_set(self):
    s = self._soundd()
    for _ in range(5):
      s.flag_audio_unavailable()
    assert s.params.d["AudioUnavailable"] is True and s.params.puts == 1

  def test_flag_comes_back_after_an_onroad_transition_clears_it(self):
    s = self._soundd()
    s.flag_audio_unavailable()
    s.params.remove("AudioUnavailable")  # CLEAR_ON_ONROAD_TRANSITION while soundd is still retrying
    s.flag_audio_unavailable()
    assert s.params.d["AudioUnavailable"] is True and s.params.puts == 2

  def test_flag_write_is_blocking(self):
    # a queued write landing after the stream-open remove would leave a false warning up all drive
    s = self._soundd()
    s.flag_audio_unavailable()
    assert s.params.blocking is True


class _Sm:
  def __init__(self, sound_pressure_recv_t: float):
    self.recv_time = {'soundPressure': sound_pressure_recv_t}


class TestSilentMicVolume:
  def _soundd(self, opened_t: float):
    s = Soundd.__new__(Soundd)
    s.stream_open_t = opened_t
    return s

  def test_mic_never_published_after_the_grace_is_silent(self):
    assert self._soundd(100.).mic_silent(_Sm(0.), 100. + MIC_STALE_S + 0.1)

  def test_inside_the_grace_after_the_speaker_opens_is_not_silent(self):
    assert not self._soundd(100.).mic_silent(_Sm(0.), 100. + MIC_STALE_S - 0.1)

  def test_a_live_mic_is_not_silent(self):
    assert not self._soundd(100.).mic_silent(_Sm(200.), 201.)

  def test_a_mic_that_stops_publishing_goes_silent(self):
    assert self._soundd(100.).mic_silent(_Sm(200.), 200. + MIC_STALE_S + 0.1)
