"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
# HL-FEAT(stop-assist): stock constants Stop Assist's numbers were derived from. Read as source text (nothing heavy is
# imported). If one changes upstream, re-check constants.py before cutting a release:
#  - should_stop (v_ego < 0.3 and a_target < 0.1): our entry's stop flag and STOP_V/GO_ACCEL mirror it,
#  - modeld's action.shouldStop (the same thresholds): model_go and the hold release are built on it,
#  - longcontrol's stopping ramp (1.0 m/s^3) and the stopAccel default (-2.0, Toyota keeps it): how the car comes to
#    rest from the stop flag (RESERVE_M) and how hard a hold presses.
# unittest.TestCase, setUp only.
import ast
import importlib.util
import os
import re
import unittest

import openpilot

from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist import constants as C

OPENPILOT = os.path.dirname(os.path.abspath(openpilot.__file__))
OPENDBC = os.path.dirname(os.path.abspath(importlib.util.find_spec("opendbc").origin))
DRIVE_HELPERS = os.path.join(OPENPILOT, "selfdrive", "controls", "lib", "drive_helpers.py")
LONGCONTROL = os.path.join(OPENPILOT, "selfdrive", "controls", "lib", "longcontrol.py")
MODELD_V2 = os.path.join(OPENPILOT, "sunnypilot", "modeld_v2", "modeld.py")
MODELD = os.path.join(OPENPILOT, "selfdrive", "modeld", "modeld.py")
CAR_INTERFACES = os.path.join(OPENDBC, "car", "interfaces.py")
TOYOTA_INTERFACE = os.path.join(OPENDBC, "car", "toyota", "interface.py")


def _read(path):
  with open(path) as f:
    return f.read()


def _function(path, name):
  for node in ast.walk(ast.parse(_read(path))):
    if isinstance(node, ast.FunctionDef) and node.name == name:
      return node
  raise AssertionError(f"{name} not found in {path}")


class TestPinnedConstants(unittest.TestCase):
  def test_should_stop_thresholds(self):
    fn = _function(DRIVE_HELPERS, "should_stop")
    assert [a.arg for a in fn.args.args] == ["v_ego", "a_target"]
    ret = [ast.unparse(n.value) for n in ast.walk(fn) if isinstance(n, ast.Return)]
    assert ret == ["bool(v_ego < 0.3 and a_target < 0.1)"], ret
    assert (C.STOP_V, C.GO_ACCEL) == (0.3, 0.1)

  def test_modeld_v2_stop_flag(self):
    src = _read(MODELD_V2)
    assert len(re.findall(r"^\s+stop = v_ego < 0\.3 and desired_accel < 0\.1$", src, re.M)) == 1
    assert "shouldStop=bool(stop)" in src

  def test_stock_modeld_stop_flag(self):
    src = _read(MODELD)
    assert len(re.findall(r"^\s+stop = should_stop\(v_ego, desired_accel\)$", src, re.M)) == 1
    assert "shouldStop=bool(stop))" in src

  def test_longcontrol_stopping_ramp(self):
    fn = _function(LONGCONTROL, "update")
    text = ast.unparse(fn)
    start = text.index("elif self.long_control_state == LongCtrlState.stopping:")
    branch = text[start:text.index("else:", start)]
    assert "if output_accel > self.CP.stopAccel:" in branch
    assert "output_accel -= 1.0 * DT_CTRL" in branch, branch

  def test_stop_accel_default_and_toyota_keeps_it(self):
    assert len(re.findall(r"^\s+ret\.stopAccel = -2\.0$", _read(CAR_INTERFACES), re.M)) == 1
    assert "stopAccel" not in _read(TOYOTA_INTERFACE)
