import collections
import queue
import subprocess
import threading
import time
from typing import List, Optional

import pytest

from twitch_audio_streamer import TwitchAudioStreamer


class MockWs:
    """Mock WebSocket for recording audio sends."""

    def __init__(self):
        self.sent_data: List[bytes] = []
        self.send_exception: Optional[Exception] = None

    def send(self, data: bytes) -> None:
        if self.send_exception:
            raise self.send_exception
        self.sent_data.append(data)


class MockStdout:
    """Mock stdout stream."""

    def __init__(self, proc: "MockPopen", data: bytes = b"", block_on_read: bool = False):
        self.proc = proc
        self.data = data
        self.offset = 0
        self.block_on_read = block_on_read
        self.closed = False

    def read(self, n: int = 3840) -> bytes:
        if self.proc.terminated or self.proc.killed:
            return b""
        if self.block_on_read:
            self.proc.read_started_event.set()
            self.proc.unblock_event.wait(timeout=5.0)
            return b""
        if self.offset >= len(self.data):
            return b""
        chunk = self.data[self.offset : self.offset + n]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class MockStderr:
    """Mock stderr stream."""

    def __init__(self, lines: Optional[List[bytes]] = None):
        self.lines = list(lines) if lines else []
        self.closed = False

    def readline(self) -> bytes:
        if not self.lines:
            return b""
        return self.lines.pop(0)

    def close(self) -> None:
        self.closed = True


class BoundedPipeStderr:
    """Simulates a bounded OS pipe buffer for stderr (>4KB writer would block without drain)."""

    def __init__(self, maxsize: int = 2):
        self._q = queue.Queue(maxsize=maxsize)
        self.closed = False

    def write_line(self, line: bytes, timeout: float = 3.0) -> None:
        self._q.put(line, timeout=timeout)

    def close_write(self) -> None:
        self._q.put(b"")

    def readline(self) -> bytes:
        try:
            return self._q.get(timeout=3.0)
        except queue.Empty:
            return b""

    def close(self) -> None:
        self.closed = True


class MockPopen:
    """Mock subprocess.Popen for TwitchAudioStreamer tests."""

    def __init__(
        self,
        stdout_data: bytes = b"",
        stderr_lines: Optional[List[bytes]] = None,
        block_on_read: bool = False,
        custom_stderr: Optional[object] = None,
    ):
        self.stdout_data = stdout_data
        self.block_on_read = block_on_read
        self.read_started_event = threading.Event()
        self.unblock_event = threading.Event()

        self.terminated = False
        self.killed = False
        self.returncode: Optional[int] = None

        self.stdout = MockStdout(self, data=stdout_data, block_on_read=block_on_read)
        self.stderr = custom_stderr if custom_stderr is not None else MockStderr(stderr_lines)

    def poll(self) -> Optional[int]:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15
        self.unblock_event.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self.unblock_event.set()


def test_empty_channel_raises_value_error():
    """Verify that an empty channel name raises ValueError."""
    ws = MockWs()
    with pytest.raises(ValueError, match="Twitch channel is empty"):
        TwitchAudioStreamer(ws=ws, channel="")


def test_drain_stderr_static_method():
    """Directly test _drain_stderr helper with various line ending formats and edge cases."""
    raw_lines = [
        b"[error] line 1\n",
        b"[error] line 2\r\n",
        b"[warning] line 3 with \rmultiple parts\n",
        b"   \n",  # whitespace only, should be ignored
        b"[fatal] final error\n",
    ]
    proc = MockPopen(stderr_lines=raw_lines)
    log = collections.deque(maxlen=20)
    TwitchAudioStreamer._drain_stderr(proc, log)

    assert "[error] line 1" in log
    assert "[error] line 2" in log
    assert "[warning] line 3 with" in log
    assert "multiple parts" in log
    assert "[fatal] final error" in log
    assert len(log) == 5


def test_stderr_drained_continuously_without_deadlock(monkeypatch):
    """T-01: Verify that stderr is continuously drained (>4KB) and does not block stdout reads."""
    ws = MockWs()
    streamer = TwitchAudioStreamer(ws=ws, channel="testchannel")

    monkeypatch.setattr(streamer, "_resolve_stream_url", lambda: "http://fake-stream-url")

    # Generate >4KB of stderr lines (150 lines * ~39 bytes = ~5850 bytes)
    num_lines = 150
    stderr_lines = [f"[error] FFmpeg error payload line {i:04d}\n".encode("utf-8") for i in range(num_lines)]
    total_stderr_bytes = sum(len(l) for l in stderr_lines)
    assert total_stderr_bytes > 4096  # Exceeds Windows 4KB pipe buffer

    audio_chunk = b"\x00\x01" * 3840  # 7680 bytes = 1 chunk
    mock_proc = MockPopen(stdout_data=audio_chunk * 2, stderr_lines=stderr_lines)

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: mock_proc)

    streamer.start()

    # Wait for streamer thread to consume data and finish the iteration
    for _ in range(50):
        if len(ws.sent_data) >= 2 and len(streamer._error_log) > 0:
            break
        time.sleep(0.05)

    streamer.stop()

    assert len(ws.sent_data) == 2
    assert ws.sent_data[0] == audio_chunk
    assert ws.sent_data[1] == audio_chunk

    # Verify stderr drain thread existed and consumed stderr lines
    assert streamer._stderr_thread is not None
    assert len(streamer._error_log) == 20  # deque maxlen=20 capped
    # Last line should be line 149
    assert "line 0149" in streamer._error_log[-1]


