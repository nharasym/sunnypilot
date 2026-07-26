"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): the "lock this device" flow, reached from Settings > Device.
#
# Set a PIN (entered twice) and engage the lock in one go. The PIN must be set on-device
# because only the device can compute the PBKDF2 hash - the dashboard never sees plaintext.
# Once engaged, mount.py's tick puts up the lock screen and Settings becomes unreachable.

from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.sunnypilot.widgets.list_view import button_item_sp
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.sunnypilot.device_lock.constants import PARAM_LOCKED
from openpilot.sunnypilot.device_lock.lock import DeviceLock, PinError
from openpilot.sunnypilot.device_lock.pin_screen import BODY_COLOR, PinScreen


class LockSetupDialog(PinScreen):
  """Enter a new PIN twice, then lock the device."""

  def __init__(self, lock: DeviceLock | None = None):
    super().__init__()
    self._lock = lock if lock is not None else DeviceLock()
    self._first: str | None = None

  def title(self) -> str:
    return "CONFIRM PIN" if self._first else "SET LOCK PIN"

  def subtitle(self):
    if self._first:
      return "Re-enter the PIN to confirm", BODY_COLOR
    return "This PIN unlocks the device. Do not forget it.", BODY_COLOR

  def on_submit(self, pin: str) -> None:
    if self._first is None:
      try:
        # validate before asking for confirmation so errors surface early
        from openpilot.sunnypilot.device_lock.lock import validate_pin
        validate_pin(pin)
      except PinError as e:
        self.set_error(str(e))
        return
      self._first = pin
      return

    if pin != self._first:
      self._first = None
      self.set_error("PINs did not match")
      return

    try:
      self._lock.set_pin(pin)
    except PinError as e:
      self._first = None
      self.set_error(str(e))
      return

    self._lock.lock()
    gui_app.pop_widget()  # mount.py's tick immediately puts up the lock screen


def device_lock_item(lock: DeviceLock | None = None):
  """Settings > Device row that opens the lock flow. Offroad-only, like the other risky actions."""
  _lock = lock if lock is not None else DeviceLock()

  def _open():
    if ui_state.engaged:
      return
    gui_app.push_widget(LockSetupDialog(_lock))

  description = (
    "Disable sunnypilot and revert the vehicle to its factory driver-assist systems, then lock the screen behind a PIN. " +
    "Use this when leaving the car with someone else, e.g. a dealership. Unlock on-device or from the sunnylink dashboard."
  )

  return button_item_sp(
    lambda: tr("Lock Device"),
    lambda: tr("LOCK"),
    lambda: tr(description),
    callback=_open,
    enabled=lambda: ui_state.is_offroad() and not ui_state.params.get_bool(PARAM_LOCKED),
  )
