"""Offline LLM translation experiment tools.

Build a dataset from transcript logs, run OpenAI-compatible LLMs with the
project's real prompt templates, and judge outputs automatically or manually.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import random
import re
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


LINE_RE = re.compile(r"^\[(?P<time>\d\d:\d\d:\d\d)\]\s+(?P<tags>(?:\[[^\]]+\])*)\s*(?P<text>.*)$")
TAG_RE = re.compile(r"\[([^\]]+)\]")


@dataclass
class TranscriptLine:
    timestamp: str
    speaker: str
    language: str
    is_translation: bool
    text: str
    path: Path
    line_no: int


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _normalize_lang(value: str | None) -> str:
    return (value or "").strip().lower()


def _clean_join(parts: list[str]) -> str:
    text = "".join(parts)
    text = re.sub(r"\s+([,.;:!?，。！？、])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def parse_transcript_line(path: Path, line_no: int, line: str) -> TranscriptLine | None:
    match = LINE_RE.match(line.rstrip("\n"))
    if not match:
        return None

    tags = TAG_RE.findall(match.group("tags") or "")
    speaker = ""
    language = ""
    is_translation = False
    for tag in tags:
        value = tag.strip()
        upper = value.upper()
        if upper.startswith("SPEAKER "):
            speaker = value.split(None, 1)[1].strip()
        elif upper == "TRANS":
            is_translation = True
        else:
            language = value.lower()

    return TranscriptLine(
        timestamp=match.group("time"),
        speaker=speaker or "unknown",
        language=language,
        is_translation=is_translation,
        text=(match.group("text") or "").strip(),
        path=path,
        line_no=line_no,
    )


def parse_transcript_file(path: Path) -> list[dict[str, Any]]:
    """Extract source/draft-translation pairs from one transcript log."""

    pending: dict[str, list[TranscriptLine]] = {}
    pairs: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, raw in enumerate(f, start=1):
            item = parse_transcript_line(path, line_no, raw)
            if item is None:
                continue

            if item.is_translation:
                source_items = pending.pop(item.speaker, [])
                source_text = _clean_join([p.text for p in source_items if p.text])
                translation = item.text.strip()
                if not source_text or not translation:
                    continue
                source_lang = next((p.language for p in reversed(source_items) if p.language), "")
                pairs.append(
                    {
                        "source": source_text,
                        "draft_translation": translation,
                        "source_lang": source_lang,
                        "target_lang": item.language,
                        "speaker": item.speaker,
                        "timestamp": item.timestamp,
                        "log_path": str(path),
                        "log_line": item.line_no,
                    }
                )
                continue

            if not item.text:
                continue
            pending.setdefault(item.speaker, []).append(item)

    return pairs


def _split_lang_filter(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def build_dataset(args: argparse.Namespace) -> None:
    logs_dir = Path(args.logs_dir)
    files = sorted(logs_dir.glob(args.pattern))
    source_filter = _split_lang_filter(args.source_lang)
    target_filter = _split_lang_filter(args.target_lang)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    accepted_history: list[dict[str, str]] = []

    for file_path in files:
        for pair in parse_transcript_file(file_path):
            source = pair["source"].strip()
            draft = pair["draft_translation"].strip()
            if len(source) < args.min_source_chars or len(draft) < args.min_translation_chars:
                continue
            if source_filter and _normalize_lang(pair.get("source_lang")) not in source_filter:
                continue
            if target_filter and _normalize_lang(pair.get("target_lang")) not in target_filter:
                continue

            dedupe_key = (source, draft, _normalize_lang(pair.get("target_lang")))
            if args.dedupe and dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            context = accepted_history[-max(0, args.context):]
            row = {
                "id": f"sample_{len(rows) + 1:06d}",
                "source": source,
                "draft_translation": draft,
                "source_lang": _normalize_lang(pair.get("source_lang")),
                "target_lang": _normalize_lang(pair.get("target_lang")),
                "speaker": pair.get("speaker") or "",
                "timestamp": pair.get("timestamp") or "",
                "log_path": pair.get("log_path") or "",
                "log_line": pair.get("log_line"),
                "context": list(context),
            }
            rows.append(row)
            accepted_history.append({"source": source, "translation": draft})

    if args.sample == "random":
        rng = random.Random(args.seed)
        rng.shuffle(rows)

    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    _write_jsonl(Path(args.output), rows)
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "logs_dir": str(logs_dir),
        "pattern": args.pattern,
        "files_scanned": len(files),
        "samples": len(rows),
        "context": args.context,
        "source_lang": sorted(source_filter),
        "target_lang": sorted(target_filter),
    }
    _write_json(Path(args.output).with_suffix(".manifest.json"), manifest)
    print(f"Wrote {len(rows)} samples to {args.output}")


def _chat_url(base_url: str) -> str:
    value = (base_url or "").strip().rstrip("/")
    if not value:
        raise ValueError("base_url is required")
    if value.endswith("/chat/completions"):
        return value
    return value + "/chat/completions"


def _resolve_api_key(model: dict[str, Any]) -> str:
    if model.get("api_key"):
        return str(model["api_key"])
    env_name = model.get("api_key_env")
    if env_name:
        return os.environ.get(str(env_name), "").strip()
    return ""


def _public_model_config(model: dict[str, Any]) -> dict[str, Any]:
    public = dict(model)
    public.pop("api_key", None)
    if public.get("api_key_env"):
        public["api_key_env"] = str(public["api_key_env"])
    return public


def _usage_cost(usage: dict[str, Any], pricing: dict[str, Any] | None) -> float | None:
    if not pricing:
        return None
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    cached_tokens = 0
    if isinstance(details, dict):
        cached_tokens = int(details.get("cached_tokens") or 0)
    cached_tokens = cached_tokens or int(usage.get("prompt_cache_hit_tokens") or 0)
    uncached_tokens = max(0, prompt_tokens - cached_tokens)
    input_per_1m = float(pricing.get("input_per_1m") or 0.0)
    cached_input_per_1m = float(pricing.get("cached_input_per_1m") or input_per_1m)
    output_per_1m = float(pricing.get("output_per_1m") or 0.0)
    return (
        uncached_tokens * input_per_1m
        + cached_tokens * cached_input_per_1m
        + completion_tokens * output_per_1m
    ) / 1_000_000.0


def _cost_currency(model: dict[str, Any]) -> str:
    pricing = model.get("pricing") if isinstance(model.get("pricing"), dict) else {}
    return str(model.get("currency") or pricing.get("currency") or "CNY").strip().upper()


async def post_chat(
    session: aiohttp.ClientSession,
    model: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    api_key = _resolve_api_key(model)
    if not api_key:
        raise RuntimeError(f"missing API key for model {model.get('name') or model.get('model')}")

    payload: dict[str, Any] = {
        "model": model["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if isinstance(model.get("extra_json"), dict):
        payload.update(model["extra_json"])

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if isinstance(model.get("extra_headers"), dict):
        headers.update(model["extra_headers"])

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    started = time.perf_counter()
    async with session.post(_chat_url(model["base_url"]), json=payload, headers=headers, timeout=timeout) as resp:
        text = await resp.text()
        latency_ms = int((time.perf_counter() - started) * 1000)
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status}: {text[:2000]}")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"non-JSON response: {text[:2000]}") from exc

    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    usage = data.get("usage") or {}
    return {
        "content": content,
        "usage": usage,
        "latency_ms": latency_ms,
        "response_model": data.get("model"),
        "estimated_cost": _usage_cost(usage, model.get("pricing")),
        "cost_currency": _cost_currency(model),
    }


def _build_messages(sample: dict[str, Any], mode: str) -> list[dict[str, str]]:
    import llm_refine

    if mode == "refine":
        return llm_refine.build_refine_messages(
            sample.get("source") or "",
            sample.get("draft_translation") or "",
            sample.get("context") or [],
            target_lang=sample.get("target_lang") or "",
        )
    if mode == "translate":
        return llm_refine.build_translate_messages(
            sample.get("source") or "",
            sample.get("context") or [],
            target_lang=sample.get("target_lang") or "",
        )
    raise ValueError(f"unsupported mode: {mode}")


def _extract_answer(text: str) -> str:
    import llm_client

    return llm_client.extract_answer_tag(text or "").strip().strip("`").strip()


def _parse_severity(text: str) -> str:
    matches = re.findall(r"<severity>(.*?)</severity>", text or "", flags=re.IGNORECASE | re.DOTALL)
    value = str(matches[-1]).strip().lower() if matches else ""
    return value if value in {"low", "medium", "high", "critical"} else ""


def _final_translation(sample: dict[str, Any], mode: str, raw_content: str) -> dict[str, Any]:
    import llm_refine

    answer = _extract_answer(raw_content)
    severity = _parse_severity(raw_content)
    draft = (sample.get("draft_translation") or "").strip()
    if mode == "refine":
        no_change = not answer or answer == llm_refine.NO_CHANGE_MARKER or severity not in {"high", "critical"}
        return {
            "answer": answer,
            "severity": severity,
            "no_change": no_change,
            "output_translation": draft if no_change else answer,
        }
    return {
        "answer": answer,
        "severity": severity,
        "no_change": False,
        "output_translation": answer,
    }


async def run_one(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    sample: dict[str, Any],
    model: dict[str, Any],
    mode: str,
    repeat_index: int,
) -> dict[str, Any]:
    model_name = str(model.get("name") or model.get("model"))
    messages = _build_messages(sample, mode)
    temperature = float(model.get("temperature", 0.2))
    max_tokens = int(model.get("max_tokens", 1024))
    timeout_seconds = float(model.get("timeout_seconds", 60))

    async with semaphore:
        try:
            response = await post_chat(
                session,
                model,
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
            parsed = _final_translation(sample, mode, response["content"])
            status = "ok"
            error = ""
        except Exception as exc:
            response = {
                "content": "",
                "usage": {},
                "latency_ms": None,
                "response_model": None,
                "estimated_cost": None,
                "cost_currency": _cost_currency(model),
            }
            parsed = {
                "answer": "",
                "severity": "",
                "no_change": True,
                "output_translation": "",
            }
            status = "error"
            error = str(exc)

    return {
        "sample_id": sample["id"],
        "model_name": model_name,
        "model": model.get("model"),
        "mode": mode,
        "repeat_index": repeat_index,
        "status": status,
        "error": error,
        "latency_ms": response["latency_ms"],
        "usage": response["usage"],
        "estimated_cost": response["estimated_cost"],
        "cost_currency": response["cost_currency"],
        "response_model": response["response_model"],
        "raw_output": response["content"],
        "answer": parsed["answer"],
        "severity": parsed["severity"],
        "no_change": parsed["no_change"],
        "output_translation": parsed["output_translation"],
    }


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * pct
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (pos - lower)


def summarize_run(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_model: dict[str, list[dict[str, Any]]] = {}
    for row in results:
        by_model.setdefault(row["model_name"], []).append(row)

    summary: list[dict[str, Any]] = []
    for model_name, rows in sorted(by_model.items()):
        ok_rows = [r for r in rows if r.get("status") == "ok"]
        latencies = [float(r["latency_ms"]) for r in ok_rows if r.get("latency_ms") is not None]
        prompt_tokens = sum(int((r.get("usage") or {}).get("prompt_tokens") or 0) for r in ok_rows)
        completion_tokens = sum(int((r.get("usage") or {}).get("completion_tokens") or 0) for r in ok_rows)
        total_cost = sum(float((r.get("estimated_cost") if r.get("estimated_cost") is not None else r.get("estimated_cost_usd")) or 0.0) for r in ok_rows)
        currencies = sorted({str(r.get("cost_currency") or "").strip().upper() for r in ok_rows if r.get("cost_currency")})
        summary.append(
            {
                "model_name": model_name,
                "requests": len(rows),
                "ok": len(ok_rows),
                "errors": len(rows) - len(ok_rows),
                "success_rate": round(len(ok_rows) / len(rows), 4) if rows else 0,
                "latency_avg_ms": round(statistics.mean(latencies), 1) if latencies else None,
                "latency_p50_ms": round(_percentile(latencies, 0.50), 1) if latencies else None,
                "latency_p95_ms": round(_percentile(latencies, 0.95), 1) if latencies else None,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "estimated_cost": round(total_cost, 6),
                "cost_currency": currencies[0] if len(currencies) == 1 else ("mixed" if currencies else ""),
            }
        )
    return summary


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row.keys()})
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_run_markdown(path: Path, summary: list[dict[str, Any]]) -> None:
    lines = [
        "# LLM run summary",
        "",
        "| Model | OK/Requests | Success | Avg ms | P50 ms | P95 ms | Tokens in/out | Est. cost |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary:
        lines.append(
            "| {model_name} | {ok}/{requests} | {success_rate:.1%} | {latency_avg_ms} | "
            "{latency_p50_ms} | {latency_p95_ms} | {prompt_tokens}/{completion_tokens} | "
            "{estimated_cost} {cost_currency} |".format(**row)
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_judge_markdown(path: Path, summary: list[dict[str, Any]]) -> None:
    lines = [
        "# LLM judge summary",
        "",
        "| Candidate | Ratings | Best | Accuracy | Fluency | Completeness | Overall |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in summary:
        lines.append(
            "| {candidate_id} | {ratings} | {best_count} | {accuracy_avg} | "
            "{fluency_avg} | {completeness_avg} | {overall_avg} |".format(**row)
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class ProgressBar:
    def __init__(self, total: int, *, enabled: bool = True) -> None:
        self.total = max(0, int(total))
        self.enabled = bool(enabled and self.total > 0)
        self.done = 0
        self.ok = 0
        self.errors = 0
        self.started = time.perf_counter()
        self._last_render = 0.0

    def update(self, row: dict[str, Any], *, force: bool = False) -> None:
        self.done += 1
        if row.get("status") == "ok":
            self.ok += 1
        else:
            self.errors += 1
        if not self.enabled:
            return

        now = time.perf_counter()
        if not force and self.done < self.total and now - self._last_render < 0.1:
            return
        self._last_render = now
        self._render(now)

    def _render(self, now: float | None = None) -> None:
        if now is None:
            now = time.perf_counter()
        width = 28
        ratio = min(1.0, self.done / self.total) if self.total else 1.0
        filled = int(round(width * ratio))
        bar = "#" * filled + "-" * (width - filled)
        elapsed = max(0.001, now - self.started)
        rate = self.done / elapsed
        sys.stderr.write(
            f"\r[{bar}] {self.done}/{self.total} "
            f"{ratio * 100:5.1f}% ok={self.ok} err={self.errors} "
            f"{rate:.2f}/s"
        )
        sys.stderr.flush()

    def finish(self) -> None:
        if not self.enabled:
            return
        if self.done < self.total or self._last_render == 0.0:
            self._render()
        sys.stderr.write("\n")
        sys.stderr.flush()


async def run_experiment_async(args: argparse.Namespace) -> None:
    dataset = _iter_jsonl(Path(args.dataset))
    if args.limit and args.limit > 0:
        dataset = dataset[: args.limit]
    config_data = _read_json(Path(args.models))
    models = config_data.get("models") or []
    if not models:
        raise SystemExit("models config has no models")

    out_dir = Path(args.output_dir or ROOT / "scratch" / "llm_eval" / f"run-{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "run_config.json", {
        "dataset": str(Path(args.dataset)),
        "mode": args.mode,
        "repeat": args.repeat,
        "concurrency": args.concurrency,
        "models": [_public_model_config(m) for m in models],
    })

    connector = aiohttp.TCPConnector(limit=max(1, args.concurrency * 2), ttl_dns_cache=300)
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    repeat_count = max(1, int(args.repeat))
    total_requests = len(dataset) * len(models) * repeat_count
    progress = ProgressBar(total_requests, enabled=not bool(args.no_progress))
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for sample in dataset:
            for model in models:
                for repeat_index in range(1, repeat_count + 1):
                    tasks.append(asyncio.create_task(run_one(session, semaphore, sample, model, args.mode, repeat_index)))
        results = []
        for task in asyncio.as_completed(tasks):
            row = await task
            results.append(row)
            progress.update(row)
    progress.finish()

    _write_jsonl(out_dir / "results.jsonl", results)
    summary = summarize_run(results)
    _write_json(out_dir / "summary.json", summary)
    _write_csv(out_dir / "summary.csv", summary)
    _write_run_markdown(out_dir / "summary.md", summary)
    print(f"Wrote run results to {out_dir}")


def run_experiment(args: argparse.Namespace) -> None:
    asyncio.run(run_experiment_async(args))


def _candidate_rows(sample: dict[str, Any], results: list[dict[str, Any]], include_baseline: bool) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    if include_baseline:
        candidates.append(
            {
                "candidate_id": "baseline_draft",
                "model_name": "baseline_draft",
                "translation": sample.get("draft_translation") or "",
            }
        )
    for row in results:
        if row.get("sample_id") != sample.get("id"):
            continue
        if row.get("status") != "ok":
            continue
        translation = (row.get("output_translation") or "").strip()
        if not translation:
            continue
        candidates.append(
            {
                "candidate_id": f"{row['model_name']}__r{row.get('repeat_index', 1)}",
                "model_name": str(row["model_name"]),
                "translation": translation,
            }
        )
    return candidates


def build_judge_prompt(sample: dict[str, Any], candidates: list[dict[str, str]]) -> str:
    payload = {
        "sample_id": sample["id"],
        "source_language": sample.get("source_lang") or "unknown",
        "target_language": sample.get("target_lang") or "unknown",
        "source": sample.get("source") or "",
        "context": sample.get("context") or [],
        "candidates": candidates,
    }
    return (
        "You are evaluating real-time subtitle translations.\n"
        "Judge every candidate against the SOURCE, using CONTEXT only to resolve references.\n"
        "Do not prefer a candidate merely because it is longer or more polished.\n\n"
        "Scoring:\n"
        "- accuracy: 1-5, fidelity to source meaning, names, numbers, actor, tense, question intent.\n"
        "- fluency: 1-5, naturalness and readability in the target language.\n"
        "- completeness: 1-5, no important omissions or unsupported additions.\n"
        "- overall: 0-100, best practical subtitle quality.\n\n"
        "Return ONLY valid JSON in this schema:\n"
        "{\n"
        '  "sample_id": "sample id",\n'
        '  "ratings": [\n'
        '    {"candidate_id": "id", "accuracy": 1, "fluency": 1, "completeness": 1, "overall": 0, "major_errors": ["short"], "comment": "short"}\n'
        "  ],\n"
        '  "best_candidate_id": "id"\n'
        "}\n\n"
        "DATA:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def build_manual_batch_judge_prompt(data_filename: str = "judge_manual_data.json") -> str:
    return (
        "# Manual LLM Judge Prompt\n\n"
        f"I uploaded `{data_filename}`. It contains a JSON array of translation-evaluation tasks.\n\n"
        "For every task in the uploaded file, judge every candidate against the task's `sample.source`. "
        "Use `sample.context` only to resolve references; do not quote it, merge it into the current source, "
        "or reward a candidate merely because it is longer or more polished.\n\n"
        "Scoring:\n"
        "- `accuracy`: 1-5, fidelity to source meaning, names, numbers, actor, tense, and question intent.\n"
        "- `fluency`: 1-5, naturalness and readability in the target language.\n"
        "- `completeness`: 1-5, no important omissions or unsupported additions.\n"
        "- `overall`: 0-100, best practical subtitle quality.\n\n"
        "Return ONLY valid JSON. Do not use Markdown fences. Use this exact schema:\n\n"
        "{\n"
        '  "results": [\n'
        "    {\n"
        '      "task_id": "task id from the uploaded file",\n'
        '      "sample_id": "sample id",\n'
        '      "ratings": [\n'
        '        {"candidate_id": "id", "accuracy": 1, "fluency": 1, "completeness": 1, "overall": 0, "major_errors": ["short"], "comment": "short"}\n'
        "      ],\n"
        '      "best_candidate_id": "id"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Important:\n"
        "- Preserve every `candidate_id` exactly as provided.\n"
        "- Include one result for every task in the uploaded file.\n"
        "- Provide the final JSON as a downloadable file named `judge_results.json`.\n"
        "- If the uploaded file is too large for one response, evaluate as many tasks as possible in order and tell me the next `task_id` to continue from.\n"
    )


def _json_from_text(text: str) -> Any:
    value = (text or "").strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```$", "", value)
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            return json.loads(value[start : end + 1])
        raise


async def judge_async(args: argparse.Namespace) -> None:
    dataset = _iter_jsonl(Path(args.dataset))
    results = _iter_jsonl(Path(args.results))
    model = _read_json(Path(args.judge_model))
    if "models" in model:
        model = model["models"][0]
    out_dir = Path(args.output_dir or Path(args.results).parent / "judge")
    out_dir.mkdir(parents=True, exist_ok=True)

    result_rows: list[dict[str, Any]] = []
    connector = aiohttp.TCPConnector(limit=max(1, args.concurrency * 2), ttl_dns_cache=300)
    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    async with aiohttp.ClientSession(connector=connector) as session:
        async def one(sample: dict[str, Any]) -> dict[str, Any] | None:
            candidates = _candidate_rows(sample, results, args.include_baseline)
            if len(candidates) < 2:
                return None
            messages = [
                {"role": "system", "content": "You are a strict translation evaluation judge. Return valid JSON only."},
                {"role": "user", "content": build_judge_prompt(sample, candidates)},
            ]
            async with semaphore:
                try:
                    response = await post_chat(
                        session,
                        model,
                        messages,
                        temperature=float(model.get("temperature", 0.0)),
                        max_tokens=int(model.get("max_tokens", 2048)),
                        timeout_seconds=float(model.get("timeout_seconds", 90)),
                    )
                    parsed = _json_from_text(response["content"])
                    return {
                        "sample_id": sample["id"],
                        "status": "ok",
                        "error": "",
                        "latency_ms": response["latency_ms"],
                        "usage": response["usage"],
                        "estimated_cost": response["estimated_cost"],
                        "cost_currency": response["cost_currency"],
                        "candidates": candidates,
                        "judge_raw_output": response["content"],
                        "judge": parsed,
                    }
                except Exception as exc:
                    return {
                        "sample_id": sample["id"],
                        "status": "error",
                        "error": str(exc),
                        "candidates": candidates,
                    }

        tasks = [one(sample) for sample in dataset[: args.limit if args.limit else None]]
        rows = await asyncio.gather(*tasks)
        result_rows = [row for row in rows if row is not None]

    _write_jsonl(out_dir / "judge_results.jsonl", result_rows)
    summary = summarize_judgements(result_rows)
    _write_json(out_dir / "judge_summary.json", summary)
    _write_csv(out_dir / "judge_summary.csv", summary)
    print(f"Wrote judge results to {out_dir}")


def summarize_judgements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_candidate: dict[str, list[dict[str, Any]]] = {}
    best_counts: dict[str, int] = {}
    for row in rows:
        if row.get("status") != "ok":
            continue
        judge = row.get("judge") or {}
        best = judge.get("best_candidate_id")
        if best:
            best_counts[str(best)] = best_counts.get(str(best), 0) + 1
        for rating in judge.get("ratings") or []:
            cid = str(rating.get("candidate_id") or "")
            if not cid:
                continue
            by_candidate.setdefault(cid, []).append(rating)

    summary: list[dict[str, Any]] = []
    for cid, ratings in sorted(by_candidate.items()):
        def avg(field: str) -> float | None:
            values = [float(r[field]) for r in ratings if isinstance(r.get(field), (int, float))]
            return round(statistics.mean(values), 3) if values else None

        summary.append(
            {
                "candidate_id": cid,
                "ratings": len(ratings),
                "best_count": best_counts.get(cid, 0),
                "accuracy_avg": avg("accuracy"),
                "fluency_avg": avg("fluency"),
                "completeness_avg": avg("completeness"),
                "overall_avg": avg("overall"),
            }
        )
    return summary


def judge(args: argparse.Namespace) -> None:
    asyncio.run(judge_async(args))


def _load_manual_judge_results(path: Path) -> list[dict[str, Any]]:
    raw = _read_json(path)
    if isinstance(raw, dict):
        items = raw.get("results")
    else:
        items = raw
    if not isinstance(items, list):
        raise SystemExit("manual judge file must be a JSON array or an object with a 'results' array")

    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ratings = item.get("ratings")
        if not isinstance(ratings, list):
            continue
        sample_id = str(item.get("sample_id") or item.get("task_id") or "")
        rows.append(
            {
                "sample_id": sample_id,
                "status": "ok",
                "error": "",
                "judge": {
                    "sample_id": sample_id,
                    "task_id": item.get("task_id") or sample_id,
                    "ratings": ratings,
                    "best_candidate_id": item.get("best_candidate_id") or "",
                },
            }
        )
    return rows


def import_judge(args: argparse.Namespace) -> None:
    out_dir = Path(args.output_dir or Path(args.results).parent / "manual_judge_import")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _load_manual_judge_results(Path(args.input))
    _write_jsonl(out_dir / "judge_results.jsonl", rows)
    summary = summarize_judgements(rows)
    _write_json(out_dir / "judge_summary.json", summary)
    _write_csv(out_dir / "judge_summary.csv", summary)
    _write_judge_markdown(out_dir / "judge_summary.md", summary)
    print(f"Imported {len(rows)} judged samples to {out_dir}")


def export_judge(args: argparse.Namespace) -> None:
    dataset = _iter_jsonl(Path(args.dataset))
    results = _iter_jsonl(Path(args.results))
    out_dir = Path(args.output_dir or Path(args.results).parent / "manual_judge")
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks: list[dict[str, Any]] = []
    for sample in dataset[: args.limit if args.limit else None]:
        candidates = _candidate_rows(sample, results, args.include_baseline)
        if len(candidates) < 2:
            continue
        tasks.append(
            {
                "task_id": sample["id"],
                "sample": {
                    "id": sample["id"],
                    "source_lang": sample.get("source_lang"),
                    "target_lang": sample.get("target_lang"),
                    "source": sample.get("source"),
                    "context": sample.get("context") or [],
                },
                "candidates": candidates,
            }
        )

    _write_json(out_dir / "judge_manual_data.json", tasks)
    _write_jsonl(out_dir / "judge_manual_tasks.jsonl", tasks)
    prompt_md = build_manual_batch_judge_prompt("judge_manual_data.json")
    (out_dir / "judge_prompt.md").write_text(prompt_md, encoding="utf-8")
    print(f"Wrote {len(tasks)} manual judge tasks to {out_dir}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="LLM translation evaluation tools")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build-dataset", help="build JSONL samples from transcript logs")
    p.add_argument("--logs-dir", default=str(ROOT / "logs"))
    p.add_argument("--pattern", default="transcript_*.txt")
    p.add_argument("--output", default=str(ROOT / "scratch" / "llm_eval" / "dataset.jsonl"))
    p.add_argument("--context", type=int, default=5)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--sample", choices=("first", "random"), default="first")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--source-lang", default="")
    p.add_argument("--target-lang", default="")
    p.add_argument("--min-source-chars", type=int, default=3)
    p.add_argument("--min-translation-chars", type=int, default=1)
    p.add_argument("--dedupe", action=argparse.BooleanOptionalAction, default=True)
    p.set_defaults(func=build_dataset)

    p = sub.add_parser("run", help="run models against a dataset")
    p.add_argument("--dataset", default=str(ROOT / "scratch" / "llm_eval" / "dataset.jsonl"))
    p.add_argument("--models", required=True)
    p.add_argument("--output-dir", default="")
    p.add_argument("--mode", choices=("translate", "refine"), default="translate")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--no-progress", action="store_true", help="disable the live progress bar")
    p.set_defaults(func=run_experiment)

    p = sub.add_parser("judge", help="judge run outputs with an OpenAI-compatible LLM")
    p.add_argument("--dataset", default=str(ROOT / "scratch" / "llm_eval" / "dataset.jsonl"))
    p.add_argument("--results", required=True)
    p.add_argument("--judge-model", required=True)
    p.add_argument("--output-dir", default="")
    p.add_argument("--include-baseline", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=judge)

    p = sub.add_parser("import-judge", help="import manual web-AI judge JSON and summarize it")
    p.add_argument("--input", required=True, help="manual judge JSON downloaded from a web AI")
    p.add_argument("--results", required=True, help="run results.jsonl; used to choose the default output directory")
    p.add_argument("--output-dir", default="")
    p.set_defaults(func=import_judge)

    p = sub.add_parser("export-judge", help="export prompts/data for manual web-AI judging")
    p.add_argument("--dataset", default=str(ROOT / "scratch" / "llm_eval" / "dataset.jsonl"))
    p.add_argument("--results", required=True)
    p.add_argument("--output-dir", default="")
    p.add_argument("--include-baseline", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=export_judge)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
