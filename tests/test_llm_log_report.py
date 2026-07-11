import shutil
import sys
from pathlib import Path

import pytest

from tools import llm_log_report


FIXTURE = Path(__file__).parent / "fixtures" / "llm_runtime_replay.jsonl"


@pytest.mark.parametrize(
    ("values", "pct", "expected"),
    [([], 0.5, None), ([30, 10, 20], 0.0, 10), ([30, 10, 20], 0.5, 20), ([30, 10, 20], 0.95, 30)],
)
def test_percentile(values, pct, expected):
    assert llm_log_report._percentile(values, pct) == expected


def test_load_rows_replays_real_style_jsonl_and_ignores_malformed_line():
    rows = llm_log_report.load_rows([FIXTURE])
    assert len(rows) == 17
    assert rows[0]["event"] == "pairing_source_close"
    assert rows[-1]["event"] == "llm_http"


def test_report_replays_runtime_flow_and_flags_lost_dispatch(tmp_path, monkeypatch, capsys):
    replay = tmp_path / "runtime.jsonl"
    shutil.copyfile(FIXTURE, replay)
    monkeypatch.setattr(sys, "argv", ["llm_log_report.py", str(replay)])

    llm_log_report.main()

    output = capsys.readouterr().out
    assert "17 events from 1 file(s)" in output
    assert "responses: 2  empty: 0  late(after timeout): 1" in output
    assert "latency p50=100ms p95=300ms max=300ms" in output
    assert "requests: 2  ok: 1  empty: 0" in output
    assert "no_change: 1 (50%)" in output
    assert "applied: 1 (50%)" in output
    assert "quiet: 1 (50%)" in output
    assert "max_wait: 1 (50%)" in output
    assert "dispatched: 3  broadcast/retracted: 2  LOST (coroutine died): 1" in output
    assert "LOST s3: synthetic lost source" in output

    review = replay.with_suffix(".applied.md")
    assert review.exists()
    content = review.read_text(encoding="utf-8")
    assert "[mistranslation]" in content
    assert "synthetic fixed two" in content
