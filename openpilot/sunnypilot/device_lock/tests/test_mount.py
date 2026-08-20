"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): tests for the overlay mount/unmount tick.
#
# Regression cover for the "locked but no PIN pad until reboot" bug: mount state must be DERIVED
# from the nav stack every frame, never cached, because other code pops the stack from under us
# (mici's _handle_transitions calls pop_widgets_to() on onroad and standstill-exit transitions).
#
# Run: python -m unittest openpilot.sunnypilot.device_lock.tests.test_mount -v

from openpilot.sunnypilot.device_lock.tests._params_stub import ensure_headless_ui, ensure_msgq_stub, ensure_params_stub
ensure_params_stub()
ensure_msgq_stub()
ensure_headless_ui()

import types
import unittest
from unittest import mock

from openpilot.sunnypilot.device_lock.constants import PARAM_IS_ONROAD, PARAM_LOCKED, PARAM_OFFROAD_MODE
from openpilot.sunnypilot.device_lock.lock import DeviceLock
from openpilot.sunnypilot.device_lock.tests.test_device_lock import FakeParams


class FakeGuiApp:
  """Stand-in for gui_app's nav stack, mirroring push/pop/widget_in_stack semantics."""

  def __init__(self):
    self.stack: list[object] = []
    self.ticks: list = []

  def add_nav_stack_tick(self, fn):
    self.ticks.append(fn)

  def push_widget(self, w):
    if w in self.stack:
      return
    self.stack.append(w)

  def pop_widgets_to(self, widget, callback=None, instant=False):
    if widget not in self.stack:
      return
    idx = self.stack.index(widget)
    del self.stack[idx + 1:]

  def widget_in_stack(self, w) -> bool:
    return w in self.stack


class MountTestCase(unittest.TestCase):
  """Shared env builder (the old `env` fixture); patches are undone per-test via enterContext."""

  def make_env(self):
    from openpilot.sunnypilot.device_lock import mount as mount_mod

    gui = FakeGuiApp()
    self.enterContext(mock.patch.object(mount_mod, "gui_app", gui))
    # LockedOverlay builds raylib Buttons; the mount only needs an object identity here
    self.enterContext(mock.patch.object(mount_mod, "LockedOverlay", lambda lock=None: object()))

    params = FakeParams()
    lock = DeviceLock(params=params)
    root = object()
    gui.push_widget(root)  # the main layout, as the real UI does before mounting
    m = mount_mod.install_device_lock(root, lock=lock)
    return types.SimpleNamespace(gui=gui, lock=lock, params=params, root=root, mount=m,
                                 overlay=m._overlay, tick=m._tick)


class TestMountLifecycle(MountTestCase):

  def test_not_mounted_when_unlocked(self):
    env = self.make_env()
    assert not env.gui.widget_in_stack(env.overlay)

  def test_mounts_when_locked(self):
    env = self.make_env()
    env.lock.lock()
    env.tick()
    assert env.gui.widget_in_stack(env.overlay)

  def test_unmounts_when_unlocked(self):
    env = self.make_env()
    env.lock.lock()
    env.tick()
    env.lock.unlock()
    env.tick()
    assert not env.gui.widget_in_stack(env.overlay)

  def test_remounts_after_something_else_pops_the_stack(self):
    """THE REGRESSION: mici's _handle_transitions pops the nav stack on transitions.

    With mount state cached in a bool, the overlay stayed popped while we believed it was up -
    device locked, no PIN pad, until a reboot or a remote lock toggle.
    """
    env = self.make_env()
    env.lock.lock()
    env.tick()
    assert env.gui.widget_in_stack(env.overlay)

    env.gui.pop_widgets_to(env.root)  # simulates _handle_transitions
    assert not env.gui.widget_in_stack(env.overlay)

    env.tick()
    assert env.gui.widget_in_stack(env.overlay), "overlay must self-heal back onto the stack"

  def test_remote_lock_while_unlocked_mounts_overlay(self):
    """Dashboard saveParams({'DeviceLocked': '1'}) writes the param directly, bypassing lock()."""
    env = self.make_env()
    env.params.put_bool(PARAM_LOCKED, True)
    env.tick()
    assert env.gui.widget_in_stack(env.overlay)

  def test_repeated_ticks_do_not_duplicate_the_overlay(self):
    env = self.make_env()
    env.lock.lock()
    for _ in range(5):
      env.tick()
    assert env.gui.stack.count(env.overlay) == 1

  def test_tick_enforces_offroad_mode_while_locked(self):
    env = self.make_env()
    env.lock.lock()
    env.params.put_bool(PARAM_OFFROAD_MODE, False)  # someone toggles Always Offroad off
    env.tick()
    assert env.params.get_bool(PARAM_OFFROAD_MODE), "enforcement must self-heal"

  def test_tick_does_not_force_offroad_while_onroad(self):
    """Deferred remote lock: must not drop control mid-drive."""
    env = self.make_env()
    env.params.put_bool(PARAM_IS_ONROAD, True)
    env.params.put_bool(PARAM_LOCKED, True)
    env.tick()
    assert not env.params.get_bool(PARAM_OFFROAD_MODE)


