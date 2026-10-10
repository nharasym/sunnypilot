# HL-FIX(audio-retry): micd/soundd keep retrying the audio device instead of exiting after 30 s
from openpilot.system.micd import open_stream_retrying, reinit_portaudio, start_stream, STREAM_RETRY_DELAY_S, STREAM_UNAVAILABLE_ATTEMPTS


class _Flaky:
  """Fails `failures` times like a PortAudio open before the audio DSP is up, then opens."""
  def __init__(self, failures: int):
    self.failures = failures
    self.calls = 0

  def __call__(self):
    self.calls += 1
    if self.calls <= self.failures:
      raise RuntimeError("Error opening OutputStream: Unanticipated host error")
    return "stream"


def _run(failures: int):
  sleeps: list[float] = []
  flags: list[int] = []
  fn = _Flaky(failures)
  out = open_stream_retrying(fn, "test", lambda: flags.append(fn.calls), sleep=sleeps.append)
  return out, fn, sleeps, flags


class TestOpenStreamRetrying:
  def test_upstream_budget_is_unchanged(self):
    # the warning threshold is exactly where upstream used to give up and exit
    assert STREAM_UNAVAILABLE_ATTEMPTS == 10 and STREAM_RETRY_DELAY_S == 3.

  def test_opens_first_try_without_sleeping_or_flagging(self):
    out, fn, sleeps, flags = _run(0)
    assert out == "stream" and fn.calls == 1 and sleeps == [] and flags == []

  def test_dock_boot_inside_the_old_budget_never_flags(self):
    # measured dock boot (route 10c): soundd opened on its 7th attempt
    out, fn, sleeps, flags = _run(6)
    assert out == "stream" and fn.calls == 7 and flags == []
    assert sleeps == [STREAM_RETRY_DELAY_S] * 6

  def test_last_attempt_of_the_old_budget_still_flags_nothing_if_it_opens(self):
    out, fn, _, flags = _run(STREAM_UNAVAILABLE_ATTEMPTS - 1)
    assert fn.calls == STREAM_UNAVAILABLE_ATTEMPTS and flags == []

  def test_no_dock_boot_keeps_retrying_and_flags_every_retry_past_the_budget(self):
    # measured no-dock boots: the sound card was ready 1-3 s after upstream's 10th attempt
    out, fn, sleeps, flags = _run(STREAM_UNAVAILABLE_ATTEMPTS + 2)
    assert out == "stream" and fn.calls == STREAM_UNAVAILABLE_ATTEMPTS + 3
    assert flags == [10, 11, 12], "re-asserted on every retry so a cleared flag comes back"
    assert len(sleeps) == STREAM_UNAVAILABLE_ATTEMPTS + 2

  def test_a_long_outage_still_opens_eventually(self):
    out, fn, _, flags = _run(400)  # 20 minutes of retries
    assert out == "stream" and fn.calls == 401 and len(flags) == 400 - STREAM_UNAVAILABLE_ATTEMPTS + 1

  def test_failing_callback_does_not_stop_the_retries(self):
    fn = _Flaky(STREAM_UNAVAILABLE_ATTEMPTS + 1)
    def bad():
      raise OSError("params write failed")
    assert open_stream_retrying(fn, "test", bad, sleep=lambda s: None) == "stream"

  def test_stop_signal_during_open_is_not_swallowed(self):
    # the manager stops processes with SIGINT; a retry loop that ate it would hang shutdown until SIGKILL
    def interrupted():
      raise KeyboardInterrupt
    try:
      open_stream_retrying(interrupted, "test", sleep=lambda s: None)
    except KeyboardInterrupt:
      return
    raise AssertionError("KeyboardInterrupt was swallowed")

  def test_stop_signal_during_the_wait_is_not_swallowed(self):
    def sleep(s):
      raise KeyboardInterrupt
    try:
      open_stream_retrying(_Flaky(5), "test", sleep=sleep)
    except KeyboardInterrupt:
      return
    raise AssertionError("KeyboardInterrupt was swallowed")


class _FakeSd:
  def __init__(self, initialized: int):
    self._initialized = initialized
    self.calls: list[str] = []

  def _terminate(self):
    if not self._initialized:
      raise RuntimeError("Error terminating PortAudio: PortAudio not initialized [PaErrorCode -10000]")
    self._initialized -= 1
    self.calls.append("terminate")

  def _initialize(self):
    self._initialized += 1
    self.calls.append("initialize")


class _FakeStream:
  def __init__(self, start_fails: bool):
    self.start_fails = start_fails
    self.closed = False

  def start(self):
    if self.start_fails:
      raise RuntimeError("Error starting stream")

  def close(self):
    self.closed = True


class TestPortAudioReinit:
  def test_initialized_portaudio_is_terminated_then_reinitialized(self):
    sd = _FakeSd(1)
    reinit_portaudio(sd)
    assert sd.calls == ["terminate", "initialize"] and sd._initialized == 1

  def test_after_a_failed_initialize_the_next_retry_still_reinitializes(self):
    # upstream called _terminate unconditionally, which raises here and wedged every later retry
    sd = _FakeSd(0)
    reinit_portaudio(sd)
    assert sd.calls == ["initialize"] and sd._initialized == 1


class TestStartStream:
  def test_started_stream_is_returned_open(self):
    st = _FakeStream(start_fails=False)
    assert start_stream(st) is st and not st.closed

  def test_start_failure_closes_and_raises_so_the_open_is_retried(self):
    st = _FakeStream(start_fails=True)
    try:
      start_stream(st)
    except RuntimeError:
      assert st.closed
      return
    raise AssertionError("a start failure must reach the retry loop")