def test_bounded_pipe_stderr_drain_allows_producer_to_finish(monkeypatch):
    """T-01: Verify that a bounded stderr pipe (writer blocks if buffer full) drains successfully."""
    ws = MockWs()
    streamer = TwitchAudioStreamer(ws=ws, channel="testchannel")
    monkeypatch.setattr(streamer, "_resolve_stream_url", lambda: "http://fake-stream-url")

    bounded_stderr = BoundedPipeStderr(maxsize=2)
    audio_chunk = b"\x02\x00" * 3840
    mock_proc = MockPopen(stdout_data=audio_chunk, custom_stderr=bounded_stderr)

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: mock_proc)

    # Background thread writes 50 lines to the bounded stderr pipe.
    # If streamer didn't drain stderr, this thread would block on the 3rd line.
    writer_finished = threading.Event()

    def write_many_lines():
        for i in range(50):
            bounded_stderr.write_line(f"bounded-err-{i}\n".encode("utf-8"))
        bounded_stderr.close_write()
        writer_finished.set()

    writer_thread = threading.Thread(target=write_many_lines, daemon=True)
    writer_thread.start()

    streamer.start()

    writer_finished.wait(timeout=3.0)
    assert writer_finished.is_set(), "Stderr writer blocked! Deadlock detected without stderr drainage."

    # Allow audio processing
    for _ in range(50):
        if len(ws.sent_data) >= 1:
            break
        time.sleep(0.05)

    streamer.stop()
    assert len(ws.sent_data) == 1
    assert "bounded-err-49" in streamer._error_log[-1]


def test_stop_terminates_blocking_process_and_exits_thread(monkeypatch):
    """T-02: Verify that stop() interrupts a blocked stdout.read(), calls terminate(), and cleans up."""
    ws = MockWs()
    streamer = TwitchAudioStreamer(ws=ws, channel="testchannel")
    monkeypatch.setattr(streamer, "_resolve_stream_url", lambda: "http://fake-stream-url")

    # block_on_read=True simulates ffmpeg stream stalled where stdout.read() blocks
    mock_proc = MockPopen(block_on_read=True)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: mock_proc)

    streamer.start()

    # Wait until process is spawned and blocked in stdout.read()
    assert mock_proc.read_started_event.wait(timeout=3.0)
    assert streamer._process is mock_proc
    worker_thread = streamer._thread
    assert worker_thread is not None and worker_thread.is_alive()

    # Calling stop() should terminate the process and allow the worker thread to exit promptly
    stop_start = time.time()
    streamer.stop()
    elapsed = time.time() - stop_start

    assert mock_proc.terminated is True
    assert elapsed < 2.0, f"stop() took {elapsed:.2f}s, should terminate immediately"
    assert streamer._thread is None
    assert streamer._process is None
    assert not worker_thread.is_alive()


def test_stop_secondary_kill_if_terminate_does_not_exit(monkeypatch):
    """T-02: Verify that if terminate() does not stop the process, stop() escalates to kill()."""
    ws = MockWs()
    streamer = TwitchAudioStreamer(ws=ws, channel="testchannel")

    mock_proc = MockPopen()
    # Override terminate to not change poll status (simulate stubborn process)
    def stubborn_terminate():
        mock_proc.terminated = True
        # poll stays None

    mock_proc.terminate = stubborn_terminate
    streamer._process = mock_proc

    # Create a dummy thread that stays alive during the first join
    class StubbornThread:
        def __init__(self):
            self._joins = 0

        def is_alive(self):
            return True

        def join(self, timeout=None):
            self._joins += 1

    dummy_thread = StubbornThread()
    streamer._thread = dummy_thread  # type: ignore

    streamer.stop()

    assert mock_proc.terminated is True
    assert mock_proc.killed is True
    assert dummy_thread._joins == 2


