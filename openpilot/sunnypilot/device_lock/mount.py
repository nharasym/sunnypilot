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


# Cached lock state, refreshed once per frame by DeviceLockMount._tick(). Lets hot paths (the
# UI's awake/brightness decision) ask "are we locked?" without a Params read every frame.
# Defaults False so anything running before the mount exists behaves normally.
_locked = False


def is_device_locked() -> bool:
  """Cheap, cached 'is the device locked' for per-frame UI code."""
  return _locked


class DeviceLockMount:
  """Owns the lock overlay's lifecycle and the per-frame enforcement tick."""

  def __init__(self, root_widget, lock: DeviceLock | None = None):
    self._root = root_widget
    self._lock = lock if lock is not None else DeviceLock()
    self._overlay = LockedOverlay(lock=self._lock)
    self._prev_locked: bool | None = None  # None = not yet observed, so the first tick mirrors

    # runs every frame regardless of nav-stack depth
    gui_app.add_nav_stack_tick(self._tick)
    self._tick()  # show immediately if we booted locked

  def _tick(self) -> None:
    global _locked

    # 1) Enforcement floor: re-assert OffroadMode while locked. This is what makes turning
    #    Always Offroad off on the device screen a no-op - it's put back within a frame.
    #    Runs even if the overlay isn't mounted, so the car stays stock regardless.
    self._lock.enforce()
    _locked = self._lock.is_locked()

    # 1b) Mirror the offroad alert on lock-state TRANSITIONS (not every frame). lock()/unlock()
    #     already set it, but two paths bypass them: a remote sunnylink saveParams writes
    #     DeviceLocked directly (would leave a stale "locked" alert forever), and the alert key
    #     is CLEAR_ON_MANAGER_START so a reboot-while-locked would lose it. Observing the
    #     transition here (first tick included, via the None sentinel) covers both. _set_alert
    #     is best-effort/exception-swallowed, so this can never break the tick.
    if _locked != self._prev_locked:
      self._lock._set_alert(_locked)
      self._prev_locked = _locked

    # 2) The lock screen itself: mount while locked, drop it once unlocked. push_widget
    #    disables everything beneath, so Settings (and the Always Offroad toggle) is
    #    unreachable for as long as this is up.
    #
    #    Mount state is DERIVED from the nav stack, never cached. Other code pops the stack
    #    from under us - mici's _handle_transitions calls pop_widgets_to() on onroad and
    #    standstill-exit transitions (and even flags it: "FIXME: these two pops can interrupt
    #    user interacting in the settings"). With a cached flag the overlay would be popped
    #    while we still believed it was up, leaving the device locked with no PIN pad until a
    #    reboot. Re-deriving each frame means any stray pop simply self-heals next frame.
    mounted = gui_app.widget_in_stack(self._overlay)
    if _locked and not mounted:
      gui_app.push_widget(self._overlay)
    elif not _locked and mounted:
      # instant=True on purpose: the default animates the dismiss, so the overlay would linger
      # in the stack for several frames and we'd re-issue the pop every frame while it played.
      gui_app.pop_widgets_to(self._root, instant=True)


def install_device_lock(root_widget, lock: DeviceLock | None = None) -> DeviceLockMount:
  """Wire the device lock into a UI. Call once, last, from the main layout's __init__."""
  return DeviceLockMount(root_widget, lock=lock)
