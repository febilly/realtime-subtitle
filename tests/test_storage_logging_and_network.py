import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import llm_log
import local_store
import logger
import network_debug
from relay_errors import RELAY_CLOSE_CODES, relay_close_info


@pytest.fixture(autouse=True)
def reset_llm_log_state(monkeypatch):
    old_file = llm_log._file
    if old_file is not None:
        old_file.close()
    monkeypatch.setattr(llm_log, "_file", None)
    monkeypatch.setattr(llm_log, "_enabled", None)
    yield
    if llm_log._file is not None:
        llm_log._file.close()
        llm_log._file = None


def test_local_store_missing_corrupt_and_non_object_files(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)
    assert local_store.load() == {}

    path.write_text("not json", encoding="utf-8")
    assert local_store.load() == {}

    path.write_text("[1, 2]", encoding="utf-8")
    assert local_store.load() == {}


def test_local_store_merge_stringifies_values_and_removes_keys(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)

    first = local_store.merge({"count": 3, "none": None, 7: True})
    second = local_store.merge({"count": 4}, removals=["none", "missing"])

    assert first == {"count": "3", "none": "", "7": "True"}
    assert second == {"count": "4", "7": "True"}
    assert json.loads(path.read_text(encoding="utf-8")) == second
    assert not list(path.parent.glob(".local_settings_*.tmp"))


def test_local_store_clear_and_non_mapping_updates(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)
    local_store.merge({"keep": "yes"})

    assert local_store.merge([("ignored", "value")]) == {"keep": "yes"}
    assert local_store.clear() == {}
    assert local_store.load() == {}


def test_atomic_write_cleans_temporary_file_after_replace_failure(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)
    monkeypatch.setattr(local_store.os, "replace", MagicMock(side_effect=OSError("denied")))

    with pytest.raises(OSError, match="denied"):
        local_store._atomic_write({"x": "y"})

    assert not list(tmp_path.glob(".local_settings_*.tmp"))


@pytest.mark.parametrize("value", ["1", "true", " YES ", "y", "On"])
def test_transcript_logger_env_truthy_values(monkeypatch, value):
    monkeypatch.setenv("ENABLE_TRANSCRIPT_LOG", value)
    assert logger._env_log_enabled() is True


def test_transcript_logger_disabled_creates_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    transcript_logger = logger.TranscriptLogger(enabled=False)
    assert transcript_logger.init_log_file() is None
    transcript_logger.write_to_log([{"text": "ignored"}])
    transcript_logger.close_log_file()
    assert not (tmp_path / "logs").exists()


def test_transcript_logger_groups_tokens_and_marks_translations(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    transcript_logger = logger.TranscriptLogger(enabled=True)
    path = Path(transcript_logger.init_log_file())

    transcript_logger.write_to_log(
        [
            {"speaker": "1", "language": "en", "text": "Hello "},
            {"is_separator": True, "text": "|"},
            {
                "speaker": "1",
                "language": "en",
                "text": "world",
                "translation_status": "translation",
            },
            {"speaker": "2", "language": "zh", "text": "你好"},
            {"speaker": None, "language": None, "text": "?"},
        ]
    )
    transcript_logger.close_log_file()

    content = path.read_text(encoding="utf-8")
    assert "[SPEAKER 1][EN][TRANS] Hello world" in content
    assert "[SPEAKER 2][ZH] 你好" in content
    assert "]  ?" in content
    assert "Ended at:" in content
    assert transcript_logger.log_file is None


def test_llm_log_is_disabled_during_pytest(monkeypatch):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "active")
    monkeypatch.setenv("ENABLE_LLM_LOG", "1")
    assert llm_log._env_enabled() is False


def test_llm_log_writes_jsonl_and_clips_long_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("ENABLE_LLM_LOG", "yes")

    llm_log.log_event("refine_result", raw="x" * (llm_log._TRUNCATE + 7), value={1, 2})

    files = list((tmp_path / "logs").glob("llm_*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["event"] == "refine_result"
    assert record["raw"].endswith("...[+7 chars]")
    assert isinstance(record["value"], str)
    assert "T" in record["ts"]


def test_llm_log_never_raises_when_directory_creation_fails(monkeypatch):
    monkeypatch.setattr(llm_log, "_enabled", True)
    monkeypatch.setattr(llm_log.os, "makedirs", MagicMock(side_effect=OSError("read only")))
    llm_log.log_event("ignored", value="safe")
    assert llm_log._file is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://user:pass@example.test:8443/path?api_key=abc&lang=zh&access_token=x#frag",
            "https://***:***@example.test:8443/path?api_key=***&lang=zh&access_token=***#frag",
        ),
        ("wss://example.test/ws?state=&page=2", "wss://example.test/ws?state=***&page=2"),
        ("relative/path", "relative/path"),
    ],
)
def test_sanitize_url_redacts_credentials(url, expected):
    assert network_debug.sanitize_url(url) == expected


@pytest.mark.parametrize(
    ("key", "sensitive"),
    [("X-Api-Key", True), ("monkey", True), ("language", False), ("page", False)],
)
def test_sensitive_key_detection(key, sensitive):
    assert network_debug._is_sensitive_key(key) is sensitive


def test_network_debug_enable_is_idempotent(monkeypatch, capsys):
    patch_clients = MagicMock()
    monkeypatch.setattr(network_debug, "_patch_clients", patch_clients)
    monkeypatch.setattr(network_debug, "_enabled", False)

    network_debug.enable()

    assert network_debug.is_enabled() is True
    patch_clients.assert_called_once_with()
    assert "Outbound network debug logging enabled" in capsys.readouterr().out


