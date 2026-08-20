"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): shared params stub for the off-device tests.
#
# Single definition on purpose. Each test module used to install its own, and because
# sys.modules is first-write-wins, whichever imported first decided whether the stub was
# functional - so the suite passed or errored depending on collection order.
#
# The stub must actually WORK, not merely exist: importing mount pulls in the UI widget chain
# (pin_screen -> widgets -> multilang), which constructs a real Params at import time.
#
# Signatures mirror common/params.py exactly. A double that is more permissive than the
# real API hides real bugs - that is precisely how Params.get(key, encoding="utf8") reached the
# device and crash-looped the UI. Keep these strict.

import os
import sys
import types


class StubParams:
  """Dict-backed Params with the real signatures."""

  _d: dict = {}

  def get(self, key, block=False, return_default=False):
    return self._d.get(key)

  def get_bool(self, key, block=False):
    return bool(self._d.get(key, False))

  def put(self, key, dat, block=False):
    self._d[key] = dat

  def put_bool(self, key, val, block=False):
    self._d[key] = bool(val)

  def remove(self, key):
    self._d.pop(key, None)


def ensure_msgq_stub() -> None:
  """Stub the compiled msgq/cereal messaging layer.

  lock_setup imports ui_state, which imports cereal.messaging -> msgq.ipc_pyx (a compiled
  extension absent off-device). Only import-time resolution is needed; the tests never send
  or receive messages.
  """
  try:
    import msgq.ipc_pyx  # noqa: F401
    return
  except Exception:
    pass
  if "msgq.ipc_pyx" in sys.modules:
    return

  ipc = types.ModuleType("msgq.ipc_pyx")
  for name in ("Context", "Poller", "SubSocket", "PubSocket", "MultiplePublishersError",
               "IpcError", "SocketEventHandle", "toggle_fake_events", "set_fake_prefix",
               "get_fake_prefix", "delete_fake_prefix", "wait_for_one_event"):
    # Classes, not lambdas: cereal.messaging evaluates annotations like `Poller | None` at
    # import time, and `|` needs a type on the left-hand side.
    setattr(ipc, name, type(name, (Exception,), {}) if "Error" in name else type(name, (), {}))
  sys.modules["msgq.ipc_pyx"] = ipc


def ensure_headless_ui() -> None:
  """Keep the UI import chain from probing for a real display.

  GuiApplication is constructed at application.py import time; on PC with SCALE unset its
  __init__ auto-detects a display scale by opening a throwaway raylib window to measure the
  monitor - a hard crash on a headless machine. Pinning SCALE skips the probe entirely.
  setdefault so an explicitly chosen SCALE is respected.
  """
  os.environ.setdefault("SCALE", "1")


def ensure_params_stub() -> None:
  """Install the stub if the real Params can't load. Safe to call repeatedly.

  openpilot.common.params is a ctypes wrapper: on an unbuilt tree the import itself raises
  OSError (dlopen of libparams_c fails); ImportError covers the module being absent. Either
  way the stub goes in.
  """
  try:
    from openpilot.common.params import Params  # noqa: F401
    return  # on-device / CI: use the real thing
  except (ImportError, OSError):
    pass

  if "openpilot.common.params" in sys.modules:
    return

  stub = types.ModuleType("openpilot.common.params")
  stub.Params = StubParams
  stub.ParamKeyFlag = object
  stub.ParamKeyType = object
  stub.UnknownKeyName = KeyError
  sys.modules["openpilot.common.params"] = stub