class TestAlertMirroring(MountTestCase):

  def test_alert_mirrors_lock_transitions(self):
    """The offroad alert must track lock-state TRANSITIONS, including paths that bypass
    lock()/unlock(): a remote saveParams write, and re-assertion on the first tick after boot
    (the alert key is CLEAR_ON_MANAGER_START, so a reboot-while-locked loses it)."""
    from openpilot.sunnypilot.device_lock import lock as lock_mod

    env = self.make_env()
    calls = []
    self.enterContext(mock.patch.object(lock_mod.DeviceLock, "_set_alert", staticmethod(calls.append)))

    # boot-while-locked: first tick must mirror True (the fixture already ran one tick unlocked,
    # so simulate a fresh mount observing a locked state)
    env.mount._prev_locked = None
    env.params.put_bool(PARAM_LOCKED, True)
    env.tick()
    assert calls[-1] is True, "first tick after boot-while-locked must set the alert"

    # steady state: no re-set every frame
    n = len(calls)
    env.tick()
    env.tick()
    assert len(calls) == n, "alert must only be written on transitions"

    # remote unlock (param write bypasses unlock()): alert must clear
    env.params.put_bool(PARAM_LOCKED, False)
    env.tick()
    assert calls[-1] is False, "remote unlock must clear the alert"

  def test_first_tick_clears_stale_alert_when_unlocked(self):
    """UI restart after a remote unlock that happened while the UI was dead: the first tick must
    mirror False so a stale "locked" alert gets cleared. Pins the None sentinel init — a plain
    False init would skip this and pass every other test."""
    from openpilot.sunnypilot.device_lock import lock as lock_mod
    from openpilot.sunnypilot.device_lock import mount as mount_mod

    calls = []
    self.enterContext(mock.patch.object(lock_mod.DeviceLock, "_set_alert", staticmethod(calls.append)))
    gui = FakeGuiApp()
    self.enterContext(mock.patch.object(mount_mod, "gui_app", gui))
    self.enterContext(mock.patch.object(mount_mod, "LockedOverlay", lambda lock=None: object()))
    root = object()
    gui.push_widget(root)

    mount_mod.install_device_lock(root, lock=DeviceLock(params=FakeParams()))  # first tick in __init__

    assert calls and calls[-1] is False, "first tick while unlocked must clear any stale alert"


# --- back-to-exit from the setup flow (and NOT from the lock screen) ---

class TestBackToExit(unittest.TestCase):

  def test_locked_overlay_does_not_override_back_empty(self):
    """SAFETY: the lock screen must never be dismissible with the back key.

    PinScreen.on_back_empty is a deliberate no-op; LockedOverlay must inherit it. If someone ever
    gives the lock screen a back-out, the whole feature is defeated.
    """
    from openpilot.sunnypilot.device_lock.locked_overlay import LockedOverlay
    from openpilot.sunnypilot.device_lock.pin_screen import PinScreen

    assert LockedOverlay.on_back_empty is PinScreen.on_back_empty

  def test_back_on_empty_entry_routes_to_hook(self):
    """_on_key must call on_back_empty when there is nothing left to delete."""
    from openpilot.sunnypilot.device_lock.pin_screen import CLEAR_KEY, PinScreen

    scr = object.__new__(PinScreen)
    scr._entry = ""
    scr._error = ""
    called = []
    scr.on_back_empty = lambda: called.append(True)
    PinScreen._on_key(scr, CLEAR_KEY)
    assert called, "back on an empty entry must invoke on_back_empty"

    # with content, back deletes instead of invoking the hook
    scr._entry = "012"
    called.clear()
    PinScreen._on_key(scr, CLEAR_KEY)
    assert scr._entry == "01" and not called

  def test_setup_back_empty_first_stage_exits(self):
    """The reported bug: pressing back until empty left no way out of the setup screen."""
    from openpilot.sunnypilot.device_lock import lock_setup

    popped = []
    self.enterContext(mock.patch.object(lock_setup.gui_app, "pop_widget", lambda *a, **k: popped.append(True)))

    dlg = object.__new__(lock_setup.LockSetupDialog)
    dlg._first = None
    dlg._entry = ""
    dlg._error = ""
    dlg.on_back_empty()
    assert popped, "back on an empty first-stage pattern must leave the setup screen"

  def test_setup_back_empty_confirm_stage_returns_to_first(self):
    """In the confirm stage, back steps BACK a stage rather than exiting outright."""
    from openpilot.sunnypilot.device_lock import lock_setup

    popped = []
    self.enterContext(mock.patch.object(lock_setup.gui_app, "pop_widget", lambda *a, **k: popped.append(True)))

    dlg = object.__new__(lock_setup.LockSetupDialog)
    dlg._first = "0123"
    dlg._entry = ""
    dlg._error = "Patterns did not match"
    dlg.on_back_empty()

    assert dlg._first is None, "must return to the first-pattern stage"
    assert not popped, "must NOT exit the dialog from the confirm stage"


class TestCachedLockState(MountTestCase):

  def test_cached_is_device_locked_tracks_state(self):
    from openpilot.sunnypilot.device_lock import mount as mount_mod

    env = self.make_env()
    env.lock.lock()
    env.tick()
    assert mount_mod.is_device_locked()

    env.lock.unlock()
    env.tick()
    assert not mount_mod.is_device_locked()


if __name__ == "__main__":
  unittest.main()
