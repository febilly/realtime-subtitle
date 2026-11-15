"""Overlay processes must not outlive the main app, however it ends."""

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object")


def _alive(pid: int) -> bool:
    from realtime_subtitle.outputs import win_overlay_native

    return win_overlay_native.is_process_alive(pid)


def _wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


@windows_only
def test_bound_child_dies_when_main_process_is_killed():
    # Stand-in for the main app: starts a long-running child, binds it to its
    # own lifetime, reports the child's PID, then waits to be killed.
    holder_code = textwrap.dedent(
        """
        import subprocess, sys, time
        from realtime_subtitle.outputs.child_job import bind_to_app_lifetime
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        print(child.pid, bind_to_app_lifetime(child.pid), flush=True)
        time.sleep(120)
        """
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code], cwd=ROOT, stdout=subprocess.PIPE, text=True,
    )
    child_pid = None
    try:
        child_pid_text, bound = holder.stdout.readline().split()
        child_pid = int(child_pid_text)
        assert bound == "True"
        assert _alive(child_pid)

        holder.kill()  # like "End task": no shutdown code runs in the holder
        holder.wait(timeout=10)

        assert _wait_until(lambda: not _alive(child_pid))
    finally:
        if holder.poll() is None:
            holder.kill()
        if child_pid and _alive(child_pid):
            subprocess.run(["taskkill", "/PID", str(child_pid), "/F"], capture_output=True)


def test_bind_ignores_missing_process():
    from realtime_subtitle.outputs.child_job import bind_to_app_lifetime

    assert bind_to_app_lifetime(0) is False


def test_parent_watch_follows_the_given_process():
    pytest.importorskip("PySide6")
    from realtime_subtitle.outputs.overlay_window import ParentWatch

    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        watch = ParentWatch(proc.pid)
        assert watch.alive()
        proc.kill()
        proc.wait(timeout=10)
        assert _wait_until(lambda: not watch.alive())
    finally:
        if proc.poll() is None:
            proc.kill()


def test_parent_watch_treats_an_already_gone_parent_as_dead():
    pytest.importorskip("PySide6")
    from realtime_subtitle.outputs.overlay_window import ParentWatch

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=10)
    pid = proc.pid
    del proc  # release our handle so the process object can go away
    assert ParentWatch(pid).alive() is False


def test_overlay_command_passes_main_pid(monkeypatch):
    # server.py loads .env at import; keep the developer's .env out of the tests.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delitem(sys.modules, "server", raising=False)
    import server

    manager = server.OverlayManager("http://127.0.0.1:1")
    command = manager._build_command(hidden=True)
    assert command[command.index("--parent-pid") + 1] == str(os.getpid())
    vr_command = server.VrOverlayManager("http://127.0.0.1:1")._build_command(hidden=False)
    assert "--parent-pid" in vr_command
