"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.startup_phases import (
  SEGMENTS, LABELS, STEPS, BIG_MODEL_STEP, READY_HOLD_S, READY_WORD_S, MODEL_NAME_S, FAILED_HOLD_S,
  DEAD_PROCESS_DEBOUNCE_S, BADGE_STYLE, CRITICAL_PROCESSES, DeadProcessTracker, RailInputs, RailMode, StartupPhases,
  short_model_name,
)

PROCS = frozenset({"card", "selfdrived", "plannerd", "controlsd", "modeld_tinygrad", "modeld", "radard", "pandad"})


def _in(t=0.0, started=True, dock=True, cam=False, sd=False, loading=False, active=None, failed=False, big=False,
        small=False, uncompiled=False, dead=None, running=None, names=()):
  # by default every process is up (that is the measured reality from +1.2 s); tests that care pass their own set
  return RailInputs(t=t, started=started, chestnut_present=dock, camera_seen=cam, selfdrive_seen=sd,
                    big_model_loading=loading, chestnut_active=active, chestnut_failed=failed,
                    model_big_seen=big, model_small_seen=small, chestnut_uncompiled=uncompiled, dead_process=dead,
                    procs_ok=PROCS if running is None else frozenset(running), model_names=tuple(names))


def _boot(p, t0=0.0):
  """Replay the measured boot order; returns the state after each milestone."""
  out = [p.update(_in(t=t0, running=()))]
  out.append(p.update(_in(t=t0 + 1, cam=True, running=())))
  out.append(p.update(_in(t=t0 + 1.1, cam=True, running=("card",))))
  out.append(p.update(_in(t=t0 + 1.2, cam=True, running=("card", "selfdrived"))))
  out.append(p.update(_in(t=t0 + 1.4, cam=True, running=("card", "selfdrived", "plannerd"))))
  out.append(p.update(_in(t=t0 + 1.6, cam=True, running=("card", "selfdrived", "plannerd", "controlsd"))))
  out.append(p.update(_in(t=t0 + 6, cam=True, sd=True)))
  out.append(p.update(_in(t=t0 + 15, cam=True, sd=True, loading=True)))
  out.append(p.update(_in(t=t0 + 34, cam=True, sd=True, loading=True, active=True)))
  out.append(p.update(_in(t=t0 + 37, cam=True, sd=True, loading=False, active=True)))
  out.append(p.update(_in(t=t0 + 37.1, cam=True, sd=True, loading=False, active=True, big=True)))
  return out


