"""Summarize an llm_*.jsonl log written by llm_log.py.

Usage:
    python tools/llm_log_report.py [logs/llm_YYYYMMDD_HHMMSS.jsonl ...]

With no arguments, reads every logs/llm_*.jsonl. Reports:
  - transport health: relay sends/responses/timeouts/errors, late responses,
    empty contents, latency percentiles
  - refine decisions: applied / no_change / no_answer / error rates, applied
    breakdown by error category
  - dispatch/broadcast correlation: sentences whose refine coroutine died
    (dispatched but never broadcast) — the "stuck gray line" signature
  - the full list of applied changes (source/draft/refined) for review
"""
import glob
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _percentile(values, pct):
    if not values:
        return None
    values = sorted(values)
    idx = min(len(values) - 1, int(round((len(values) - 1) * pct)))
    return values[idx]


def load_rows(paths):
    rows = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    return rows


def main():
    paths = sys.argv[1:] or sorted(glob.glob(str(ROOT / "logs" / "llm_*.jsonl")))
    if not paths:
        print("no llm_*.jsonl logs found")
        return
    rows = load_rows(paths)
    print(f"{len(rows)} events from {len(paths)} file(s)\n")

    by_event = Counter(r.get("event") for r in rows)
    print("== events ==")
    for event, count in by_event.most_common():
        print(f"  {event}: {count}")

    # Transport
    responses = [r for r in rows if r.get("event") == "relay_response"]
    lat = [r["latency_ms"] for r in responses if isinstance(r.get("latency_ms"), (int, float)) and r["latency_ms"] >= 0]
    if responses:
        empty = sum(1 for r in responses if r.get("empty"))
        late = sum(1 for r in responses if r.get("late"))
        print("\n== relay transport ==")
        print(f"  responses: {len(responses)}  empty: {empty}  late(after timeout): {late}")
        if lat:
            print(f"  latency p50={_percentile(lat, 0.5)}ms p95={_percentile(lat, 0.95)}ms max={max(lat)}ms")
        timeouts = [r for r in rows if r.get("event") == "relay_timeout"]
        errors = [r for r in rows if r.get("event") == "relay_error"]
        print(f"  timeouts: {len(timeouts)}  errors: {len(errors)}")
        for reason, count in Counter(e.get("reason") for e in errors).most_common():
            print(f"    error reason {reason}: {count}")

    # HTTP (own key)
    https = [r for r in rows if r.get("event") == "llm_http"]
    if https:
        ok = [r for r in https if r.get("status") == 200]
        lat = [r["latency_ms"] for r in ok if isinstance(r.get("latency_ms"), (int, float))]
        print("\n== own-key http ==")
        print(f"  requests: {len(https)}  ok: {len(ok)}  empty: {sum(1 for r in ok if r.get('empty'))}")
        if lat:
            print(f"  latency p50={_percentile(lat, 0.5)}ms p95={_percentile(lat, 0.95)}ms")
        for r in https:
            if r.get("error"):
                print(f"    error: {str(r['error'])[:120]}")

    # Refine decisions
    decisions = [r for r in rows if r.get("event") == "refine_result"]
    if decisions:
        print("\n== refine decisions ==")
        by_decision = Counter(r.get("decision") for r in decisions)
        total = len(decisions)
        for decision, count in by_decision.most_common():
            print(f"  {decision}: {count} ({100 * count / total:.0f}%)")
        applied = [r for r in decisions if r.get("decision") == "applied"]
        for category, count in Counter(r.get("category") for r in applied).most_common():
            print(f"    applied {category}: {count}")

    # Dispatch/broadcast correlation
    dispatched = {r.get("sentence_id"): r for r in rows if r.get("event") == "refine_dispatch" and r.get("sentence_id")}
    broadcast_ids = {r.get("sentence_id") for r in rows if r.get("event") in ("refine_broadcast", "refine_dropped_retracted")}
    lost = [sid for sid in dispatched if sid not in broadcast_ids]
    print("\n== dispatch/broadcast ==")
    print(f"  dispatched: {len(dispatched)}  broadcast/retracted: {len(broadcast_ids)}  LOST (coroutine died): {len(lost)}")
    for sid in lost:
        r = dispatched[sid]
        print(f"    LOST {sid}: {str(r.get('source'))[:60]}")

    # Applied changes for review
    if decisions:
        applied = [r for r in decisions if r.get("decision") == "applied"]
        if applied:
            out = Path(paths[0]).with_suffix(".applied.md")
            with out.open("w", encoding="utf-8") as f:
                for r in applied:
                    f.write(f"### {r.get('ts')} [{r.get('category') or '-'}]\n")
                    f.write(f"- SRC  : {r.get('source')}\n")
                    f.write(f"- DRAFT: {r.get('draft')}\n")
                    f.write(f"- FIXED: {r.get('refined')}\n\n")
            print(f"\napplied-changes review file -> {out}")


if __name__ == "__main__":
    main()