def test_network_log_helpers_redact_url_and_report_errors(monkeypatch, capsys):
    monkeypatch.setattr(network_debug, "_enabled", True)
    monkeypatch.setattr(network_debug.time, "perf_counter", MagicMock(side_effect=[10.0, 10.025]))
    started = network_debug._log_outgoing_start(
        "HTTP", "GET", "https://example.test?token=secret"
    )
    network_debug._log_outgoing_error(
        "HTTP", "GET", "https://example.test?token=secret", TimeoutError("late"), started
    )

    output = capsys.readouterr().out
    assert "secret" not in output
    assert "token=***" in output
    assert "TimeoutError: late 25.0ms" in output


def test_patch_clients_runs_once(monkeypatch):
    patches = [MagicMock(), MagicMock(), MagicMock()]
    monkeypatch.setattr(network_debug, "_patch_requests", patches[0])
    monkeypatch.setattr(network_debug, "_patch_aiohttp", patches[1])
    monkeypatch.setattr(network_debug, "_patch_websockets_sync", patches[2])
    monkeypatch.setattr(network_debug, "_patched", False)

    network_debug._patch_clients()
    network_debug._patch_clients()

    assert network_debug._patched is True
    for patch in patches:
        patch.assert_called_once_with()


def test_requests_patch_logs_success_and_error(monkeypatch):
    fake_requests = types.ModuleType("requests")

    class Session:
        def request(self, method, url, *args, **kwargs):
            if "fail" in url:
                raise OSError("offline")
            return types.SimpleNamespace(status_code=204)

    fake_requests.sessions = types.SimpleNamespace(Session=Session)
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setattr(network_debug, "_original_requests_request", None)
    start = MagicMock(return_value=10.0)
    finish = MagicMock()
    error = MagicMock()
    monkeypatch.setattr(network_debug, "_log_outgoing_start", start)
    monkeypatch.setattr(network_debug, "_log_outgoing_finish", finish)
    monkeypatch.setattr(network_debug, "_log_outgoing_error", error)

    network_debug._patch_requests()
    session = Session()
    response = session.request("get", "https://ok", timeout=3)
    assert response.status_code == 204
    start.assert_called_with("HTTP", "GET", "https://ok")
    finish.assert_called_once_with("HTTP", "GET", "https://ok", 204, 10.0)

    with pytest.raises(OSError, match="offline"):
        session.request("post", "https://fail")
    error.assert_called_once()


@pytest.mark.asyncio
async def test_aiohttp_patch_logs_success_and_error(monkeypatch):
    fake_aiohttp = types.ModuleType("aiohttp")

    class ClientSession:
        async def _request(self, method, url, *args, **kwargs):
            if "fail" in str(url):
                raise TimeoutError("late")
            return types.SimpleNamespace(status=201)

    fake_aiohttp.ClientSession = ClientSession
    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)
    monkeypatch.setattr(network_debug, "_original_aiohttp_request", None)
    monkeypatch.setattr(network_debug, "_log_outgoing_start", MagicMock(return_value=4.0))
    finish = MagicMock()
    error = MagicMock()
    monkeypatch.setattr(network_debug, "_log_outgoing_finish", finish)
    monkeypatch.setattr(network_debug, "_log_outgoing_error", error)

    network_debug._patch_aiohttp()
    session = ClientSession()
    response = await session._request("put", "https://ok")
    assert response.status == 201
    finish.assert_called_once_with("HTTP", "PUT", "https://ok", 201, 4.0)

    with pytest.raises(TimeoutError, match="late"):
        await session._request("get", "https://fail")
    error.assert_called_once()


def test_websocket_patch_logs_success_and_error(monkeypatch):
    websockets = types.ModuleType("websockets")
    sync = types.ModuleType("websockets.sync")
    client = types.ModuleType("websockets.sync.client")

    def connect(uri, *args, **kwargs):
        if "fail" in uri:
            raise ConnectionError("refused")
        return "connection"

    client.connect = connect
    sync.client = client
    websockets.sync = sync
    monkeypatch.setitem(sys.modules, "websockets", websockets)
    monkeypatch.setitem(sys.modules, "websockets.sync", sync)
    monkeypatch.setitem(sys.modules, "websockets.sync.client", client)
    monkeypatch.setattr(network_debug, "_original_ws_sync_connect", None)
    monkeypatch.setattr(network_debug, "_log_outgoing_start", MagicMock(return_value=2.0))
    finish = MagicMock()
    error = MagicMock()
    monkeypatch.setattr(network_debug, "_log_outgoing_finish", finish)
    monkeypatch.setattr(network_debug, "_log_outgoing_error", error)

    network_debug._patch_websockets_sync()
    assert client.connect("wss://ok") == "connection"
    finish.assert_called_once_with("WS  ", "CONNECT", "wss://ok", "connected", 2.0)

    with pytest.raises(ConnectionError, match="refused"):
        client.connect("wss://fail")
    error.assert_called_once()


@pytest.mark.parametrize("code", [None, "bad", 1000, 4999])
def test_relay_close_info_ignores_unknown_codes(code):
    assert relay_close_info(code) is None


@pytest.mark.parametrize("code", sorted(RELAY_CLOSE_CODES))
def test_relay_close_info_returns_all_known_mappings(code):
    tag, terminal = RELAY_CLOSE_CODES[code]
    result = relay_close_info(str(code))
    assert result[0:2] == (tag, terminal)
    assert result[2]
