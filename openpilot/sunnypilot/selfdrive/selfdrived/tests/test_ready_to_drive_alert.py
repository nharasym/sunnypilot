"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(ready-to-drive): sound only since 2026-10-05; the model names moved to the startup rail
from openpilot.cereal import log, custom
from openpilot.selfdrive.selfdrived.events import ET
from openpilot.sunnypilot.selfdrive.selfdrived import events as ev

AlertSize = log.SelfdriveState.AlertSize
EventNameSP = custom.OnroadEventSP.EventName


class TestReadyToDriveAlert:
  def test_table_entry_is_the_callback(self):
    assert ev.EVENTS_SP[EventNameSP.bigModelReady][ET.PERMANENT] is ev.big_model_ready_alert

  def test_chime_without_a_popup(self):
    a = ev.big_model_ready_alert(None, None, None, False, 0, None)
    assert a.alert_size == AlertSize.none, "the visual lives on the startup rail now; nothing over the camera view"
    assert a.alert_text_1 == "" and a.alert_text_2 == ""
    assert a.audible_alert == ev.AudibleAlert.prompt, "the ready chime stays"

  def test_never_reads_bundles(self):
    # the old text helper is gone; the callback must not touch params inside selfdrived's alert pass
    assert not hasattr(ev, "_loaded_models_text")
