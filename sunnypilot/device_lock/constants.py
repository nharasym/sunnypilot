"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

# HL-FEAT(device-lock): all tunables/param names for the device lock live here so a re-port
# onto a new sunnypilot release only has to move this package + re-add the tagged hook lines.

# --- params (declared in common/params_keys.h) ---
PARAM_LOCKED = "DeviceLocked"                        # BOOL, PERSISTENT|BACKUP - source of truth
PARAM_PIN_HASH = "DeviceLockPinHash"                 # STRING, PERSISTENT|BACKUP - "salt$hash", never plaintext
PARAM_ATTEMPTS = "DeviceLockAttempts"                # INT, PERSISTENT - consecutive failed PIN attempts
PARAM_PREV_OFFROAD = "DeviceLockPrevOffroadMode"     # BOOL, PERSISTENT - OffroadMode before locking, restored on unlock

# params we drive to force the car back to stock while locked
PARAM_OFFROAD_MODE = "OffroadMode"                   # BOOL, CLEAR_ON_MANAGER_START (re-asserted by us)

# offroad alert key (registered in selfdrive/selfdrived/alerts_offroad.json)
OFFROAD_ALERT_LOCKED = "Offroad_DeviceLocked"

# --- PIN policy ---
PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 8

# PBKDF2-HMAC-SHA256. Cheap enough for a 536x240 device UI, plenty for a 4-8 digit PIN
# guarded by rate limiting (the PIN space is small either way - the real defence is the
# lockout below, not the KDF cost).
PIN_KDF_ITERATIONS = 200_000
PIN_SALT_BYTES = 16

# --- rate limiting ---
# After this many consecutive wrong PINs, entry is refused for a cooldown that doubles each
# time (60s, 120s, 240s ... capped). Attempts persist across reboot; the cooldown timer is
# in-memory on purpose - a reboot costs more time than it saves an attacker, and it avoids
# depending on wall-clock (which we've already been bitten by, see the GPS fix-age bug).
RATE_LIMIT_AFTER_ATTEMPTS = 5
RATE_LIMIT_BASE_SECONDS = 60.0
RATE_LIMIT_MAX_SECONDS = 3600.0
