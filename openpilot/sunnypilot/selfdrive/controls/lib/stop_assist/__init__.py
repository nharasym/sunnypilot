"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

HL-FEAT(stop-assist): model-planned stops with no car ahead (lights, crosswalks). See stop_assist.py.
"""
from openpilot.sunnypilot.selfdrive.controls.lib.stop_assist.stop_assist import SOURCE_E2E, StopAssist

__all__ = ["SOURCE_E2E", "StopAssist"]
