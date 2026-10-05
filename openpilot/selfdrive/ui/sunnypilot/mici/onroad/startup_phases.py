"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

HL-FEAT(startup-rail): the state behind the onroad startup rail. Pure Python on purpose:
no pyray, no ui_state, no cereal, so it runs under pytest on the Mac and on the device.

Six segments, each a REAL milestone of this device's boot (measured on routes 000000f0/ed,
seconds after ignition): cameras up ~1 s, selfdrived up ~6 s, modeld's main loop ~15-18 s,
big model loaded on the eGPU ~34-37 s, small model loaded ~37-40 s, first big-model frame
right after. Nothing here is a timer pretending to be progress. "ready" needs the first
modelV2 frame that came from the big model, the same edge as the Ready To Drive alert.

The rail doubles as a health strip: a process the manager expects to be running but which
is not shows "<name> not running" in the fault colour. The manager in this tree does not
restart a crashed onroad process, so that normally stays up until the next ignition.
"""
from dataclasses import dataclass
from enum import Enum

# the steps, in boot order. Process steps light when the manager reports that process running this
# session (or not scheduled at all, e.g. maneuver/joystick dev modes); the processes themselves come up within ~2 s on this device, so on a good boot they are
# already lit when the camera page appears. They are here because they have failed before (r20:
# plannerd crash-looped): the strip then stalls on "starting plannerd" and goes amber.
STEPS = (
  ("cameras", "starting cameras"),
  ("card", "starting card"),
  ("selfdrived", "starting selfdrived"),
  ("plannerd", "starting plannerd"),
  ("controlsd", "starting controlsd"),
  ("modeld", "starting modeld"),          # modeld's main loop, i.e. the ChestnutLoading / bigModelLoading edge
  ("bigmodel", "loading big model"),      # the long one: big model into the eGPU, ~20 s
  ("smallmodel", "loading small model"),
  ("model", "starting model"),            # both models in; waiting for the first big-model frame
)
SEGMENTS = len(STEPS)
LABELS = tuple(label for _, label in STEPS)
BIG_MODEL_STEP = [k for k, _ in STEPS].index("bigmodel")   # the lit count during which the elapsed counter shows
# manager process names per process step. The modeld step is deliberately NOT here: its process is
# up at +1.2 s but its main loop (the python/tinygrad import) takes until +15-18 s, which is what
# "starting modeld" should narrate, so that step keys on the loading edge below.
STEP_PROCESSES = {"card": ("card",), "selfdrived": ("selfdrived",), "plannerd": ("plannerd",), "controlsd": ("controlsd",)}
READY_HOLD_S = 2.5     # how long the green "ready" stays before fading (matches the old chestnut icon)
FAILED_HOLD_S = 5.0    # how long an amber big-model message stays before fading
DEAD_PROCESS_DEBOUNCE_S = 0.5   # one managerState period: filters the start()->is_alive() frame of a launch
# only processes the drive depends on are worth an amber strip for the rest of the drive (the manager
# in this tree never restarts a crashed one); helpers like mapd/uploaders/sunnylink are left to their
# own alerts. Display names keep the strip readable in a 240 px panel.
CRITICAL_PROCESSES: dict[str, str] = {
  "camerad": "camerad", "sensord": "sensord", "pandad": "pandad", "card": "card",
  "modeld": "modeld", "modeld_tinygrad": "modeld", "dmonitoringmodeld": "dmonitoringd", "dmonitoringd": "dmonitoringd",
  "selfdrived": "selfdrived", "controlsd": "controlsd", "plannerd": "plannerd", "radard": "radard",
  "locationd": "locationd", "calibrationd": "calibrationd", "paramsd": "paramsd", "torqued": "torqued", "lagd": "lagd",
}

# GPU badge look per ui_state.ChestnutState value, kept here (pure) so it is testable and never
# touches pyray's colour constants, which are plain tuples in this raylib and have no .r/.g/.b
# (that exact mistake would kill the UI process on the first onroad frame). (bg rgb, fg rgb, breathe)
BADGE_STYLE: dict[str, tuple[tuple[int, int, int], tuple[int, int, int], bool]] = {
  "loading": ((72, 72, 72), (255, 255, 255), True),
  "active": ((0, 255, 38), (0, 0, 0), False),
  "uncompiled": ((255, 175, 3), (0, 0, 0), False),
  "failed": ((255, 175, 3), (0, 0, 0), False),
}


class RailMode(Enum):
  HIDDEN = "hidden"       # offroad, no dock: draw nothing, forget everything
  STARTING = "starting"
  READY = "ready"
  FAILED = "failed"       # big model failed, not compiled, or never attempted
  FAULT = "fault"         # a process the manager expects is not running


@dataclass(frozen=True)
class RailInputs:
  t: float                        # monotonic seconds
  started: bool                   # onroad
  chestnut_present: bool          # dock attached this session
  camera_seen: bool               # a road camera frame arrived this session
  selfdrive_seen: bool            # selfdriveState arrived this session
  big_model_loading: bool         # bigModelLoading event / ChestnutLoading param
  chestnut_active: bool | None    # ChestnutActive param: None until modeld writes it
  chestnut_failed: bool           # ui_state says the big model failed
  model_big_seen: bool            # a modelV2 frame with big=True arrived this session
  model_small_seen: bool = False  # a modelV2 frame with big=False arrived this session
  chestnut_uncompiled: bool = False  # dock present but no compiled big model
  dead_process: str | None = None  # a process that should be running but is not (debounced)
  procs_ok: frozenset[str] = frozenset()  # manager process names running, or not scheduled this session (dev modes
                                          # swap plannerd/controlsd out; an unscheduled step must not stall the strip)


@dataclass(frozen=True)
class RailState:
  mode: RailMode
  lit: int                        # segments lit, 0..SEGMENTS
  label: str
  elapsed_s: float | None         # seconds spent in the current step, only for the big-model load
  visible: bool                   # draw it (before the widget's own fade)


class DeadProcessTracker:
  """Debounces the manager's 'should be running but is not' set, keeps only the drive-critical
  processes, and returns the display name of the one dead longest."""

  def __init__(self):
    self._since: dict[str, float] = {}

  def reset(self) -> None:
    self._since.clear()

  def update(self, missing: set[str], t: float) -> str | None:
    missing = {m for m in missing if m in CRITICAL_PROCESSES}
    for name in list(self._since):
      if name not in missing:
        del self._since[name]
    for name in missing:
      self._since.setdefault(name, t)
    dead = [(since, name) for name, since in self._since.items() if t - since >= DEAD_PROCESS_DEBOUNCE_S]
    return CRITICAL_PROCESSES[min(dead)[1]] if dead else None


class StartupPhases:
  def __init__(self):
    self.reset()

  def reset(self) -> None:
    self._done: set[str] = set()
    self._lit = 0
    self._loading_seen = False
    self._step_t: float | None = None
    self._ready_t: float | None = None
    self._failed_t: float | None = None

  def _hold(self, start_attr: str, t: float, hold_s: float) -> bool:
    if getattr(self, start_attr) is None:
      setattr(self, start_attr, t)
    return t - getattr(self, start_attr) < hold_s

  def _satisfied_now(self, i: RailInputs) -> set[str]:
    """Steps whose signal is present in THIS frame; latched into _done by update()."""
    done = set()
    if i.camera_seen:
      done.add("cameras")
    for step, names in STEP_PROCESSES.items():
      if any(n in i.procs_ok for n in names):
        done.add(step)
    if i.selfdrive_seen:
      done.add("selfdrived")               # a selfdriveState message is stronger proof than the 2 Hz manager list
    if i.big_model_loading:
      self._loading_seen = True
    if self._loading_seen:
      done.add("modeld")                   # modeld is in its main loop once it raises the loading flag
      if i.chestnut_active is True:
        done.add("bigmodel")
      if not i.big_model_loading:
        done |= {"bigmodel", "smallmodel"}   # loading ended: the big model is in and the small one too
    if i.model_big_seen:
      done |= {k for k, _ in STEPS}        # the first big frame proves the whole chain
    return done

  def update(self, i: RailInputs) -> RailState:
    if not i.started or not i.chestnut_present:
      self.reset()
      return RailState(RailMode.HIDDEN, 0, "", None, False)

    # milestones latch: a stale or missing message never un-lights a segment. lit counts the steps
    # satisfied in ORDER from the bottom, so a step that never happens stalls the strip on its label.
    self._done |= self._satisfied_now(i)
    lit = 0
    for key, _ in STEPS:
      if key not in self._done:
        break
      lit += 1
    if lit != self._lit:
      self._lit = lit
      self._step_t = i.t

    # a dead process is the most specific story at any point (modeld dying mid-load would otherwise
    # leave the elapsed counter climbing forever; after ready it also flips the chestnut to failed)
    if i.dead_process:
      self._ready_t = None       # re-arm the ready hold in case it ever comes back
      self._failed_t = None
      return RailState(RailMode.FAULT, lit, f"{i.dead_process} not running", None, True)

    # the big model failing, not being compiled, or never being attempted beats the milestones
    if i.chestnut_failed or (self._loading_seen and i.chestnut_active is False):
      return RailState(RailMode.FAILED, lit, "big model failed", None, self._hold("_failed_t", i.t, FAILED_HOLD_S))
    if i.chestnut_uncompiled or (i.model_small_seen and not self._loading_seen):
      return RailState(RailMode.FAILED, lit, "big model not loaded", None, self._hold("_failed_t", i.t, FAILED_HOLD_S))
    self._failed_t = None

    if lit >= SEGMENTS:
      return RailState(RailMode.READY, lit, "ready", None, self._hold("_ready_t", i.t, READY_HOLD_S))

    elapsed = (i.t - self._step_t) if (lit == BIG_MODEL_STEP and self._step_t is not None) else None
    return RailState(RailMode.STARTING, lit, LABELS[lit], elapsed, True)
