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
# Run: uv run --with pytest python -m pytest sunnypilot/device_lock/tests/ --noconftest -o addopts="" -q

from openpilot.sunnypilot.device_lock.tests._params_stub import ensure_params_stub
ensure_params_stub()

import types

import pytest

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


@pytest.fixture
def env(monkeypatch):
  from openpilot.sunnypilot.device_lock import mount as mount_mod

  gui = FakeGuiApp()
  monkeypatch.setattr(mount_mod, "gui_app", gui)
  # LockedOverlay builds raylib Buttons; the mount only needs an object identity here
  monkeypatch.setattr(mount_mod, "LockedOverlay", lambda lock=None: object())

  params = FakeParams()
  lock = DeviceLock(params=params)
  root = object()
  gui.push_widget(root)  # the main layout, as the real UI does before mounting
  m = mount_mod.install_device_lock(root, lock=lock)
  return types.SimpleNamespace(gui=gui, lock=lock, params=params, root=root, mount=m,
                               overlay=m._overlay, tick=m._tick)


def test_not_mounted_when_unlocked(env):
  assert not env.gui.widget_in_stack(env.overlay)


def test_mounts_when_locked(env):
  env.lock.lock()
  env.tick()
  assert env.gui.widget_in_stack(env.overlay)


def test_unmounts_when_unlocked(env):
  env.lock.lock()
  env.tick()
  env.lock.unlock()
  env.tick()
  assert not env.gui.widget_in_stack(env.overlay)


def test_remounts_after_something_else_pops_the_stack(env):
  """THE REGRESSION: mici's _handle_transitions pops the nav stack on transitions.

  With mount state cached in a bool, the overlay stayed popped while we believed it was up -
  device locked, no PIN pad, until a reboot or a remote lock toggle.
  """
  env.lock.lock()
  env.tick()
  assert env.gui.widget_in_stack(env.overlay)

  env.gui.pop_widgets_to(env.root)  # simulates _handle_transitions
  assert not env.gui.widget_in_stack(env.overlay)

  env.tick()
  assert env.gui.widget_in_stack(env.overlay), "overlay must self-heal back onto the stack"


def test_remote_lock_while_unlocked_mounts_overlay(env):
  """Dashboard saveParams({'DeviceLocked': '1'}) writes the param directly, bypassing lock()."""
  env.params.put_bool(PARAM_LOCKED, True)
  env.tick()
  assert env.gui.widget_in_stack(env.overlay)


def test_repeated_ticks_do_not_duplicate_the_overlay(env):
  env.lock.lock()
  for _ in range(5):
    env.tick()
  assert env.gui.stack.count(env.overlay) == 1


def test_tick_enforces_offroad_mode_while_locked(env):
  env.lock.lock()
  env.params.put_bool(PARAM_OFFROAD_MODE, False)  # someone toggles Always Offroad off
  env.tick()
  assert env.params.get_bool(PARAM_OFFROAD_MODE), "enforcement must self-heal"


def test_tick_does_not_force_offroad_while_onroad(env):
  """Deferred remote lock: must not drop control mid-drive."""
  env.params.put_bool(PARAM_IS_ONROAD, True)
  env.params.put_bool(PARAM_LOCKED, True)
  env.tick()
  assert not env.params.get_bool(PARAM_OFFROAD_MODE)


def test_alert_mirrors_lock_transitions(env, monkeypatch):
  """The offroad alert must track lock-state TRANSITIONS, including paths that bypass
  lock()/unlock(): a remote saveParams write, and re-assertion on the first tick after boot
  (the alert key is CLEAR_ON_MANAGER_START, so a reboot-while-locked loses it)."""
  from openpilot.sunnypilot.device_lock import lock as lock_mod

  calls = []
  monkeypatch.setattr(lock_mod.DeviceLock, "_set_alert", staticmethod(calls.append))

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


def test_first_tick_clears_stale_alert_when_unlocked(monkeypatch):
  """UI restart after a remote unlock that happened while the UI was dead: the first tick must
  mirror False so a stale "locked" alert gets cleared. Pins the None sentinel init — a plain
  False init would skip this and pass every other test."""
  from openpilot.sunnypilot.device_lock import lock as lock_mod
  from openpilot.sunnypilot.device_lock import mount as mount_mod

  calls = []
  monkeypatch.setattr(lock_mod.DeviceLock, "_set_alert", staticmethod(calls.append))
  gui = FakeGuiApp()
  monkeypatch.setattr(mount_mod, "gui_app", gui)
  monkeypatch.setattr(mount_mod, "LockedOverlay", lambda lock=None: object())
  root = object()
  gui.push_widget(root)

  mount_mod.install_device_lock(root, lock=DeviceLock(params=FakeParams()))  # first tick in __init__

  assert calls and calls[-1] is False, "first tick while unlocked must clear any stale alert"


def test_cached_is_device_locked_tracks_state(env):
  from openpilot.sunnypilot.device_lock import mount as mount_mod

  env.lock.lock()
  env.tick()
  assert mount_mod.is_device_locked()

  env.lock.unlock()
  env.tick()
  assert not mount_mod.is_device_locked()
