"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): mici (comma 4) variant of the Settings > Device entry.
#
# mici has its own settings tree with a different widget vocabulary (BigButton + texture) from the
# big UI's list rows, so the button is built separately here. Kept in its own module so the big-UI
# path never imports mici widgets and vice versa. The dialog itself (LockSetupDialog) is shared.

from openpilot.selfdrive.ui.mici.widgets.button import BigButton
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.sunnypilot.device_lock.constants import PARAM_LOCKED
from openpilot.sunnypilot.device_lock.lock import DeviceLock
from openpilot.sunnypilot.device_lock.lock_setup import LockSetupDialog


def device_lock_button_mici(lock: DeviceLock | None = None) -> BigButton:
  """Settings > Device button that opens the set-PIN-and-lock flow. Offroad-only."""
  _lock = lock if lock is not None else DeviceLock()

  btn = BigButton("lock device", "", gui_app.texture("icons_mici/settings/network/new/lock.png", 64, 64))
  btn.set_click_callback(lambda: gui_app.push_widget(LockSetupDialog(_lock)))
  # DeviceLock.lock() re-checks onroad at submit time; this just greys the button out
  btn.set_enabled(lambda: ui_state.is_offroad() and not ui_state.params.get_bool(PARAM_LOCKED))
  return btn
