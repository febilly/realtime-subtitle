import hashlib
import io
import shutil
import threading
import zipfile
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

import pytest

import config
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


def test_qwen3_asr_downloads_pinned_files_and_verifies_sha256(models_dir, monkeypatch):
    requested = []
    body = b"GGUF" + b"\0" * 8

    def fake_urlopen(request, timeout=None):
        requested.append(request.full_url)
        return FakeResponse(body)

    variant = dict(model_manager.QWEN3_ASR_VARIANTS["1.7B"])
    digest = hashlib.sha256(body).hexdigest()
    variant["sha256"] = {variant["decoder"]: digest, variant["mmproj"]: digest}
    monkeypatch.setitem(model_manager.QWEN3_ASR_VARIANTS, "1.7B", variant)
    monkeypatch.setattr(model_manager, "urlopen", fake_urlopen)
    monkeypatch.setattr(model_manager, "_ordered_hf_endpoints", lambda *a: ["https://hf-mirror.com"])
    monkeypatch.setattr(config, "LOCAL_QWEN_ASR_MODEL", "1.7B")

    model_manager.download_qwen3_asr()

    model_dir = models_dir / model_manager.QWEN3_ASR_DIR_NAME
    base = f"https://hf-mirror.com/{variant['repo']}/resolve/{variant['revision']}/"
    assert requested == [base + variant["mmproj"], base + variant["decoder"]]
    assert model_manager.get_qwen3_asr_files() == (model_dir / variant["decoder"], model_dir / variant["mmproj"])


def test_qwen3_asr_rejects_a_file_with_the_wrong_sha256(models_dir, monkeypatch):
    monkeypatch.setattr(model_manager, "urlopen", lambda request, timeout=None: FakeResponse(b"GGUF tampered"))
    monkeypatch.setattr(model_manager, "_ordered_hf_endpoints", lambda *a: ["https://huggingface.co"])
    monkeypatch.setattr(config, "LOCAL_QWEN_ASR_MODEL", "1.7B")

    with pytest.raises(RuntimeError, match="sha256 mismatch"):
        model_manager.download_qwen3_asr()
    assert not list((models_dir / model_manager.QWEN3_ASR_DIR_NAME).glob("*.gguf"))


def test_silero_and_runtime_come_from_the_pinned_hf_repo(models_dir, monkeypatch):
    requested = []
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in ("llama.dll", "mtmd.dll", "libomp.dll", "llama-server.exe"):
            bundle.writestr(name, b"x")

    def fake_urlopen(request, timeout=None):
        requested.append(request.full_url)
        return FakeResponse(archive.getvalue() if request.full_url.endswith(".zip") else b"onnx")

    monkeypatch.setattr(model_manager, "urlopen", fake_urlopen)
    monkeypatch.setattr(model_manager, "_ordered_hf_endpoints", lambda *a: ["https://hf-mirror.com"])
    monkeypatch.setattr(model_manager, "LLAMA_RUNTIME_ZIP_SHA256", hashlib.sha256(archive.getvalue()).hexdigest())
    monkeypatch.setattr(model_manager, "SILERO_VAD_ONNX_SHA256", hashlib.sha256(b"onnx").hexdigest())
    monkeypatch.setattr(model_manager, "is_silero_cached", lambda: False)
    monkeypatch.setattr(model_manager.sys, "platform", "win32")

    model_manager.download_silero()
    model_manager._download_llama_runtime(models_dir / "runtime")

    base = f"https://hf-mirror.com/{model_manager.LOCAL_RUNTIME_REPO}/resolve/{model_manager.LOCAL_RUNTIME_REVISION}/"
    assert requested == [base + model_manager.SILERO_VAD_ONNX_NAME, base + model_manager.LLAMA_RUNTIME_ZIP]
    assert sorted(path.name for path in (models_dir / "runtime").iterdir()) == ["libomp.dll", "llama.dll", "mtmd.dll"]
    assert not (models_dir / model_manager.LLAMA_RUNTIME_ZIP).exists()


