"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(ready-to-drive)
from unittest import mock

from openpilot.cereal import log, custom
from openpilot.selfdrive.selfdrived.events import ET
from openpilot.sunnypilot.selfdrive.selfdrived import events as ev

AlertSize = log.SelfdriveState.AlertSize
EventNameSP = custom.OnroadEventSP.EventName


def _bundle(name):
  b = mock.Mock()
  b.displayName = name
  return b


class TestReadyToDriveAlert:
  def test_table_entry_is_the_callback(self):
    assert ev.EVENTS_SP[EventNameSP.bigModelReady][ET.PERMANENT] is ev.big_model_ready_alert

  def test_text_and_size(self):
    with mock.patch("openpilot.sunnypilot.models.helpers.get_active_bundle",
                    side_effect=lambda chestnut: _bundle("Cinque V2") if chestnut else _bundle("Small X")):
      a = ev.big_model_ready_alert(None, None, None, False, 0, None)
    assert a.alert_text_1 == "Ready To Drive"
    assert a.alert_text_2 == "Cinque V2 / Small X"
    assert a.alert_size == AlertSize.mid, "mid is the size that renders the second line"
    assert a.duration == 200, "Alert stores seconds as 100 Hz frames; 2 s hold like the original"

  def test_only_big_model_known(self):
    with mock.patch("openpilot.sunnypilot.models.helpers.get_active_bundle",
                    side_effect=lambda chestnut: _bundle("Cinque V2") if chestnut else None):
      assert ev.big_model_ready_alert(None, None, None, False, 0, None).alert_text_2 == "Cinque V2"

  def test_bundle_lookup_failure_never_raises(self):
    with mock.patch("openpilot.sunnypilot.models.helpers.get_active_bundle", side_effect=RuntimeError("params down")):
      a = ev.big_model_ready_alert(None, None, None, False, 0, None)
    assert a.alert_text_1 == "Ready To Drive" and a.alert_text_2 == ""
