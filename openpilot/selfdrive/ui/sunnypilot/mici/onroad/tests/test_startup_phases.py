"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.selfdrive.ui.sunnypilot.mici.onroad.startup_phases import (
  SEGMENTS, LABELS, READY_HOLD_S, FAILED_HOLD_S, DEAD_PROCESS_DEBOUNCE_S, BADGE_STYLE, CRITICAL_PROCESSES,
  DeadProcessTracker, RailInputs, RailMode, StartupPhases,
)


def _in(t=0.0, started=True, dock=True, cam=False, sd=False, loading=False, active=None, failed=False, big=False,
        small=False, uncompiled=False, dead=None):
  return RailInputs(t=t, started=started, chestnut_present=dock, camera_seen=cam, selfdrive_seen=sd,
                    big_model_loading=loading, chestnut_active=active, chestnut_failed=failed,
                    model_big_seen=big, model_small_seen=small, chestnut_uncompiled=uncompiled, dead_process=dead)


def _boot(p, t0=0.0):
  """Replay the measured boot order; returns the state after each milestone."""
  out = [p.update(_in(t=t0))]
  out.append(p.update(_in(t=t0 + 1, cam=True)))
  out.append(p.update(_in(t=t0 + 6, cam=True, sd=True)))
  out.append(p.update(_in(t=t0 + 15, cam=True, sd=True, loading=True)))
  out.append(p.update(_in(t=t0 + 34, cam=True, sd=True, loading=True, active=True)))
  out.append(p.update(_in(t=t0 + 37, cam=True, sd=True, loading=False, active=True)))
  out.append(p.update(_in(t=t0 + 37.1, cam=True, sd=True, loading=False, active=True, big=True)))
  return out


class TestMilestones:
  def test_measured_boot_lights_one_segment_per_milestone(self):
    s = _boot(StartupPhases())
    assert [x.lit for x in s] == [0, 1, 2, 3, 4, 5, SEGMENTS]
    assert [x.label for x in s] == ["starting cameras", "starting sunnypilot", "starting modeld", "loading big model",
                                    "loading small model", "starting model", "ready"]
    assert all(x.mode == RailMode.STARTING for x in s[:-1])
    assert all(x.visible for x in s)                                   # the strip is drawn through the whole boot
    assert [x.elapsed_s is None for x in s] == [True, True, True, False, True, True, True]
    assert s[-1].mode == RailMode.READY

  def test_labels_table_matches_the_segment_count(self):
    assert len(LABELS) == SEGMENTS and len(set(LABELS)) == SEGMENTS   # every step reads differently, so the drum turns

  def test_elapsed_counts_from_the_start_of_the_big_model_load(self):
    p = StartupPhases()
    p.update(_in(t=15, cam=True, sd=True, loading=True))
    s = p.update(_in(t=27, cam=True, sd=True, loading=True))
    assert s.lit == 3 and s.elapsed_s == 12
    assert p.update(_in(t=34, cam=True, sd=True, loading=True, active=True)).elapsed_s is None

  def test_segments_latch_against_stale_inputs(self):
    p = StartupPhases()
    p.update(_in(t=6, cam=True, sd=True))
    assert p.update(_in(t=7, cam=False, sd=False)).lit == 2          # a dropped frame never un-lights anything

  def test_loading_end_implies_both_model_steps(self):
    # ChestnutActive is polled at 5 Hz; the loading end can arrive first and must not leave a hole
    p = StartupPhases()
    p.update(_in(t=15, cam=True, sd=True, loading=True))
    assert p.update(_in(t=37, cam=True, sd=True, loading=False, active=None)).lit == 5

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
    assert s.mode == RailMode.FAULT and s.label == "modeld not running" and s.elapsed_s is None
    p = StartupPhases()
    _boot(p)
    s = p.update(_in(t=200, cam=True, sd=True, active=True, big=True, dead="plannerd"))
    assert s.mode == RailMode.FAULT and s.label == "plannerd not running" and s.visible
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
    assert s.mode == RailMode.STARTING and s.lit == 1
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
