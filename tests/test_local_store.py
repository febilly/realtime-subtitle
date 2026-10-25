import importlib
import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, call

import pytest

import local_store


@pytest.fixture
def web_server_module():
    previous_config = sys.modules.pop("config", None)
    previous_web_server = sys.modules.pop("web_server", None)
    try:
        importlib.import_module("config")
        ws_mod = importlib.import_module("web_server")
        yield ws_mod
    finally:
        sys.modules.pop("web_server", None)
        sys.modules.pop("config", None)
        if previous_web_server is not None:
            sys.modules["web_server"] = previous_web_server
        if previous_config is not None:
            sys.modules["config"] = previous_config


def test_normal_load_missing_file_returns_empty(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)
    assert local_store.load() == {}


def test_normal_save_merge_and_load(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)

    merged = local_store.merge({"soniox_key": "abc", "count": 42})
    assert merged == {"soniox_key": "abc", "count": "42"}

    loaded = local_store.load()
    assert loaded == {"soniox_key": "abc", "count": "42"}

    # Update one key and remove another
    updated = local_store.merge({"gemini_key": "xyz"}, removals=["count"])
    assert updated == {"soniox_key": "abc", "gemini_key": "xyz"}
    assert local_store.load() == {"soniox_key": "abc", "gemini_key": "xyz"}


def test_normal_clear(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)

    local_store.merge({"k1": "v1", "k2": "v2"})
    assert local_store.load() == {"k1": "v1", "k2": "v2"}

    cleared = local_store.clear()
    assert cleared == {}
    assert local_store.load() == {}


def test_non_dict_json_treated_as_empty(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)

    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert local_store.load() == {}


def test_corrupt_json_creates_backup_and_returns_empty(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)
    corrupt_content = '{"broken": json syntax'
    path.write_text(corrupt_content, encoding="utf-8")

    result = local_store.load()
    assert result == {}

    # Check that a backup file was created with the corrupt content
    backup_files = list(tmp_path.glob("settings.json.corrupt-*"))
    assert len(backup_files) == 1
    assert backup_files[0].read_text(encoding="utf-8") == corrupt_content
    # The original corrupt file was moved/replaced
    assert not path.exists()

    # Subsequent merge creates a new valid file
    new_data = local_store.merge({"token": "xyz"})
    assert new_data == {"token": "xyz"}
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == {"token": "xyz"}


def test_corrupt_backup_failure_still_returns_empty(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)
    path.write_text("corrupted", encoding="utf-8")

    # If os.replace fails during backup attempt, load() should still return {} safely
    monkeypatch.setattr(local_store.os, "replace", MagicMock(side_effect=OSError("disk error")))
    assert local_store.load() == {}


def test_load_retries_on_permission_error_and_succeeds(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)
    path.write_text(json.dumps({"key": "val"}), encoding="utf-8")

    real_open = open
    attempts = 0

    def mock_open(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("Transient lock by scanner")
        return real_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open)
    sleep_mock = MagicMock()
    monkeypatch.setattr(local_store.time, "sleep", sleep_mock)

    data = local_store.load(max_retries=3, base_delay=0.05)
    assert data == {"key": "val"}
    assert attempts == 3
    assert sleep_mock.call_count == 2
    sleep_mock.assert_has_calls([call(0.05), call(0.10)])


