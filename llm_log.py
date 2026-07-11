"""Structured JSONL logging for LLM calls and refine decisions.

One file per app run: ``logs/llm_<timestamp>.jsonl``, one JSON object per
line, always with ``ts`` (ISO, milliseconds) and ``event``. Layers:

  transport   relay_send / relay_response / relay_error / relay_timeout /
              relay_send_failed / relay_llm_disabled  (hosted 托管 mode);
              llm_http (own-key OpenAI-compatible call)
  decision    refine_attempt / refine_result / translate_result
              (source, draft, raw model output, gate decision)
  session     pairing_source_close / pairing_close / refine_dispatch /
              refine_broadcast (sentence_id correlation — a dispatch with no
              matching broadcast means the refine coroutine died before
              delivering its result)

Disabled by default; set ENABLE_LLM_LOG=1 when collecting diagnostics
(pairing timing, refine decisions). Never raises: logging must not break
the session.
"""
import json
import os
import threading
from datetime import datetime

_lock = threading.Lock()
_file = None
_enabled: bool | None = None

# Long free-text fields are truncated to keep lines readable; raw model
# output matters most and 4000 chars covers every sane refine reply.
_TRUNCATE = 4000


def _env_enabled() -> bool:
    # Never write log files from the test suite.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    value = os.environ.get("ENABLE_LLM_LOG")
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _clip(value):
    if isinstance(value, str) and len(value) > _TRUNCATE:
        return value[:_TRUNCATE] + f"...[+{len(value) - _TRUNCATE} chars]"
    return value


def log_event(event: str, **fields) -> None:
    global _file, _enabled
    try:
        if _enabled is None:
            _enabled = _env_enabled()
        if not _enabled:
            return
        record = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
        }
        for key, value in fields.items():
            record[key] = _clip(value)
        line = json.dumps(record, ensure_ascii=False, default=str)
        with _lock:
            if _file is None:
                logs_dir = os.path.join(os.getcwd(), "logs")
                os.makedirs(logs_dir, exist_ok=True)
                path = os.path.join(
                    logs_dir, f"llm_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
                )
                _file = open(path, "a", encoding="utf-8")
                print(f"📝 LLM log: {path}")
            _file.write(line + "\n")
            _file.flush()
    except Exception:
        pass
