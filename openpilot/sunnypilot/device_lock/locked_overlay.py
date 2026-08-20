"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): the lock screen. This is the CORE of the feature.
#
# Pushed onto the nav stack whenever DeviceLocked is set, which disables every widget beneath it
# (see gui_app.push_widget) so Settings - and therefore the Always Offroad toggle - simply cannot
# be reached. Modelled on OnboardingWindow, which uses the same "cover everything until a
# condition is met" pattern. Lifecycle is owned by mount.py.

from openpilot.sunnypilot.device_lock.lock import DeviceLock
from openpilot.sunnypilot.device_lock.pin_screen import BODY_COLOR, ERROR_COLOR, PinScreen


class LockedOverlay(PinScreen):
  """Full-screen, undismissable lock screen with PIN entry."""

  def __init__(self, lock: DeviceLock | None = None):
    super().__init__()
    self._lock = lock if lock is not None else DeviceLock()

  def title(self) -> str:
    return "DEVICE LOCKED"

  def subtitle(self):
    if self._lock.is_rate_limited():
      return f"Too many attempts - wait {int(self._lock.cooldown_remaining()) + 1}s", ERROR_COLOR
    if not self._lock.has_pin():
      return "No pattern set - unlock from the sunnylink dashboard", BODY_COLOR
    return "Enter pattern to unlock. Vehicle is on factory systems.", BODY_COLOR

  def input_enabled(self) -> bool:
    return not self._lock.is_rate_limited() and self._lock.has_pin()

  def before_render(self) -> None:
    # keep the car reverted to stock for as long as this screen is up
    self._lock.enforce()

  def on_submit(self, pin: str) -> None:
    # mount.py's tick notices DeviceLocked cleared and pops this screen
    if not self._lock.try_unlock(pin):
      self.set_error("Incorrect pattern")
