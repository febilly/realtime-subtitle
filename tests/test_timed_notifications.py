import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

import local_store
import timed_notifications as notices


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(notices.config, "LOCAL_MODEL_STAR_REMINDER", "auto")
    monkeypatch.setattr(notices.config, "TIMED_NOTIFICATION_COOLDOWN_SECONDS", 14400)


def usage_days(count, start=datetime(2026, 10, 1, 12)):
    for index in range(count):
        stamp = (start + timedelta(days=index * 2)).timestamp()
        notices.record_usage(stamp)
        notices.record_usage(stamp + 60)
    return stamp + 120


def test_counts_distinct_usage_dates_not_elapsed_or_session_days():
    now = usage_days(6)
    assert notices.snapshot(now)["usage_days"] == 6
    assert notices.claim("local_star", now) == {"shown": False}
    now += 3 * 86400
    notices.record_usage(now)
    assert notices.snapshot(now)["usage_days"] == 7
    assert notices.claim("local_star", now)["star_shown_count"] == 1
    assert notices.snapshot(now)["usage_days"] == 0
    # Reset must not count the same day a second time, including after reopening.
    notices.record_usage(now + 120)
    assert notices.snapshot(now)["usage_days"] == 0
    state = json.loads(local_store.load()[notices.STATE_KEY])
    assert state["last_use"] == now + 120


def test_midnight_and_backward_clock():
    first = datetime(2026, 10, 1, 23, 59).timestamp()
    notices.record_usage(first)
    notices.record_usage(first + 120)
    notices.record_usage(first - 86400)
    assert notices.snapshot(first + 120)["usage_days"] == 2


def test_byok_and_hosted_local_share_days_but_hosted_cloud_does_not_count():
    first = datetime(2026, 10, 1, 12).timestamp()
    notices.record_usage(first, relay_mode=False)
    notices.record_usage(first + 60, relay_mode=True, local_model=True)
    assert notices.snapshot(first)["usage_days"] == 1
    notices.record_usage(first + 86400, relay_mode=True)
    assert notices.snapshot(first)["usage_days"] == 1
    notices.record_usage(first + 2 * 86400, relay_mode=True, local_model=True)
    assert notices.snapshot(first)["usage_days"] == 2


def test_shared_cooldown_preserves_due_days_and_pending_server_notice():
    now = usage_days(7)
    token = notices.remember_server_notice({"id": "soniox_quota"})
    assert notices.remember_server_notice({"id": "soniox_quota"}) == token
    assert notices.claim("server", now, notice_id=token)["shown"] is True
    for kind in ("local_star", "invite", "update"):
        assert notices.claim(kind, now + 14399)["shown"] is False
    assert notices.snapshot(now)["usage_days"] == 7
    assert notices.snapshot(now)["notices"] == []
    assert notices.claim("local_star", now + 14400)["shown"] is True


def test_server_notice_waits_across_refresh_until_successful_claim():
    now = 1000000
    notices.claim("invite", now)
    token = notices.remember_server_notice({"id": "soniox_quota"})
    assert notices.claim("server", now + 1, notice_id=token)["shown"] is False
    assert notices.snapshot(now)["notices"][0]["notice_id"] == token
    assert notices.claim("server", now + 14400, notice_id=token)["shown"] is True
    assert notices.claim("server", now + 28800, notice_id=token)["shown"] is False


def test_second_display_and_permanent_opt_out():
    now = usage_days(7)
    assert notices.claim("local_star", now)["star_shown_count"] == 1
    now = usage_days(7, datetime.fromtimestamp(now) + timedelta(days=1))
    assert notices.claim("local_star", now)["star_shown_count"] == 2
    notices.disable_star()
    now = usage_days(8, datetime.fromtimestamp(now) + timedelta(days=1))
    assert notices.snapshot(now)["star_disabled"] is True
    assert notices.claim("local_star", now)["shown"] is False


def test_modes_and_disabled_cooldown(monkeypatch):
    monkeypatch.setattr(notices.config, "LOCAL_MODEL_STAR_REMINDER", "always")
    monkeypatch.setattr(notices.config, "TIMED_NOTIFICATION_COOLDOWN_SECONDS", 0)
    notices.disable_star()
    now = 1000000
    assert notices.claim("local_star", now)["shown"] is True
    assert notices.claim("invite", now)["shown"] is True
    assert notices.claim("update", now)["shown"] is True
    assert notices.snapshot(now - 100)["retry_after_seconds"] == 0
    monkeypatch.setattr(notices.config, "LOCAL_MODEL_STAR_REMINDER", "never")
    assert notices.claim("local_star", now)["shown"] is False


def test_concurrent_claims_only_admit_one_and_preserve_other_settings():
    local_store.merge({"unrelated": "keep"})
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: notices.claim("invite", 1000000), range(8)))
    assert sum(result["shown"] for result in results) == 1
    assert local_store.load()["unrelated"] == "keep"


def test_separate_app_processes_share_one_cooldown_slot():
    env = {**os.environ, "LOCAL_SETTINGS_FILE": local_store._path(),
           "TIMED_NOTIFICATION_COOLDOWN_SECONDS": "14400"}
    script = "import timed_notifications as n; print(n.claim('invite', 1000000)['shown'])"

    def launch(_):
        result = subprocess.run([sys.executable, "-c", script], env=env,
                                capture_output=True, text=True, timeout=30, check=True)
        return result.stdout.strip().splitlines()[-1] == "True"

    with ThreadPoolExecutor(max_workers=3) as executor:
        assert sum(executor.map(launch, range(3))) == 1


def test_session_usage_respects_pause_and_throttles_writes(monkeypatch):
    import realtime_session_base as sessions
    session = object.__new__(sessions.RealtimeSessionBase)
    session.is_paused = True
    record = MagicMock()
    monkeypatch.setattr(sessions.timed_notifications, "record_usage", record)
    monkeypatch.setattr(sessions.config, "RELAY_MODE", True)
    session._record_star_usage(local_model=True)
    record.assert_not_called()
    session.is_paused = False
    session._record_star_usage(local_model=True)
    record.assert_called_once_with(relay_mode=True, local_model=True)
    session._record_star_usage(local_model=True)
    assert record.call_count == 1


@pytest.mark.asyncio
async def test_startup_snapshot_includes_notices_sent_before_browser_connected(monkeypatch):
    import web_server
    monkeypatch.setattr(web_server.WebServer, "_is_loopback_request", lambda *_: True)
    monkeypatch.setattr(web_server.config, "take_relay_notices", lambda: [{"id": "soniox_quota"}])
    server = web_server.WebServer(MagicMock(), MagicMock())
    request = MagicMock(method="GET")
    response = await server.timed_notifications_handler(request)
    data = json.loads(response.text)
    assert data["notices"][0]["notice"]["id"] == "soniox_quota"
    token = data["notices"][0]["notice_id"]
    request.method = "POST"
    request.json = AsyncMock(return_value={"kind": "server", "notice_id": token})
    response = await server.timed_notifications_handler(request)
    assert json.loads(response.text)["shown"] is True


@pytest.mark.asyncio
async def test_broadcast_retains_notice_without_a_browser_client():
    import web_server
    server = web_server.WebServer(MagicMock(), MagicMock())
    await server.broadcast_to_clients({"type": "server_notice", "notice": {"id": "soniox_quota"}})
    assert notices.snapshot()["notices"][0]["notice"] == {"id": "soniox_quota"}