def test_normal_eof_path(monkeypatch):
    """Verify that normal EOF from stdout completes cleanly and sends all audio."""
    ws = MockWs()
    streamer = TwitchAudioStreamer(ws=ws, channel="testchannel")
    monkeypatch.setattr(streamer, "_resolve_stream_url", lambda: "http://fake-stream-url")

    chunk1 = b"\x01\x00" * 3840
    chunk2 = b"\x02\x00" * 3840
    chunk3 = b"\x03\x00" * 3840
    mock_proc = MockPopen(stdout_data=chunk1 + chunk2 + chunk3)

    # After first iteration, set stop_event so it does not loop infinitely
    orig_popen = lambda *args, **kwargs: mock_proc
    monkeypatch.setattr(subprocess, "Popen", orig_popen)

    streamer.start()

    for _ in range(50):
        if len(ws.sent_data) == 3:
            break
        time.sleep(0.05)

    streamer.stop()

    assert ws.sent_data == [chunk1, chunk2, chunk3]
    assert mock_proc.stdout.closed is True
    assert streamer._process is None


def test_ffmpeg_error_printed_on_exit(monkeypatch, capsys):
    """Verify that ffmpeg stderr messages are printed when the stream exits."""
    ws = MockWs()
    streamer = TwitchAudioStreamer(ws=ws, channel="testchannel")
    monkeypatch.setattr(streamer, "_resolve_stream_url", lambda: "http://fake-stream-url")

    stderr_lines = [b"Invalid audio PTS\n", b"Stream disconnect\n"]
    mock_proc = MockPopen(stdout_data=b"", stderr_lines=stderr_lines)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: mock_proc)

    # Run one iteration of _run directly
    def stop_after_one():
        time.sleep(0.1)
        streamer._stop_event.set()

    threading.Thread(target=stop_after_one, daemon=True).start()
    streamer._run()

    captured = capsys.readouterr()
    assert "ffmpeg error: Invalid audio PTS\nStream disconnect" in captured.out


def test_exponential_backoff_on_failure(monkeypatch):
    """Verify exponential backoff when stream URL resolution fails repeatedly."""
    ws = MockWs()
    streamer = TwitchAudioStreamer(ws=ws, channel="testchannel")

    attempts = 0
    delays: List[float] = []

    def failing_resolve():
        nonlocal attempts
        attempts += 1
        if attempts >= 4:
            streamer._stop_event.set()
        raise RuntimeError(f"Twitch channel offline (attempt {attempts})")

    monkeypatch.setattr(streamer, "_resolve_stream_url", failing_resolve)

    # Intercept _stop_event.wait to record wait times without real sleeping
    orig_wait = streamer._stop_event.wait

    def fake_wait(timeout=None):
        if timeout is not None:
            delays.append(timeout)
        if streamer._stop_event.is_set():
            return True
        return False

    monkeypatch.setattr(streamer._stop_event, "wait", fake_wait)

    streamer._run()

    # Backoff sequence: 2.0s, 4.0s, 8.0s
    assert delays == [2.0, 4.0, 8.0]


def test_backoff_resets_after_successful_resolution(monkeypatch):
    """Verify backoff resets back to 2.0s after a successful resolution."""
    ws = MockWs()
    streamer = TwitchAudioStreamer(ws=ws, channel="testchannel")

    resolutions = [
        RuntimeError("offline 1"),
        "http://valid-stream-url",
        RuntimeError("offline 2"),
    ]
    delays: List[float] = []

    def dynamic_resolve():
        if not resolutions:
            streamer._stop_event.set()
            raise RuntimeError("done")
        res = resolutions.pop(0)
        if isinstance(res, Exception):
            raise res
        return res

    monkeypatch.setattr(streamer, "_resolve_stream_url", dynamic_resolve)

    chunk = b"\x00" * 7680
    mock_proc = MockPopen(stdout_data=chunk)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: mock_proc)

    def fake_wait(timeout=None):
        if timeout is not None:
            delays.append(timeout)
        if not resolutions:
            streamer._stop_event.set()
            return True
        return False

    monkeypatch.setattr(streamer._stop_event, "wait", fake_wait)

    streamer._run()

    # 1. First failure -> delay 2.0s (retry_delay becomes 4.0)
    # 2. Success -> retry_delay reset to 2.0, normal delay wait 1.0s
    # 3. Second failure -> delay 2.0s (retry_delay becomes 4.0)
    assert delays[0] == 2.0
    assert delays[1] == 1.0
    assert delays[2] == 2.0


def test_filenotfound_ffmpeg_exits_cleanly(monkeypatch, capsys):
    """Verify that FileNotFoundError (missing ffmpeg) terminates _run immediately."""
    ws = MockWs()
    streamer = TwitchAudioStreamer(ws=ws, channel="testchannel")
    monkeypatch.setattr(streamer, "_resolve_stream_url", lambda: "http://fake-stream-url")

    def raise_fnf(*args, **kwargs):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(subprocess, "Popen", raise_fnf)

    streamer._run()

    captured = capsys.readouterr()
    assert "ffmpeg not found" in captured.out
    assert streamer._process is None