class TestMilestones:
  def test_measured_boot_lights_one_segment_per_step(self):
    s = _boot(StartupPhases())
    assert [x.lit for x in s] == [0, 1, 2, 3, 4, 5, 5, 6, 7, 8, SEGMENTS]
    assert [x.label for x in s] == ["cameras", "card", "selfdrived", "plannerd", "controlsd", "modeld", "modeld",
                                    "big model", "small model", "first frame", "ready"]
    assert all(x.mode == RailMode.STARTING for x in s[:-1])
    assert all(x.visible for x in s)                                   # the strip is drawn through the whole boot
    assert [x.elapsed_s is None for x in s] == [x.lit != BIG_MODEL_STEP for x in s]
    assert s[-1].mode == RailMode.READY

  def test_steps_table_is_consistent(self):
    assert SEGMENTS == len(STEPS) == 9 and len(set(LABELS)) == SEGMENTS   # every step reads differently, so the drum turns
    assert LABELS[BIG_MODEL_STEP] == "big model"

  def test_a_process_that_never_starts_stalls_the_strip_on_its_name(self):
    # the r20 case: plannerd crash-looping. Later steps must not paper over the hole.
    p = StartupPhases()
    s = p.update(_in(t=6, cam=True, sd=True, running=("card", "selfdrived", "controlsd")))
    assert s.lit == 3 and s.label == "plannerd" and s.mode == RailMode.STARTING
    s = p.update(_in(t=15, cam=True, sd=True, loading=True, running=("card", "selfdrived", "controlsd")))
    assert s.lit == 3 and s.label == "plannerd"                        # modeld progressing does not hide it
    s = p.update(_in(t=16, cam=True, sd=True, loading=True, running=("card", "selfdrived", "controlsd"), dead="plannerd"))
    assert s.mode == RailMode.FAULT and s.label == "plannerd not running" and s.lit == 3   # amber bars show where it stopped

  def test_a_process_the_manager_did_not_schedule_is_not_a_stall(self):
    # maneuver / joystick dev modes swap plannerd or controlsd out: the manager lists them as neither
    # running nor expected, and the widget passes them as ok
    s = StartupPhases().update(_in(t=6, cam=True, sd=True, running=("card", "selfdrived", "plannerd", "controlsd")))
    assert s.lit == 5 and s.label == "modeld"

  def test_stronger_signals_imply_process_steps(self):
    # selfdriveState arriving proves selfdrived even if the 2 Hz manager list lags; the first big frame proves all
    s = StartupPhases().update(_in(t=6, cam=True, sd=True, running=("card", "plannerd", "controlsd")))
    assert s.lit == 5 and s.label == "modeld"
    # ... but a hole lower down still stops the count (consecutive, not max): card missing here
    s = StartupPhases().update(_in(t=6, cam=True, sd=True, running=("plannerd", "controlsd")))
    assert s.lit == 1 and s.label == "card"
    # the modeld step never keys on the process being up, under either runner name
    s = StartupPhases().update(_in(t=6, cam=True, sd=True, running=PROCS | {"modeld"}))
    assert s.lit == 5 and s.label == "modeld" and s.elapsed_s is None
    s = StartupPhases().update(_in(t=40, cam=True, big=True, running=()))
    assert s.mode == RailMode.READY and s.lit == SEGMENTS

  def test_elapsed_counts_from_the_start_of_the_big_model_load(self):
    p = StartupPhases()
    p.update(_in(t=15, cam=True, sd=True, loading=True))
    s = p.update(_in(t=27, cam=True, sd=True, loading=True))
    assert s.lit == BIG_MODEL_STEP and s.elapsed_s == 12
    assert p.update(_in(t=34, cam=True, sd=True, loading=True, active=True)).elapsed_s is None

  def test_segments_latch_against_stale_inputs(self):
    p = StartupPhases()
    p.update(_in(t=6, cam=True, sd=True))
    assert p.update(_in(t=7, cam=False, sd=False, running=())).lit == 5   # a dropped frame never un-lights anything

  def test_loading_end_implies_both_model_steps(self):
    # ChestnutActive is polled at 5 Hz; the loading end can arrive first and must not leave a hole
    p = StartupPhases()
    p.update(_in(t=15, cam=True, sd=True, loading=True))
    assert p.update(_in(t=37, cam=True, sd=True, loading=False, active=None)).lit == SEGMENTS - 1

  def test_ui_restart_mid_drive_goes_straight_to_ready(self):
    s = StartupPhases().update(_in(t=100, cam=True, sd=True, active=True, big=True))
    assert s.mode == RailMode.READY and s.lit == SEGMENTS and s.visible

  def test_ready_holds_then_hides(self):
    p = StartupPhases()
    _boot(p)
    assert p.update(_in(t=37.1 + READY_HOLD_S - 0.1, cam=True, sd=True, active=True, big=True)).visible
    assert not p.update(_in(t=37.1 + READY_HOLD_S + 0.1, cam=True, sd=True, active=True, big=True)).visible

  def test_second_ignition_starts_a_fresh_hold_and_counter(self):
    p = StartupPhases()
    _boot(p)
    p.update(_in(t=60, cam=True, sd=True, active=True, big=True))     # old hold long expired
    p.update(_in(t=70, started=False))                                 # ignition off
    s = _boot(p, t0=100)
    assert s[-1].mode == RailMode.READY and s[-1].visible              # not inheriting the old timestamp
    assert not p.update(_in(t=137.1 + READY_HOLD_S + 0.1, cam=True, sd=True, active=True, big=True)).visible
    p.update(_in(t=200, started=False))
    p.update(_in(t=215, cam=True, sd=True, loading=True))
    assert p.update(_in(t=220, cam=True, sd=True, loading=True)).elapsed_s == 5   # counter restarted too


