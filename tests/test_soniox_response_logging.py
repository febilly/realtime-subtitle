import importlib
import json

import soniox_response_log


def fresh_module():
    soniox_response_log.close()
    return importlib.reload(soniox_response_log)


def test_soniox_response_log_is_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SONIOX_RESPONSE_LOG", raising=False)
    recorder = fresh_module()

    recorder.log_response({"tokens": [{"text": "not recorded"}]})

    assert not (tmp_path / "logs").exists()


def test_soniox_response_log_ignores_local_env_during_pytest(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "diagnostic-test")
    monkeypatch.setenv("SONIOX_RESPONSE_LOG", "1")
    recorder = fresh_module()

    recorder.log_response({"tokens": [{"text": "synthetic"}]})

    assert not (tmp_path / "logs").exists()


def test_soniox_response_log_records_raw_batch_and_runtime_context(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("SONIOX_RESPONSE_LOG", "1")
    recorder = fresh_module()
    response = {
        "tokens": [
            {
                "text": "First. Second.",
                "is_final": True,
                "speaker": "1",
                "translation_status": "original",
            }
        ],
        "endpoint_detected": False,
    }

    recorder.log_response(
        response,
        segment_mode="punctuation",
        translation_mode="accurate",
        sent_count=4,
        stream_key="abc",
    )
    recorder.close()

    files = list((tmp_path / "logs" / "soniox-responses").glob("responses_*.jsonl"))
    assert len(files) == 1
    row = json.loads(files[0].read_text(encoding="utf-8"))
    assert row["event"] == "soniox_response"
    assert row["segment_mode"] == "punctuation"
    assert row["translation_mode"] == "accurate"
    assert row["sent_count"] == 4
    assert row["stream_key"] == "abc"
    assert row["response"] == response


def test_soniox_response_log_failure_never_escapes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("SONIOX_RESPONSE_LOG", "1")
    recorder = fresh_module()

    def fail_open(*args, **kwargs):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("builtins.open", fail_open)
    recorder.log_response({"tokens": []}, segment_mode="punctuation")
    recorder.log_response({"tokens": []}, segment_mode="punctuation")

    assert recorder._enabled is False
