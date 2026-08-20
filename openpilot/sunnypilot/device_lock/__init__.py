"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): public API for the device lock. See lock.py for the design notes.

from openpilot.sunnypilot.device_lock.lock import DeviceLock, PinError, validate_pin, hash_pin, check_pin
from openpilot.sunnypilot.device_lock.constants import (
  PARAM_LOCKED,
  PARAM_PIN_HASH,
  OFFROAD_ALERT_LOCKED,
  PIN_MIN_LENGTH,
  PIN_MAX_LENGTH,
)

__all__ = [
  "DeviceLock",
  "PinError",
  "validate_pin",
  "hash_pin",
  "check_pin",
  "PARAM_LOCKED",
  "PARAM_PIN_HASH",
  "OFFROAD_ALERT_LOCKED",
  "PIN_MIN_LENGTH",
  "PIN_MAX_LENGTH",
]