class TestFailuresAndFaults:
  def test_big_model_failed_overrides_and_holds(self):
    p = StartupPhases()
    p.update(_in(t=15, cam=True, sd=True, loading=True))
    s = p.update(_in(t=75, cam=True, sd=True, loading=False, active=False))   # BIG_MODEL_TIMEOUT path
    assert s.mode == RailMode.FAILED and s.label == "big model failed" and s.visible
    assert not p.update(_in(t=75 + FAILED_HOLD_S + 0.1, cam=True, sd=True, loading=False, active=False)).visible

  def test_a_stale_false_before_any_loading_is_not_a_failure(self):
    # ChestnutActive=False can linger from a previous drive until modeld removes it
    assert StartupPhases().update(_in(t=6, cam=True, sd=True, active=False)).mode == RailMode.STARTING

  def test_failed_then_cleared_then_failed_again_shows_again(self):
    p = StartupPhases()
    p.update(_in(t=40, cam=True, sd=True, failed=True))
    p.update(_in(t=40 + FAILED_HOLD_S + 1, cam=True, sd=True, failed=True))
    p.update(_in(t=50, cam=True, sd=True))                              # cleared
    assert p.update(_in(t=60, cam=True, sd=True, failed=True)).visible  # a fresh hold, not the old expired one

  def test_ui_state_failed_flag_is_enough(self):
    assert StartupPhases().update(_in(t=40, cam=True, sd=True, failed=True)).mode == RailMode.FAILED

  def test_dock_without_a_compiled_or_attempted_big_model(self):
    s = StartupPhases().update(_in(t=20, cam=True, sd=True, uncompiled=True))
    assert s.mode == RailMode.FAILED and s.label == "big model not loaded"
    # small-model frames flowing with no load ever seen: modeld skipped the big model
    s = StartupPhases().update(_in(t=20, cam=True, sd=True, small=True))
    assert s.mode == RailMode.FAILED and s.label == "big model not loaded"
    # ... but small frames are no concern once a load was seen (the normal boot never sends them first)
    p = StartupPhases()
    p.update(_in(t=15, cam=True, sd=True, loading=True))
    assert p.update(_in(t=16, cam=True, sd=True, loading=True, small=True)).mode == RailMode.STARTING

  def test_dead_process_is_a_fault_at_any_point(self):
    p = StartupPhases()
    p.update(_in(t=15, cam=True, sd=True, loading=True))
    s = p.update(_in(t=27, cam=True, sd=True, loading=True, dead="modeld"))   # died mid-load: no endless counter
    assert s.mode == RailMode.FAULT and s.label == "modeld not running" and s.elapsed_s is None and s.lit == BIG_MODEL_STEP
    p = StartupPhases()
    _boot(p)
    s = p.update(_in(t=200, cam=True, sd=True, active=True, big=True, dead="plannerd"))
    assert s.mode == RailMode.FAULT and s.label == "plannerd not running" and s.visible and s.lit == SEGMENTS
    s = p.update(_in(t=203, cam=True, sd=True, active=True, big=True))
    assert s.mode == RailMode.READY and s.visible                    # a fresh green hold if it ever recovers
    assert not p.update(_in(t=203 + READY_HOLD_S + 0.1, cam=True, sd=True, active=True, big=True)).visible

  def test_dead_modeld_wins_over_the_chestnut_failed_flag(self):
    p = StartupPhases()
    _boot(p)
    s = p.update(_in(t=200, cam=True, sd=True, failed=True, big=True, dead="modeld"))
    assert s.mode == RailMode.FAULT and s.label == "modeld not running"


class TestHidden:
  def test_offroad_or_no_dock_hides_and_forgets(self):
    p = StartupPhases()
    _boot(p)
    assert p.update(_in(t=50, started=False)).mode == RailMode.HIDDEN
    s = p.update(_in(t=51, cam=True))                                 # next ignition starts from scratch
    assert s.mode == RailMode.STARTING and s.lit == 5               # cameras + the four processes (all up by default)
    assert StartupPhases().update(_in(t=0, dock=False, cam=True)).mode == RailMode.HIDDEN


