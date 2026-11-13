import json
import time

import pytest

from server_route import ServerRouter, normalize_mirrors, parse_bundled_mirrors

OFFICIAL = "https://official.example"
R4 = "https://relay-4.example"
R2 = "https://relay-2.example"
BUNDLED = f"{R4}|100,{R2}|200"


def make_probe(delays, mirrors=None):
    """delays: base -> seconds before answering, or None to fail."""
    calls = []

    def probe(base, timeout):
        calls.append(base)
        delay = delays.get(base)
        if delay is None:
            raise ConnectionError("down")
        if delay > timeout:
            time.sleep(timeout)
            raise TimeoutError("timeout")
        time.sleep(delay)
        return {"ok": True, "mirrors": mirrors if mirrors is not None else [{"url": R4, "priority": 100}, {"url": R2, "priority": 200}]}

    probe.calls = calls
    return probe


def router(delays, tmp_path=None, grace=0.3, timeout=1.5, bundled=BUNDLED, mirrors=None):
    return ServerRouter(
        OFFICIAL,
        bundled_mirrors=bundled,
        cache_path=str(tmp_path / "mirrors.json") if tmp_path else "",
        grace_seconds=grace,
        probe_timeout_seconds=timeout,
        rerace_min_interval_seconds=0.0,
        probe=make_probe(delays, mirrors),
    )


def chosen(r):
    assert r.wait_ready(5)
    return r.active_base


def test_parse_bundled_mirrors():
    assert parse_bundled_mirrors(f"{R4}|100, {R2}/ ; http://insecure.example|1") == [
        {"url": R4, "priority": 100},
        {"url": R2, "priority": 200},
    ]
    assert normalize_mirrors("nope") is None
    assert normalize_mirrors([{"url": R4, "priority": "7"}, {"url": "ftp://x"}]) == [{"url": R4, "priority": 7}]


def test_official_first_wins_immediately():
    r = router({OFFICIAL: 0.05, R4: 0.5, R2: 0.5})
    start = time.monotonic()
    assert chosen(r) == OFFICIAL
    assert time.monotonic() - start < 0.4  # no grace wait when the best route answered


def test_official_within_grace_beats_faster_relay():
    r = router({OFFICIAL: 0.25, R4: 0.05, R2: 0.05}, grace=0.5)
    assert chosen(r) == OFFICIAL


def test_relay_used_when_official_misses_grace():
    r = router({OFFICIAL: 1.4, R4: 0.05, R2: 0.05}, grace=0.3)
    start = time.monotonic()
    assert chosen(r) == R4  # both relays answered; lower priority number wins
    assert time.monotonic() - start < 0.9  # stopped waiting after the grace period


def test_higher_priority_relay_within_grace_wins():
    r = router({OFFICIAL: None, R4: 0.2, R2: 0.05}, grace=0.4)
    assert chosen(r) == R4


def test_lower_priority_relay_when_others_are_down():
    r = router({OFFICIAL: None, R4: None, R2: 0.05})
    assert chosen(r) == R2


def test_everything_down_keeps_official():
    r = router({OFFICIAL: None, R4: None, R2: None})
    assert chosen(r) == OFFICIAL


def test_server_list_replaces_bundled_list(tmp_path):
    r = router({OFFICIAL: 0.01}, tmp_path, mirrors=[{"url": "https://relay-9.example", "priority": 5}])
    chosen(r)
    saved = json.loads((tmp_path / "mirrors.json").read_text(encoding="utf-8"))
    assert saved["mirrors"] == [{"url": "https://relay-9.example", "priority": 5}]

    # Next run: the bundled relays are no longer probed, only the saved list.
    nxt = router({OFFICIAL: None, "https://relay-9.example": 0.01, R4: 0.01}, tmp_path)
    assert chosen(nxt) == "https://relay-9.example"
    assert R4 not in nxt._probe.calls


def test_cache_for_another_server_is_ignored(tmp_path):
    (tmp_path / "mirrors.json").write_text(json.dumps({"official": "https://other", "mirrors": []}), encoding="utf-8")
    r = router({OFFICIAL: None, R4: 0.01}, tmp_path)
    assert chosen(r) == R4


def test_network_failure_races_again():
    delays = {OFFICIAL: 0.01, R4: 0.01}
    r = router(delays)
    assert chosen(r) == OFFICIAL
    delays[OFFICIAL] = None
    assert r.report_network_failure() is True
    deadline = time.monotonic() + 3
    while r.active_base != R4 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert r.active_base == R4


def test_rerace_is_rate_limited():
    r = ServerRouter(OFFICIAL, BUNDLED, "", 0.1, 1.0, 30.0, probe=make_probe({OFFICIAL: 0.01}))
    chosen(r)
    assert r.report_network_failure() is False


def test_no_mirrors_is_ready_immediately():
    r = ServerRouter(OFFICIAL, "", "", 0.1, 1.0, 0.0, probe=make_probe({OFFICIAL: 0.5}))
    start = time.monotonic()
    assert r.wait_ready(5)
    assert time.monotonic() - start < 0.2
    assert r.active_base == OFFICIAL
