"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): shared params_pyx stub for the off-device tests.
#
# Single definition on purpose. Each test module used to install its own, and because
# sys.modules is first-write-wins, whichever imported first decided whether the stub was
# functional - so the suite passed or errored depending on collection order.
#
# The stub must actually WORK, not merely exist: importing mount pulls in the UI widget chain
# (pin_screen -> widgets -> multilang), which constructs a real Params at import time.
#
# Signatures mirror common/params_pyx.pyx exactly. A double that is more permissive than the
# real API hides real bugs - that is precisely how Params.get(key, encoding="utf8") reached the
# device and crash-looped the UI. Keep these strict.

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


def ensure_params_stub() -> None:
  """Install the stub if the compiled extension isn't present. Safe to call repeatedly."""
  try:
    from openpilot.common.params_pyx import Params  # noqa: F401
    return  # on-device / CI: use the real thing
  except ModuleNotFoundError:
    pass

  if "openpilot.common.params_pyx" in sys.modules:
    return

  stub = types.ModuleType("openpilot.common.params_pyx")
  stub.Params = StubParams
  stub.ParamKeyFlag = object
  stub.ParamKeyType = object
  stub.UnknownKeyName = KeyError
  sys.modules["openpilot.common.params_pyx"] = stub