class TestDeadProcessTracker:
  def test_debounce_boundary_and_cleanup(self):
    d = DeadProcessTracker()
    assert d.update({"plannerd"}, 10.0) is None
    assert d.update({"plannerd"}, 10.0 + DEAD_PROCESS_DEBOUNCE_S - 0.01) is None
    assert d.update({"plannerd"}, 10.0 + DEAD_PROCESS_DEBOUNCE_S) == "plannerd"
    assert d.update(set(), 11.0) is None                               # recovered
    assert d.update({"plannerd"}, 11.5) is None                        # a new episode debounces again

  def test_longest_dead_wins(self):
    d = DeadProcessTracker()
    d.update({"modeld"}, 0.0)
    d.update({"modeld", "calibrationd"}, 0.3)
    assert d.update({"modeld", "calibrationd"}, 1.0) == "modeld"

  def test_only_drive_critical_processes_count_and_get_display_names(self):
    d = DeadProcessTracker()
    d.update({"mapd", "sunnylink_registration_manager", "uploader"}, 0.0)
    assert d.update({"mapd", "sunnylink_registration_manager", "uploader"}, 5.0) is None   # helpers never pin the strip
    d.update({"modeld_tinygrad"}, 10.0)
    assert d.update({"modeld_tinygrad"}, 11.0) == "modeld"                               # readable in the panel
    for name in ("camerad", "selfdrived", "controlsd", "plannerd", "card", "pandad", "radard", "dmonitoringmodeld"):
      assert name in CRITICAL_PROCESSES

  def test_startup_launch_frame_is_filtered(self):
    # the one managerState frame between start() and is_alive() during the onroad burst
    d = DeadProcessTracker()
    assert d.update({"controlsd"}, 0.0) is None
    assert d.update(set(), 0.5) is None


class TestBadgeStyle:
  def test_covers_exactly_the_drawn_chestnut_states(self):
    assert set(BADGE_STYLE) == {"loading", "active", "uncompiled", "failed"}
    for bg, fg, breathe in BADGE_STYLE.values():
      assert all(0 <= c <= 255 for c in bg + fg) and isinstance(breathe, bool)
    assert BADGE_STYLE["loading"][2] and not BADGE_STYLE["active"][2]
    assert BADGE_STYLE["uncompiled"][0] == BADGE_STYLE["failed"][0]    # one amber for both bad states


class TestReadyNamesTheModels:
  BIG = "Cinque Terre Model V2 (September 08, 2026)"
  SMALL = "Terrible Super Fantastic Do Over Model (August 15, 2026)"

  def _ready_at(self, t):
    p = StartupPhases()
    _boot(p)
    def at(dt):
      return p.update(_in(t=37.1 + dt, cam=True, sd=True, active=True, big=True, names=(self.BIG, self.SMALL)))
    return p, at

  def test_short_names(self):
    assert short_model_name(self.BIG) == "Cinque Terre V2"
    assert short_model_name(self.SMALL) == "Terrible Super Fantastic Do Over"
    assert short_model_name("") == "" and short_model_name("Plain") == "Plain"

  def test_ready_then_each_model_then_fade(self):
    _, at = self._ready_at(0)
    assert at(0.0).label == "ready" and at(0.0).visible
    assert at(READY_WORD_S - 0.05).label == "ready"
    assert at(READY_WORD_S + 0.05).label == "Cinque Terre V2"
    assert at(READY_WORD_S + MODEL_NAME_S + 0.05).label == "Terrible Super Fantastic Do Over"
    s = at(READY_WORD_S + 2 * MODEL_NAME_S - 0.05)
    assert s.visible and s.mode == RailMode.READY
    assert not at(READY_WORD_S + 2 * MODEL_NAME_S + 0.05).visible

  def test_no_names_keeps_the_plain_ready_hold(self):
    p = StartupPhases()
    _boot(p)
    s = p.update(_in(t=37.1 + READY_HOLD_S - 0.1, cam=True, sd=True, active=True, big=True))
    assert s.label == "ready" and s.visible
    assert not p.update(_in(t=37.1 + READY_HOLD_S + 0.1, cam=True, sd=True, active=True, big=True)).visible

  def test_only_big_model_known(self):
    p = StartupPhases()
    _boot(p)
    def at(dt):
      return p.update(_in(t=37.1 + dt, cam=True, sd=True, active=True, big=True, names=(self.BIG,)))
    assert at(READY_WORD_S + 0.5).label == "Cinque Terre V2"
    assert not at(READY_WORD_S + MODEL_NAME_S + 0.05).visible