def test_load_raises_permission_error_after_exhausting_retries(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)
    path.write_text(json.dumps({"key": "val"}), encoding="utf-8")

    attempts = 0

    def mock_open(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise PermissionError("File locked by indexing service")

    monkeypatch.setattr("builtins.open", mock_open)
    sleep_mock = MagicMock()
    monkeypatch.setattr(local_store.time, "sleep", sleep_mock)

    with pytest.raises(PermissionError, match="File locked by indexing service"):
        local_store.load(max_retries=3, base_delay=0.05)

    assert attempts == 3
    assert sleep_mock.call_count == 2


def test_merge_aborts_without_overwriting_when_load_fails(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)
    initial_content = json.dumps({"soniox_api_key": "secret123", "account_token": "token_abc"})
    path.write_text(initial_content, encoding="utf-8")

    # Simulate load() raising PermissionError (e.g. file lock)
    monkeypatch.setattr(local_store, "load", MagicMock(side_effect=PermissionError("File locked")))

    with pytest.raises(PermissionError, match="File locked"):
        local_store.merge({"account_token": "lost"})

    # CRITICAL: Verify the original file was NOT overwritten or emptied!
    assert path.read_text(encoding="utf-8") == initial_content
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["soniox_api_key"] == "secret123"
    assert loaded["account_token"] == "token_abc"
    # Verify no orphan temporary files were created
    assert not list(tmp_path.glob(".local_settings_*.tmp"))


def test_atomic_write_retries_on_permission_error_and_succeeds(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)

    real_replace = os.replace
    attempts = 0

    def mock_replace(src, dst):
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise PermissionError("WinError 32: sharing violation")
        return real_replace(src, dst)

    monkeypatch.setattr(local_store.os, "replace", mock_replace)
    sleep_mock = MagicMock()
    monkeypatch.setattr(local_store.time, "sleep", sleep_mock)

    local_store._atomic_write({"saved": "ok"})
    assert attempts == 2
    assert sleep_mock.call_count == 1
    sleep_mock.assert_called_once_with(0.05)
    assert json.loads(path.read_text(encoding="utf-8")) == {"saved": "ok"}


def test_atomic_write_raises_and_cleans_tmp_after_retries(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(local_store.config, "LOCAL_SETTINGS_FILE", str(path), raising=False)

    attempts = 0

    def mock_replace(src, dst):
        nonlocal attempts
        attempts += 1
        raise PermissionError("WinError 5: Access denied")

    monkeypatch.setattr(local_store.os, "replace", mock_replace)
    sleep_mock = MagicMock()
    monkeypatch.setattr(local_store.time, "sleep", sleep_mock)

    with pytest.raises(PermissionError, match="Access denied"):
        local_store._atomic_write({"saved": "ok"})

    assert attempts == 3
    assert sleep_mock.call_count == 2
    # Ensure temporary file is cleaned up
    assert not list(tmp_path.glob(".local_settings_*.tmp"))


@pytest.mark.asyncio
async def test_web_server_local_store_get_returns_500_on_read_failure(
    monkeypatch, web_server_module
):
    ws = web_server_module.WebServer(MagicMock(), MagicMock())

    monkeypatch.setattr(ws, "_is_loopback_request", lambda req: True)
    monkeypatch.setattr(local_store, "load", MagicMock(side_effect=PermissionError("Locked file")))

    request = AsyncMock()
    response = await ws.local_store_get_handler(request)
    assert response.status == 500
    body = json.loads(response.body)
    assert body["status"] == "error"
    assert "Locked file" in body["message"]


@pytest.mark.asyncio
async def test_web_server_local_store_post_returns_500_on_merge_failure(
    monkeypatch, web_server_module
):
    ws = web_server_module.WebServer(MagicMock(), MagicMock())

    monkeypatch.setattr(ws, "_is_loopback_request", lambda req: True)
    monkeypatch.setattr(local_store, "merge", MagicMock(side_effect=PermissionError("Merge lock error")))

    request = AsyncMock()
    request.json.return_value = {"set": {"key": "val"}}
    response = await ws.local_store_post_handler(request)
    assert response.status == 500
    body = json.loads(response.body)
    assert body["status"] == "error"
    assert "Merge lock error" in body["message"]


@pytest.mark.asyncio
async def test_web_server_local_store_post_returns_500_on_clear_failure(
    monkeypatch, web_server_module
):
    ws = web_server_module.WebServer(MagicMock(), MagicMock())

    monkeypatch.setattr(ws, "_is_loopback_request", lambda req: True)
    monkeypatch.setattr(local_store, "clear", MagicMock(side_effect=PermissionError("Clear lock error")))

    request = AsyncMock()
    request.json.return_value = {"clear": True}
    response = await ws.local_store_post_handler(request)
    assert response.status == 500
    body = json.loads(response.body)
    assert body["status"] == "error"
    assert "Clear lock error" in body["message"]
