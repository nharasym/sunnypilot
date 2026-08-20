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
from openpilot.sunnypilot.device_lock.lock import DeviceLock, LockError, PinError
from openpilot.sunnypilot.device_lock.pin_screen import BODY_COLOR, PinScreen

# NOTE: ui_state / list_view / multilang are imported lazily inside device_lock_item() below.
# They pull in the whole cereal messaging stack at import time, which the dialog itself does not
# need - keeping them out of module scope lets LockSetupDialog be imported (and unit-tested)
# without a compiled msgq present.


class LockSetupDialog(PinScreen):
  """Enter a new PIN twice, then lock the device."""

  def __init__(self, lock: DeviceLock | None = None):
    super().__init__()
    self._lock = lock if lock is not None else DeviceLock()
    self._first: str | None = None

  def title(self) -> str:
    return "CONFIRM PATTERN" if self._first else "SET UNLOCK PATTERN"

  def subtitle(self):
    if self._first:
      return "Enter the same pattern again", BODY_COLOR
    return "Tap 4-8 symbols. This unlocks the device - do not forget it.", BODY_COLOR

  def on_back_empty(self) -> None:
    """Back with an empty pattern: step back a stage, then out of the dialog entirely.

    Without this the setup screen is a trap - there is no other way out except completing the
    lock or waiting for the display to time out.
    """
    if self._first is not None:
      # in the confirm stage: return to entering the first pattern
      self._first = None
      self.set_error("")
      return
    gui_app.pop_widget()

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
      self.set_error("Patterns did not match")
      return

    try:
      self._lock.set_pin(pin)
      # refuses if the car went onroad while this dialog was open
      self._lock.lock()
    except (PinError, LockError) as e:
      self._first = None
      self.set_error(str(e))
      return

    gui_app.pop_widget()  # mount.py's tick immediately puts up the lock screen


def device_lock_item(lock: DeviceLock | None = None):
  """Settings > Device row (big UI) that opens the lock flow. Offroad-only, like other risky actions."""
  from openpilot.selfdrive.ui.ui_state import ui_state
  from openpilot.system.ui.lib.multilang import tr
  from openpilot.system.ui.sunnypilot.widgets.list_view import button_item_sp
  from openpilot.sunnypilot.device_lock.constants import PARAM_LOCKED

  _lock = lock if lock is not None else DeviceLock()

  def _open():
    # match the button's own gate; DeviceLock.lock() re-checks at submit time in case the car
    # goes onroad while this dialog is open
    if not ui_state.is_offroad():
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
