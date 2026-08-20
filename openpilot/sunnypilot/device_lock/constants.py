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
PARAM_PIN_FORMAT = "DeviceLockPinFormat"             # INT, PERSISTENT|BACKUP - which PIN alphabet the stored hash uses

# params we drive to force the car back to stock while locked
PARAM_OFFROAD_MODE = "OffroadMode"                   # BOOL, CLEAR_ON_MANAGER_START (re-asserted by us)

# read-only: authoritative onroad state, written by system/manager/helpers.py on the onroad
# transition. Used to refuse locking mid-drive (locking forces OffroadMode, which would close the
# panda relay and drop control).
PARAM_IS_ONROAD = "IsOnroad"

# offroad alert key (registered in selfdrive/selfdrived/alerts_offroad.json)
OFFROAD_ALERT_LOCKED = "Offroad_DeviceLocked"

# --- PIN policy ---
# The PIN is a sequence of the four controller-style symbols (cross / circle / triangle / square),
# not digits. Four big targets beat twelve cramped ones on mici's 536x240 screen, and a shape
# pattern is easy to remember.
#
# Internally a PIN is stored as the characters "0".."3" (indices into PIN_SYMBOLS), so the hashing
# and comparison code is alphabet-agnostic - only the rendered glyph and the alphabet check differ.
#
# Threat model, per the owner: stop a dealership from *using* openpilot on a test drive or while
# shuffling the car around the lot. Not a determined attacker with the car all day. 4 symbols x 4
# positions = 256 combinations, and the rate limiter makes casual guessing pointless.
PIN_ALPHABET = "0123"
PIN_SYMBOLS = ("cross", "circle", "triangle", "square")

PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 8

# Bumped whenever the alphabet changes, so a hash written under an older scheme is not silently
# unmatchable. has_pin() treats a stale format as "no PIN", which fails safe: the lock screen says
# to unlock from the dashboard rather than stranding the owner with input that can never match.
PIN_FORMAT_SYMBOLS = 1
PIN_FORMAT_CURRENT = PIN_FORMAT_SYMBOLS

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
