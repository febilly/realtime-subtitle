import io
import threading
from email.message import Message
from urllib.error import HTTPError

import pytest

from local_inference import model_manager


class FakeResponse:
    def __init__(self, data: bytes, *, status=200, content_type="application/octet-stream", chunks=None):
        self._stream = io.BytesIO(data)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(data))
        self._chunks = chunks

    def read(self, size):
        if self._chunks is not None:
            return self._chunks.pop(0) if self._chunks else b""
        return self._stream.read(size)

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(model_manager, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(model_manager, "_legacy_models_dir", lambda: tmp_path / "legacy")
    monkeypatch.setattr(model_manager, "_ensure_llama_runtime", lambda progress=None: None)
    monkeypatch.delenv("REALTIME_SUBTITLE_HF_ENDPOINT", raising=False)
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    return tmp_path


def test_hymt2_falls_back_to_mirror_when_huggingface_is_unreachable(models_dir, monkeypatch):
    requested = []

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        requested.append(url)
        if url.startswith("https://huggingface.co/"):
            raise OSError("connection reset")
        return FakeResponse(b"GGUF" + b"\0" * 100)

    monkeypatch.setattr(model_manager, "urlopen", fake_urlopen)
    progress = []

    model_manager.download_hymt2(lambda stage, done, total: progress.append((stage, done, total)))

    dest = models_dir / "hymt2" / model_manager.HYMT2_MODEL_FILE
    assert dest.read_bytes().startswith(b"GGUF")
    assert model_manager.get_hymt2_model_path() == dest
    assert requested[-1] == (
        "https://hf-mirror.com/febilly/Hy-MT2-1.8B-StreamRevise-v4-GGUF/resolve/main/"
        "Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf"
    )
    assert progress[-1] == ("hymt2", 104, 104)


def test_download_rejects_a_gateway_page_and_keeps_no_model(models_dir, monkeypatch):
    monkeypatch.setattr(
        model_manager, "urlopen",
        lambda request, timeout=None: FakeResponse(b"<html>login</html>", content_type="text/html"),
    )

    with pytest.raises(RuntimeError, match="HTML page"):
        model_manager.download_hymt2()
    assert model_manager.get_hymt2_model_path() is None


def test_download_resumes_a_partial_file_with_a_range_request(models_dir, monkeypatch):
    dest = models_dir / "hymt2" / model_manager.HYMT2_MODEL_FILE
    dest.parent.mkdir(parents=True)
    dest.with_name(dest.name + ".part").write_bytes(b"GGUF1234")
    headers_seen = []

    def fake_urlopen(request, timeout=None):
        headers_seen.append(request.headers.get("Range"))
        return FakeResponse(b"5678", status=206)

    monkeypatch.setattr(model_manager, "urlopen", fake_urlopen)

    model_manager._stream_download("https://example.invalid/x.gguf", dest, "hymt2")

    assert headers_seen == ["bytes=8-"]
    assert dest.read_bytes() == b"GGUF12345678"


def test_complete_partial_file_is_accepted_on_416(models_dir, monkeypatch):
    dest = models_dir / "hymt2" / model_manager.HYMT2_MODEL_FILE
    dest.parent.mkdir(parents=True)
    dest.with_name(dest.name + ".part").write_bytes(b"GGUF5678")
    headers = Message()
    headers["Content-Range"] = "bytes */8"

    def fake_urlopen(request, timeout=None):
        raise HTTPError(request.full_url, 416, "Range Not Satisfiable", headers, None)

    monkeypatch.setattr(model_manager, "urlopen", fake_urlopen)

    model_manager._stream_download("https://example.invalid/x.gguf", dest, "hymt2")

    assert dest.read_bytes() == b"GGUF5678"


def test_cancel_stops_the_download_and_keeps_the_partial_for_resume(models_dir, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class SlowResponse(FakeResponse):
        def read(self, size):
            started.set()
            release.wait(5)
            return b"GGUF"

    monkeypatch.setattr(
        model_manager, "urlopen", lambda request, timeout=None: SlowResponse(b"GGUF" * 4),
    )
    monkeypatch.setattr(model_manager, "_ordered_hf_endpoints", lambda *a: ["https://huggingface.co"])
    errors = []

    def run():
        try:
            model_manager.run_download("hymt2")
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(5)
    assert model_manager.get_download_progress()["active"] is True
    with pytest.raises(model_manager.DownloadBusy):
        model_manager.run_download("qwen3-asr")
    assert model_manager.cancel_download() is True
    release.set()
    worker.join(5)

    assert len(errors) == 1 and isinstance(errors[0], model_manager.DownloadCancelled)
    state = model_manager.get_download_progress()
    assert state["active"] is False and state["cancelled"] is True
    part = models_dir / "hymt2" / (model_manager.HYMT2_MODEL_FILE + ".part")
    assert part.read_bytes() == b"GGUF"
    assert model_manager.get_hymt2_model_path() is None
    assert model_manager.cancel_download() is False


def test_existing_hymt2_skips_the_network_and_ignores_the_imatrix_file(models_dir, monkeypatch):
    model_dir = models_dir / "hymt2"
    model_dir.mkdir()
    (model_dir / "Hy-MT2.imatrix.gguf").write_bytes(b"GGUF")
    assert model_manager.get_hymt2_model_path() is None
    (model_dir / "custom.gguf").write_bytes(b"GGUF")
    monkeypatch.setattr(model_manager, "urlopen", lambda *a, **k: pytest.fail("network used"))

    model_manager.download_hymt2()

    assert model_manager.get_hymt2_model_path() == model_dir / "custom.gguf"


def test_status_reports_download_and_vram_estimates(models_dir):
    status = model_manager.get_all_local_models_status()
    assert status["translation"]["hymt2"]["estimated_bytes"] == 1_100_000_000
    assert status["translation"]["hymt2"]["estimated_vram_bytes"] == 1_500_000_000
    assert status["asr"]["qwen3-asr"]["estimated_vram_bytes"] == 2_000_000_000
