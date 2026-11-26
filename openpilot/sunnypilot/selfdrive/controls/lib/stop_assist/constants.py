"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

HL-FEAT(stop-assist): every tunable of Stop Assist phase 1 and where it comes from. Measured numbers are
from the owner's Highlander logs (~/rlogs/analysis_1126_stops/results/, 2026-11-26), open loop on older builds.
Units: m, s, m/s, m/s^2, m/s^3. Durations are measured on modelV2.logMonoTime, never by counting frames.
"""

# --- planned stop (stop_point.planned_stop) ---
LIGHTS_STOP_V = 1.0         # plan minimum below this is a planned stop: comma's own stop bar uses 1.0; 0.5 flickers on Light Speed v2
MAX_STOP_DIST = 100.0       # planned stops farther than this are ignored (the arm rule replayed over 21.6 moving hours used 100 m)
EOF_AGE_MAX_S = 0.2         # clip of the camera-frame age at plannerd: median 55 ms, max seen 157 ms

# --- stock stop/go thresholds (drive_helpers.should_stop, modeld action.shouldStop; pinned by test_pinned_constants) ---
STOP_V = 0.3                # below this speed a target under GO_ACCEL is a stop request
GO_ACCEL = 0.1              # a model action at or above this is the model asking to go

# --- arming (IDLE -> CONFIRM) ---
ARM_V_MIN = 3.0             # the arm rule's speed band (3-20 m/s); below it the plan minimum is mostly the car's own speed
ARM_V_MAX = 20.0
ARM_MODEL_ACCEL = -0.15     # arm only while the model is already braking (arm rule: 207 arms, 90 % then below 2 m/s within 20 s)
MAX_ARM_NEED = 2.2          # refuse stops already too late at arm (uncapped need above this): the model and the driver handle those
MIN_EFF_M = 0.2             # floor of the effective distance in the need formula: keeps it finite at or past the target

# --- CONFIRM ---
CONFIRM_S = 0.5             # earliest confirm after arming, by time
CONFIRM_FRAMES = 10         # window: the last 10 model frames (0.5 s at 20 Hz) ...
CONFIRM_MIN = 8             # ... of which at least 8 must have a planned stop ...
CONFIRM_SPREAD = 3.0        # ... agreeing within 3.0 m after dropping the worst one (frame scatter 0.78 m SD, single steps to 4.4 m)

# --- tracker (stop_point.StopPointTracker) ---
MEDIAN_N = 7                # median of the last 7 raw points (frame scatter 0.44 m, jumps to 4.4 m): final error +0.14 m over 77 lights
FAR_FWD_RATE = 0.5          # beyond NEAR_M the target may move away from the car at most this fast: caps a retreat caused by our own braking
NEAR_M = 20.0               # within this remaining distance the target is anchored ...
NEAR_FWD = 1.0              # ... and may move at most +1.0 m away from the anchor ...
NEAR_BACK = 1.5             # ... or -1.5 m toward the car (median error drifts -0.85 m between 20-30 m and 2-6 m out)
FREEZE_M = 6.0              # freeze the target at this remaining distance ...
FREEZE_V = 2.5              # ... or below this speed: past that point the plan mostly reflects the car's own deceleration
NEW_STOP_M = 5.0            # a planned stop this far beyond the target ...
NEW_STOP_S = 0.5            # ... for this long is a different stop (the next light): ramp out

# --- control law ---
LAT_S = 0.25                # measured command-to-aEgo lag 0.21 s + a frame
RESERVE_M = 0.3             # longcontrol's stopping state engages with 0.02-0.13 m left
A_CAP = 2.5                 # deepest commanded deceleration: the owner's p90 peak
A_MAX = 2.0                 # no entry above ACCEL_MAX (opendbc car/interfaces.py)
JERK_IN = 1.0               # slew toward more braking: gentler than his own 1.4-1.7 m/s^3 (family comfort)
JERK_OUT = 2.0              # slew toward less braking
RAMP_OUT_JERK = 1.5         # RAMP_OUT rises at this rate instead of deleting a braking entry in one frame
RAMP_END_MARGIN = 0.05      # RAMP_OUT ends once the entry is this far above the model's candidate ...
RAMP_END_ACCEL = 0.5        # ... or at this accel, whichever first: from then the entry could no longer be the minimum

# --- plan dropout (no planned stop in a frame) during APPROACH/FINAL ---
DROP_CLAMP = 0.2            # once the plan has lost the stop, never brake harder than the model's action minus this
DROP_GREEN_S = 0.5          # green: no planned stop for this long ...
DROP_GREEN_ACCEL = -0.2     # ... with the model no longer braking (action at or above this) ...
DROP_RISE = 0.1             # ... or the action has risen this much since the dropout started
DROP_MAX_S = 2.5            # stop lost: keep at most this long while the model still brakes

# --- low speed, hold and release ---
LOW_V_GO = 1.5              # below this the plan minimum is degenerate (accelerating model: 'no stop' 0.0 % at 0-0.3 m/s, 1.1 % at 0.3-1) ...
LOW_V_GO_S = 0.3            # ... so FINAL's green is the model's go held this long
HOLD_MIN_S = 1.0            # a hold lasts at least this long (it bridges the measured 50-100 ms stop-flag dropouts) ...
GO_CONFIRM_S = 0.5          # ... and releases once the model's go has held continuously this long
RELEASED_TRAVEL_M = 10.0    # after a release or the accelerator, re-arm only after this much travel ...
RELEASED_V_MIN = 3.0        # ... at or above this speed ...
RELEASED_CLEAR_S = 0.5      # ... with no planned stop for this long (one stop per light)

# --- lead, model, faults ---
LEAD_MARGIN_M = 10.0        # a lead nearer than the stop point + 10 m blocks: the lead planner owns that stop
LEAD_AWAY_VREL = 2.0        # ... unless it is pulling away by 2 m/s or more
MODEL_GAP_S = 0.25          # model frames are 50 ms apart (max 68 ms seen, none over 75 ms): a longer gap drops to IDLE
MODEL_SWITCH_BLOCK_S = 2.0  # after modelV2.big changes, no arming for this long (the other model's plan is not yet trusted)
FAULT_ALERT_S = 3.0         # the "Stop Assist Off" alert shows this long after a fault

# --- settings (params_keys.h: StopAssistLights, StopAssistLightsOffsetFt) ---
OFFSET_MIN_FT = 0           # owner, 2026-11-26: the range must allow 0 ft ...
OFFSET_MAX_FT = 10
OFFSET_DEFAULT_FT = 3       # ... and start at 3 ft
FT_TO_M = 0.3048
