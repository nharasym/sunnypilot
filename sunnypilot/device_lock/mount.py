"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): single mount point for the lock screen.
#
# Keeps the whole wiring in this package so each UI's main.py needs only ONE line:
#     install_device_lock(self)
# (added last in __init__, so the overlay lands on top of anything else pushed).
# That's the entire integration surface for the re-port onto a new sunnypilot release.

from openpilot.system.ui.lib.application import gui_app
from openpilot.sunnypilot.device_lock.lock import DeviceLock
from openpilot.sunnypilot.device_lock.locked_overlay import LockedOverlay


class DeviceLockMount:
  """Owns the lock overlay's lifecycle and the per-frame enforcement tick."""

  def __init__(self, root_widget, lock: DeviceLock | None = None):
    self._root = root_widget
    self._lock = lock if lock is not None else DeviceLock()
    self._overlay = LockedOverlay(lock=self._lock)
    self._shown = False

    # runs every frame regardless of nav-stack depth
    gui_app.add_nav_stack_tick(self._tick)
    self._tick()  # show immediately if we booted locked

  def _tick(self) -> None:
    # 1) Enforcement floor: re-assert OffroadMode while locked. This is what makes turning
    #    Always Offroad off on the device screen a no-op - it's put back within a frame.
    #    Runs even if the overlay isn't mounted, so the car stays stock regardless.
    self._lock.enforce()

    # 2) The lock screen itself: mount while locked, drop it once unlocked. push_widget
    #    disables everything beneath, so Settings (and the Always Offroad toggle) is
    #    unreachable for as long as this is up.
    locked = self._lock.is_locked()
    if locked and not self._shown:
      gui_app.push_widget(self._overlay)
      self._shown = True
    elif not locked and self._shown:
      gui_app.pop_widgets_to(self._root)
      self._shown = False


def install_device_lock(root_widget, lock: DeviceLock | None = None) -> DeviceLockMount:
  """Wire the device lock into a UI. Call once, last, from the main layout's __init__."""
  return DeviceLockMount(root_widget, lock=lock)
