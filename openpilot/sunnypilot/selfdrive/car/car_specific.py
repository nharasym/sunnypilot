"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from openpilot.cereal import log, custom
from opendbc.car import structs

from opendbc.car.chrysler.values import RAM_DT
from openpilot.common.params import Params
from openpilot.selfdrive.selfdrived.events import Events
from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

EventName = log.OnroadEvent.EventName
EventNameSP = custom.OnroadEventSP.EventName
GearShifter = structs.CarState.GearShifter

# HL-FEAT(bsm-approaching) tuning. Runs at selfdrived rate (100Hz).
BSM_APPROACHING_MIN_SPEED = 5.0   # m/s (~11 mph); the radar itself only reports above ~10 mph
BSM_REARM_FRAMES = 100            # a side must be clear this long (1s) before it can chime again
BSM_CHIME_HOLD_FRAMES = 150       # keep the alert up 1.5s per threat
BSM_RETRIGGER_GAP_FRAMES = 15     # drop the alert this long when a new threat lands mid-hold: soundd
                                  # replays only on alert identity change, so without the gap a second
                                  # approach during the hold would be visually merged and audibly silent
BSM_PARAM_READ_FRAMES = 100       # re-read the toggle every 1s


class CarSpecificEventsSP:
  def __init__(self, CP: structs.CarParams, CP_SP: structs.CarParamsSP, params: Params | None = None):
    self.CP = CP
    self.CP_SP = CP_SP
    self.params = params or Params()

    self.low_speed_alert = False

    self.frame = 0
    self.bsm_alert_enabled = self.params.get_bool("BsmApproachingAlert")
    self.bsm_hold_frames = {"left": 0, "right": 0}
    self.bsm_gap_frames = 0
    self.bsm_cond_prev = {"left": False, "right": False}
    # start re-armed so the first approach of a drive chimes
    self.bsm_clear_frames = {"left": BSM_REARM_FRAMES, "right": BSM_REARM_FRAMES}

  def update(self, CS: structs.CarState, CS_SP, events: Events):
    events_sp = EventsSP()

    self.frame += 1
    if self.frame % BSM_PARAM_READ_FRAMES == 0:
      self.bsm_alert_enabled = self.params.get_bool("BsmApproachingAlert")

    if self.CP.brand == 'chrysler':
      if self.CP.carFingerprint in RAM_DT:
        # remove belowSteerSpeed event from CarSpecificEvents as RAM_DT uses a different logic
        if events.has(EventName.belowSteerSpeed):
          events.remove(EventName.belowSteerSpeed)

        # TODO-SP: use if/elif to have the gear shifter condition takes precedence over the speed condition
        # TODO-SP: add 1 m/s hysteresis
        if CS.vEgo >= self.CP.minEnableSpeed:
          self.low_speed_alert = False
        if self.CP.minEnableSpeed >= 14.5 and CS.gearShifter != GearShifter.drive:
          self.low_speed_alert = True
      if self.low_speed_alert:
        events.add(EventName.belowSteerSpeed)

    elif self.CP.brand == 'toyota':
      if self.CP.openpilotLongitudinalControl:
        if CS.cruiseState.standstill and not CS.brakePressed and self.CP_SP.enableGasInterceptor:
          if events.has(EventName.resumeRequired):
            events.remove(EventName.resumeRequired)

      # HL-FEAT(bsm-approaching): audible alert when the driver signals toward a side where the
      # factory BSM radar flags a fast-APPROACHING vehicle (closing from the rear quarter, not
      # yet adjacent). Upstream already folds APPROACHING into left/rightBlindspot for
      # lane-change blocking; this is the audible counterpart for driver-initiated merges,
      # keyed on APPROACHING alone so it stays quiet about visible adjacent traffic.
      # Edge-triggered per side with a re-arm dwell: one chime per approach, not a continuous
      # nag. A new threat landing while the alert is already up forces a short alert gap so
      # the sound replays (soundd keys on alert identity) — every threat gets its own chime.
      if self.bsm_alert_enabled and CS_SP is not None:
        for side, blinker, approaching in (("left", CS.leftBlinker, CS_SP.leftBlindspotApproaching),
                                           ("right", CS.rightBlinker, CS_SP.rightBlindspotApproaching)):
          cond = blinker and approaching and CS.vEgo > BSM_APPROACHING_MIN_SPEED
          if cond and not self.bsm_cond_prev[side] and self.bsm_clear_frames[side] >= BSM_REARM_FRAMES:
            if self.bsm_gap_frames > 0 or any(self.bsm_hold_frames.values()):
              self.bsm_gap_frames = BSM_RETRIGGER_GAP_FRAMES
            self.bsm_hold_frames[side] = BSM_CHIME_HOLD_FRAMES
          self.bsm_clear_frames[side] = 0 if cond else min(self.bsm_clear_frames[side] + 1, BSM_REARM_FRAMES)
          self.bsm_cond_prev[side] = cond

      if self.bsm_gap_frames > 0:
        self.bsm_gap_frames -= 1
      elif any(self.bsm_hold_frames.values()):
        for side in ("left", "right"):
          if self.bsm_hold_frames[side] > 0:
            self.bsm_hold_frames[side] -= 1
        events_sp.add(EventNameSP.bsmApproaching)

    return events_sp