def test_runtime_falls_back_to_github_when_hf_fails(models_dir, monkeypatch):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("llama.dll", b"x")
    requested = []

    def fake_urlopen(request, timeout=None):
        requested.append(request.full_url)
        if "github.com" not in request.full_url:
            raise OSError("blocked")
        return FakeResponse(archive.getvalue())

    monkeypatch.setattr(model_manager, "urlopen", fake_urlopen)
    monkeypatch.setattr(model_manager, "_ordered_hf_endpoints", lambda *a: ["https://hf-mirror.com"])
    monkeypatch.setattr(model_manager.sys, "platform", "win32")

    model_manager._download_llama_runtime(models_dir / "runtime")

    assert "github.com/ggml-org/llama.cpp" in requested[-1]
    assert (models_dir / "runtime" / "llama.dll").is_file()


def _make_legacy_files(root):
    model_dir = root / "qwen3-asr"
    runtime_dir = root / "qwen_llama_vulkan_bin"
    model_dir.mkdir(parents=True)
    runtime_dir.mkdir()
    for name in model_manager.LEGACY_QWEN3_ASR_FILES:
        (model_dir / name).write_bytes(b"x" * 10)
    (runtime_dir / "llama.dll").write_bytes(b"x" * 5)
    (runtime_dir / "ggml-vulkan.dll").write_bytes(b"x" * 5)


def test_legacy_cleanup_removes_old_files_and_keeps_everything_else(models_dir):
    _make_legacy_files(models_dir)
    (models_dir / "llama-cpp-vulkan-b8391.zip").write_bytes(b"x" * 3)
    kept = [
        models_dir / "qwen3-asr" / "notes.txt",
        models_dir / "hymt2" / model_manager.HYMT2_MODEL_FILE,
        models_dir / model_manager.QWEN3_ASR_DIR_NAME / "Qwen3-ASR-1.7B.Q6_K.gguf",
        models_dir / model_manager.QWEN_LLAMA_VULKAN_BIN_DIR_NAME / "llama.dll",
        models_dir / f"llama-cpp-vulkan-{model_manager.LLAMA_CPP_TAG}.zip.part",
    ]
    for path in kept:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")

    status = model_manager.get_all_local_models_status()["legacy"]
    assert status["count"] == 6 and status["bytes"] == 43

    assert model_manager.remove_legacy_files() == {"removed_bytes": 43, "failed": []}

    assert all(path.is_file() for path in kept)
    assert not (models_dir / "qwen_llama_vulkan_bin").exists()
    assert (models_dir / "qwen3-asr").is_dir()  # still holds notes.txt
    assert model_manager.get_legacy_files_status()["count"] == 0


def test_legacy_cleanup_never_touches_the_yakutan_models_root(models_dir):
    _make_legacy_files(models_dir / "legacy")

    assert model_manager.get_legacy_files_status()["count"] == 0
    assert model_manager.remove_legacy_files() == {"removed_bytes": 0, "failed": []}
    assert (models_dir / "legacy" / "qwen3-asr" / model_manager.LEGACY_QWEN3_ASR_FILES[0]).is_file()


def test_legacy_cleanup_reports_a_file_that_is_in_use(models_dir, monkeypatch):
    _make_legacy_files(models_dir)
    original = Path.unlink

    def fake_unlink(path, *args, **kwargs):
        if path.name == "llama.dll":
            raise PermissionError("in use")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fake_unlink)

    result = model_manager.remove_legacy_files()

    assert result["failed"] == ["llama.dll"] and result["removed_bytes"] == 35
    assert (models_dir / "qwen_llama_vulkan_bin" / "llama.dll").is_file()


def test_status_reports_download_and_vram_estimates(models_dir):
    status = model_manager.get_all_local_models_status()
    assert status["translation"]["hymt2"]["estimated_bytes"] == 1_100_000_000
    assert status["translation"]["hymt2"]["estimated_vram_bytes"] == 1_300_000_000
    assert status["asr"]["qwen3-asr"]["estimated_vram_bytes"] == 2_100_000_000


def test_status_reports_free_space_on_the_download_drive(models_dir, monkeypatch):
    # MODELS_DIR does not exist before the first download: measure its drive.
    monkeypatch.setattr(model_manager, "MODELS_DIR", models_dir / "not" / "yet")
    seen = []

    def fake_usage(path):
        seen.append(Path(path))
        return shutil._ntuple_diskusage(100, 40, 60)

    monkeypatch.setattr(model_manager.shutil, "disk_usage", fake_usage)
    assert model_manager.get_all_local_models_status()["disk_free_bytes"] == 60
    assert seen == [models_dir.resolve()]
