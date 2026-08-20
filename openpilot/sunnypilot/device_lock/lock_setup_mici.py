"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): mici (comma 4) entry point for the lock.
#
# Lives on the MAIN settings page as a circle button beside the Always Offroad controls, not
# buried inside Settings > Device: the lock is conceptually a sibling of Always Offroad (it
# *forces* always-offroad), so it belongs at the same level and the same visual weight.
#
# mici uses a different widget vocabulary from the big UI (BigCircleButton + texture rather than
# list rows), so the button is built here; the LockSetupDialog itself is shared.

from openpilot.selfdrive.ui.mici.widgets.button import BigCircleButton
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.sunnypilot.device_lock.lock import DeviceLock
from openpilot.sunnypilot.device_lock.lock_setup import LockSetupDialog
from openpilot.sunnypilot.device_lock.mount import is_device_locked

# matches BIG_ICON_SIZE used by the neighbouring always-offroad buttons; the source icon is
# 126x164, so keep its aspect rather than squashing it to a square
LOCK_ICON_H = 110
LOCK_ICON_W = 85


def device_lock_circle_button_mici(lock: DeviceLock | None = None) -> BigCircleButton:
  """Circle button for the main mici settings page that opens the set-pattern-and-lock flow.

  Only shown while offroad and unlocked: locking is refused onroad by DeviceLock.lock() anyway,
  and once locked the overlay covers the whole screen so the button is unreachable by definition.
  """
  _lock = lock if lock is not None else DeviceLock()

  btn = BigCircleButton(gui_app.texture("icons_mici/settings/network/new/lock.png",
                                        LOCK_ICON_W, LOCK_ICON_H), red=False)
  btn.set_click_callback(lambda: gui_app.push_widget(LockSetupDialog(_lock)))
  # is_device_locked() is the cached flag refreshed by the mount tick - set_visible runs every
  # frame, so avoid a Params read per frame here
  btn.set_visible(lambda: not ui_state.started and not is_device_locked())
  return btn
