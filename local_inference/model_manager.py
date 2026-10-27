from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import LOCAL_INFERENCE_DISPLAY_NAMES, get_common_runtime_issues, get_engine_runtime_issues

logger = logging.getLogger(__name__)
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
VENDOR_DIR = PACKAGE_DIR / "vendor"

QWEN3_ASR_DIR_NAME = "qwen3-asr"
QWEN_LLAMA_VULKAN_BIN_DIR_NAME = "qwen_llama_vulkan_bin"
SILERO_VAD_DIR_NAME = "silero_vad"
SILERO_VAD_ONNX_NAME = "silero_vad_16k_op15.onnx"
HYMT2_DIR_NAME = "hymt2"
NEMOTRON_BOUNDARY_DIR_NAME = (
    "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11"
)
NEMOTRON_BOUNDARY_FILES = (
    "encoder.int8.onnx",
    "decoder.int8.onnx",
    "joiner.int8.onnx",
    "tokens.txt",
)
QWEN3_ASR_FILES = (
    "qwen3_asr_encoder_frontend.int4.onnx",
    "qwen3_asr_encoder_backend.int4.onnx",
    "qwen3_asr_llm.q4_k.gguf",
)
QWEN3_ASR_MODEL_URL = (
    "https://github.com/HaujetZhao/Qwen3-ASR-GGUF/releases/download/models/"
    "Qwen3-ASR-1.7B-gguf.zip"
)
LLAMA_CPP_DLL_URL_TEMPLATE = (
    "https://github.com/ggml-org/llama.cpp/releases/download/{tag}/"
    "llama-{tag}-bin-win-vulkan-x64.zip"
)
LLAMA_CPP_LATEST_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
SILERO_VAD_ONNX_URL = (
    "https://raw.githubusercontent.com/snakers4/silero-vad/master/"
    f"src/silero_vad/data/{SILERO_VAD_ONNX_NAME}"
)
_MODEL_SIZE_BYTES = {"silero-vad": 1_300_000, "qwen3-asr": 1_500_000_000, "hymt2": 1_100_000_000}
# Rough VRAM while loaded (weights + KV cache + runtime), shown before download.
_MODEL_VRAM_BYTES = {"qwen3-asr": 2_000_000_000, "hymt2": 1_500_000_000}

HYMT2_REPO_ID = "febilly/Hy-MT2-1.8B-StreamRevise-v4-GGUF"
# The repo also carries an .imatrix.gguf (quantization calibration data);
# inference needs only this weight file.
HYMT2_MODEL_FILE = "Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf"
HF_ENDPOINT_OFFICIAL = "https://huggingface.co"
# Tried in order when huggingface.co is unreachable; same resolve/ layout.
HF_ENDPOINT_MIRRORS = ("https://hf-mirror.com",)


def _default_models_dir() -> Path:
    configured = os.environ.get("REALTIME_SUBTITLE_LOCAL_MODELS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "local_models"
    return PROJECT_ROOT / "local_models"


MODELS_DIR = _default_models_dir()


def apply_cache_env() -> None:
    """Keep optional model caches beside the user-managed local model folder."""
    os.environ.setdefault("HF_HOME", str((MODELS_DIR / "huggingface").resolve()))


def _legacy_models_dir() -> Path:
    configured = os.environ.get("FUNASR_LOCAL_MODELS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    return PROJECT_ROOT.parent.parent / "funasr" / "local_asr_models"


def model_roots() -> list[Path]:
    roots: list[Path] = []
    for value in (MODELS_DIR, _legacy_models_dir()):
        resolved = value.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _find_dir(name: str, ready) -> Path | None:
    for root in model_roots():
        candidate = root / name
        if ready(candidate):
            return candidate
    return None


def _qwen_model_ready(path: Path) -> bool:
    return path.is_dir() and all((path / filename).is_file() for filename in QWEN3_ASR_FILES)


def _runtime_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("llama.dll", "ggml.dll", "ggml-base.dll", "libomp140.x86_64.dll")
    return ("libllama.so", "libggml.so", "libggml-base.so")


def _runtime_ready(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_file() for name in _runtime_names())


def qwen_llama_bin_dir() -> Path:
    configured = os.environ.get("REALTIME_SUBTITLE_LLAMA_BIN_DIR", "").strip()
    if configured:
        explicit = Path(configured).expanduser()
        if _runtime_ready(explicit):
            return explicit
    found = _find_dir(QWEN_LLAMA_VULKAN_BIN_DIR_NAME, _runtime_ready)
    if found is not None:
        return found
    bundled = VENDOR_DIR / "qwen_asr_gguf" / "inference" / "bin"
    if _runtime_ready(bundled):
        return bundled
    legacy_bundle = (
        PROJECT_ROOT.parent.parent
        / "funasr"
        / "local_inference"
        / "vendor"
        / "qwen_asr_gguf"
        / "inference"
        / "bin"
    )
    if _runtime_ready(legacy_bundle):
        return legacy_bundle
    return MODELS_DIR / QWEN_LLAMA_VULKAN_BIN_DIR_NAME


def _gpu_used_mb() -> int | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return int(result.stdout.strip().splitlines()[0])
    except Exception:
        return None


@contextmanager
def warn_if_weights_not_resident(label: str, weights_path):
    try:
        size_mb = Path(weights_path).stat().st_size / (1024 * 1024)
    except OSError:
        size_mb = 0.0
    before = _gpu_used_mb()
    try:
        yield
    finally:
        after = _gpu_used_mb()
        if before is None or after is None or size_mb <= 0:
            return
        delta = after - before
        if delta < size_mb * 0.5:
            logger.warning(
                "%s weights may be in shared GPU memory (expected +%.0f MiB, observed +%d MiB); "
                "free VRAM and reload the model to avoid PCIe-bound decoding",
                label,
                size_mb,
                delta,
            )
        else:
            logger.info("%s weights resident in VRAM (+%d MiB)", label, delta)


def prepare_qwen_llama_runtime_env() -> None:
    os.environ["YAKUTAN_QWEN_LLAMA_BIN"] = str(qwen_llama_bin_dir().resolve())
    os.environ["GGML_VK_ENABLE_MEMORY_PRIORITY"] = "1"
    os.environ.pop("GGML_VK_ALLOW_SYSMEM_FALLBACK", None)
    # ggml-vulkan puts weights in host-visible VRAM (the ReBAR window) when it
    # can. With ASR and Hy-MT2 in one process the second model then lands in
    # shared GPU memory, even with VRAM free and memory priority on: measured
    # 3080, second-loaded model 107ms -> 365ms (ASR) / 46ms -> 285ms (Hy-MT2).
    # Plain device-local memory keeps both resident.
    os.environ["GGML_VK_DISABLE_HOST_VISIBLE_VIDMEM"] = "1"


def ensure_vendor_sources(engine: str) -> Path | None:
    if engine != "qwen3-asr":
        raise ValueError(f"Unknown local ASR engine: {engine}")
    base = VENDOR_DIR / "qwen_asr_gguf"
    required = (
        "asr_engine.py",
        "inference/asr.py",
        "inference/schema.py",
        "inference/encoder.py",
        "inference/llama.py",
        "inference/utils.py",
    )
    if not all((base / name).is_file() for name in required):
        raise RuntimeError("Bundled Qwen3-ASR runtime sources are incomplete")
    return base


def get_local_model_path(engine: str) -> str | None:
    if engine != "qwen3-asr":
        return None
    found = _find_dir(QWEN3_ASR_DIR_NAME, _qwen_model_ready)
    return str(found) if found else None


def _nemotron_boundary_ready(path: Path) -> bool:
    return path.is_dir() and all(
        (path / filename).is_file() for filename in NEMOTRON_BOUNDARY_FILES
    )


def get_semantic_boundary_model_path(explicit: str | None = None) -> Path | None:
    """Find the optional Nemotron timestamp sidecar without downloading it."""
    candidates: list[Path] = []
    runtime_root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else PROJECT_ROOT
    )
    raw = str(explicit or "").strip()
    if raw:
        configured = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not configured.is_absolute():
            configured = runtime_root / configured
        candidates.append(configured)
    candidates.append(runtime_root / "models" / NEMOTRON_BOUNDARY_DIR_NAME)
    candidates.extend(root / NEMOTRON_BOUNDARY_DIR_NAME for root in model_roots())

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if _nemotron_boundary_ready(resolved):
            return resolved
    return None


def _silero_ready(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 100_000


def silero_onnx_path() -> Path:
    bundled = PACKAGE_DIR / "models" / SILERO_VAD_DIR_NAME / SILERO_VAD_ONNX_NAME
    if _silero_ready(bundled):
        return bundled
    for root in model_roots():
        candidates = (
            root / SILERO_VAD_DIR_NAME / SILERO_VAD_ONNX_NAME,
            # FunASR keeps downloaded weights in ``local_asr_models`` but its
            # bundled VAD under ``local_inference/models``.  Deriving this from
            # an explicit model root also works after PyInstaller extraction,
            # where PROJECT_ROOT no longer points at the source checkout.
            root.parent / "local_inference" / "models" / SILERO_VAD_DIR_NAME / SILERO_VAD_ONNX_NAME,
        )
        for path in candidates:
            if _silero_ready(path):
                return path
    legacy_bundle = PROJECT_ROOT.parent.parent / "funasr" / "local_inference" / "models" / SILERO_VAD_DIR_NAME / SILERO_VAD_ONNX_NAME
    if _silero_ready(legacy_bundle):
        return legacy_bundle
    return MODELS_DIR / SILERO_VAD_DIR_NAME / SILERO_VAD_ONNX_NAME


def is_silero_cached() -> bool:
    return _silero_ready(silero_onnx_path())


def is_qwen3_asr_ready() -> bool:
    return bool(get_local_model_path("qwen3-asr")) and _runtime_ready(qwen_llama_bin_dir())


def is_asr_models_ready(engine: str) -> bool:
    return engine == "qwen3-asr" and is_qwen3_asr_ready()


def is_asr_cached(engine: str) -> bool:
    return (
        engine == "qwen3-asr"
        and is_silero_cached()
        and is_qwen3_asr_ready()
        and not get_engine_runtime_issues(engine)
    )


def get_hymt2_model_path() -> Path | None:
    for root in model_roots():
        model_dir = root / HYMT2_DIR_NAME
        if model_dir.is_dir():
            if (model_dir / HYMT2_MODEL_FILE).is_file():
                return model_dir / HYMT2_MODEL_FILE
            files = sorted(path for path in model_dir.glob("*.gguf") if ".imatrix." not in path.name)
            if files:
                return files[0]
    return None


def is_hymt2_cached() -> bool:
    return get_hymt2_model_path() is not None and _runtime_ready(qwen_llama_bin_dir())


class DownloadCancelled(RuntimeError):
    """The user cancelled a model download; partial files are kept for resume."""


class DownloadBusy(RuntimeError):
    """Another model download is already running."""


_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_PROGRESS_INTERVAL_SEC = 0.5
# Check the file header after download: an error page or a truncated file must
# fail here, not later when llama.cpp tries to load it.
_FILE_MAGIC = {".gguf": b"GGUF", ".zip": b"PK"}

_download_lock = threading.Lock()
_download_cancel = threading.Event()
_download_state_lock = threading.Lock()
_download_state: dict = {
    "active": False, "component": None, "stage": None,
    "done": 0, "total": None, "error": None, "cancelled": False,
}


def get_download_progress() -> dict:
    with _download_state_lock:
        return dict(_download_state)


def _set_download_state(**values) -> None:
    with _download_state_lock:
        _download_state.update(values)


def cancel_download() -> bool:
    """Ask the running download to stop at its next chunk."""
    if not _download_lock.locked():
        return False
    _download_cancel.set()
    return True


def _report(progress_callback, stage: str, done: int, total: int | None) -> None:
    """progress_callback(stage, done_bytes, total_bytes_or_None)."""
    if progress_callback is None:
        return
    try:
        progress_callback(stage, done, total)
    except Exception:
        # Progress is display only; it must never break the download.
        logger.debug("Download progress callback failed", exc_info=True)


def _hf_endpoints() -> list[str]:
    """HuggingFace endpoints in try order: env override > official > mirrors."""
    endpoints: list[str] = []
    for value in (
        os.environ.get("REALTIME_SUBTITLE_HF_ENDPOINT"),
        os.environ.get("HF_ENDPOINT"),
        HF_ENDPOINT_OFFICIAL,
        *HF_ENDPOINT_MIRRORS,
    ):
        endpoint = (value or "").strip().rstrip("/")
        if endpoint and endpoint not in endpoints:
            endpoints.append(endpoint)
    return endpoints


def _hf_file_url(endpoint: str, repo_id: str, filename: str, revision: str = "main") -> str:
    return f"{endpoint}/{repo_id}/resolve/{revision}/{filename}"


def _hf_endpoint_reachable(url: str, timeout: float = 8.0) -> bool:
    """Fetch one byte: proves both the site and the CDN it redirects to
    (in mainland China often only the latter is blocked)."""
    try:
        request = Request(url, headers={"User-Agent": "RealtimeSubtitle", "Range": "bytes=0-0"})
        with urlopen(request, timeout=timeout) as response:
            response.read(1)
        return True
    except Exception as exc:
        logger.info("HuggingFace endpoint probe failed %s: %s", url, exc)
        return False


def _ordered_hf_endpoints(repo_id: str, filename: str) -> list[str]:
    """Move the first reachable endpoint to the front; unreachable ones stay
    at the end as a last resort (the probe itself can misjudge)."""
    endpoints = _hf_endpoints()
    unreachable: list[str] = []
    for index, endpoint in enumerate(endpoints):
        remaining = endpoints[index + 1:]
        if not remaining:
            # Last candidate: no point spending another probe timeout on it.
            return [endpoint, *unreachable]
        if _hf_endpoint_reachable(_hf_file_url(endpoint, repo_id, filename)):
            return [endpoint, *remaining, *unreachable]
        logger.warning("%s unreachable, falling back", endpoint)
        unreachable.append(endpoint)
    return unreachable


def _parse_content_range_total(headers) -> int | None:
    """Total length from Content-Range ("bytes 0-3/1069288640" / "bytes */1069288640")."""
    value = headers.get("Content-Range") if headers else None
    if not value or "/" not in value:
        return None
    total = value.rsplit("/", 1)[1].strip()
    return int(total) if total.isdigit() else None


def _verify_download(path: Path, expected_bytes: int | None, magic: bytes | None) -> None:
    actual = path.stat().st_size
    if expected_bytes is not None and actual != expected_bytes:
        raise OSError(f"size mismatch: expected {expected_bytes} bytes, got {actual}")
    if magic:
        with open(path, "rb") as handle:
            if handle.read(len(magic)) != magic:
                raise OSError("unexpected file header (error page or blocked by a gateway?)")


def _verify_or_discard(part: Path, expected_bytes: int | None, magic: bytes | None) -> None:
    try:
        _verify_download(part, expected_bytes, magic)
    except OSError:
        # Corrupt content cannot be resumed; delete so the next try starts over.
        part.unlink(missing_ok=True)
        raise


def _stream_download(url: str, dest: Path, stage: str, progress_callback=None,
                     timeout: float = 30.0) -> None:
    """Download to dest.part (resumable), verify, then rename to dest.

    A cancelled or interrupted download leaves dest.part behind; the next call
    (also from a different mirror) resumes it. Only a failed check deletes it.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    done = part.stat().st_size if part.is_file() else 0
    magic = _FILE_MAGIC.get(dest.suffix.lower())

    headers = {"User-Agent": "RealtimeSubtitle"}
    if done:
        headers["Range"] = f"bytes={done}-"
        logger.info("Resuming %s at %.1f MB", stage, done / (1024 * 1024))

    try:
        response = urlopen(Request(url, headers=headers), timeout=timeout)
    except HTTPError as exc:
        if exc.code == 416 and done:
            # Range past the end: .part is either complete or longer than the
            # remote (corrupt). The 416 Content-Range "bytes */<total>" tells which.
            _verify_or_discard(part, _parse_content_range_total(exc.headers), magic)
            os.replace(part, dest)
            _report(progress_callback, stage, done, done)
            return
        raise

    with response:
        status = getattr(response, "status", None) or response.getcode()
        if done and status != 206:
            logger.info("%s: server ignored the range request, restarting", stage)
            done = 0
        if (response.headers.get_content_type() or "") == "text/html":
            # Binary weights are never served as HTML: this is a portal/gateway page.
            raise OSError(f"{stage}: got an HTML page (blocked by a network gateway?)")
        length = response.headers.get("Content-Length")
        total = done + int(length) if length and length.isdigit() else None

        _report(progress_callback, stage, done, total)
        last_report = time.monotonic()
        with open(part, "ab" if done else "wb") as handle:
            while True:
                if _download_cancel.is_set():
                    raise DownloadCancelled(stage)
                chunk = response.read(_DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - last_report >= _PROGRESS_INTERVAL_SEC:
                    last_report = now
                    _report(progress_callback, stage, done, total)

    _report(progress_callback, stage, done, total)
    _verify_or_discard(part, total, magic)
    os.replace(part, dest)


def _download_file(url: str, dest: Path, stage: str, progress_callback=None) -> None:
    logger.info("Downloading %s", stage)
    _stream_download(url, dest, stage, progress_callback)


def download_silero(progress_callback=None) -> None:
    if is_silero_cached():
        return
    _download_file(
        SILERO_VAD_ONNX_URL,
        MODELS_DIR / SILERO_VAD_DIR_NAME / SILERO_VAD_ONNX_NAME,
        "silero",
        progress_callback,
    )


def _download_llama_runtime(destination: Path, progress_callback=None) -> None:
    if _runtime_ready(destination):
        return
    if sys.platform != "win32":
        raise RuntimeError("Automatic llama.cpp runtime download is currently supported on Windows only")
    destination.mkdir(parents=True, exist_ok=True)
    try:
        request = Request(LLAMA_CPP_LATEST_API, headers={"User-Agent": "RealtimeSubtitle"})
        with urlopen(request, timeout=15) as response:
            tag = json.loads(response.read())["tag_name"]
    except Exception:
        tag = "b8391"
    archive_path = MODELS_DIR / f"llama-cpp-vulkan-{tag}.zip"
    _download_file(
        LLAMA_CPP_DLL_URL_TEMPLATE.format(tag=tag), archive_path, "llama-runtime", progress_callback,
    )
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.namelist():
            name = os.path.basename(member)
            if not name:
                continue
            if name in _runtime_names() or name.startswith("ggml-") and name.endswith(".dll"):
                with archive.open(member) as source, open(destination / name, "wb") as target:
                    shutil.copyfileobj(source, target)
    archive_path.unlink(missing_ok=True)


def download_qwen3_asr(progress_callback=None) -> None:
    ensure_vendor_sources("qwen3-asr")
    model_dir = MODELS_DIR / QWEN3_ASR_DIR_NAME
    model_dir.mkdir(parents=True, exist_ok=True)
    if not _qwen_model_ready(model_dir):
        archive_path = MODELS_DIR / "qwen3-asr-1.7b-gguf.zip"
        _download_file(QWEN3_ASR_MODEL_URL, archive_path, "qwen3-asr", progress_callback)
        _report(progress_callback, "qwen3-asr-extract", 0, None)
        with zipfile.ZipFile(archive_path, "r") as archive:
            for member in archive.namelist():
                name = os.path.basename(member)
                if name in QWEN3_ASR_FILES:
                    with archive.open(member) as source, open(model_dir / name, "wb") as target:
                        shutil.copyfileobj(source, target)
        archive_path.unlink(missing_ok=True)
    _ensure_llama_runtime(progress_callback)


def _ensure_llama_runtime(progress_callback=None) -> None:
    # Qwen3-ASR and Hy-MT2 share one llama.cpp runtime; whichever is
    # downloaded first brings it along.
    if not _runtime_ready(qwen_llama_bin_dir()):
        _download_llama_runtime(MODELS_DIR / QWEN_LLAMA_VULKAN_BIN_DIR_NAME, progress_callback)


def prepare_engine(engine: str, progress_callback=None) -> None:
    if engine != "qwen3-asr":
        raise ValueError(f"Unknown local ASR engine: {engine}")
    download_silero(progress_callback)
    if not is_qwen3_asr_ready():
        download_qwen3_asr(progress_callback)


def download_hymt2(progress_callback=None) -> None:
    """Download the Hy-MT2 GGUF; falls back to mirrors when huggingface.co is unreachable."""
    if get_hymt2_model_path() is None:
        dest = MODELS_DIR / HYMT2_DIR_NAME / HYMT2_MODEL_FILE
        failures: list[str] = []
        for endpoint in _ordered_hf_endpoints(HYMT2_REPO_ID, HYMT2_MODEL_FILE):
            host = urlparse(endpoint).netloc or endpoint
            try:
                logger.info("Downloading Hy-MT2 (%s) from %s", HYMT2_MODEL_FILE, host)
                _stream_download(
                    _hf_file_url(endpoint, HYMT2_REPO_ID, HYMT2_MODEL_FILE),
                    dest, "hymt2", progress_callback,
                )
                break
            except DownloadCancelled:
                raise
            except Exception as exc:
                logger.warning("Hy-MT2 download from %s failed: %s", host, exc)
                failures.append(f"{host}: {exc}")
        else:
            raise RuntimeError("Hy-MT2 download failed (" + "; ".join(failures) + ")")
    _ensure_llama_runtime(progress_callback)


_DOWNLOADERS = {
    "qwen3-asr": lambda progress: prepare_engine("qwen3-asr", progress),
    "hymt2": download_hymt2,
}


def run_download(component: str) -> None:
    """Run one component download, publishing progress for the UI.

    Only one download runs at a time; raises DownloadBusy otherwise.
    """
    downloader = _DOWNLOADERS.get(component)
    if downloader is None:
        raise ValueError(f"Unknown local model component: {component}")
    if not _download_lock.acquire(blocking=False):
        raise DownloadBusy(get_download_progress().get("component") or "")
    try:
        _download_cancel.clear()
        _set_download_state(
            active=True, component=component, stage=None, done=0, total=None,
            error=None, cancelled=False,
        )

        def progress(stage, done, total):
            _set_download_state(stage=stage, done=int(done), total=total)

        try:
            downloader(progress)
        except DownloadCancelled:
            _set_download_state(cancelled=True)
            raise
        except Exception as exc:
            _set_download_state(error=str(exc))
            raise
    finally:
        _set_download_state(active=False)
        _download_cancel.clear()
        _download_lock.release()


def get_engine_status(engine: str) -> dict:
    if engine != "qwen3-asr":
        raise ValueError(f"Unknown local ASR engine: {engine}")
    model_path = get_local_model_path(engine)
    runtime_path = qwen_llama_bin_dir()
    return {
        "engine": engine,
        "display_name": LOCAL_INFERENCE_DISPLAY_NAMES[engine],
        "ready": is_asr_cached(engine),
        "model_ready": bool(model_path),
        "model_path": model_path,
        "runtime_ready": _runtime_ready(runtime_path),
        "runtime_path": str(runtime_path),
        "vad_ready": is_silero_cached(),
        "vad_path": str(silero_onnx_path()),
        "runtime_issues": get_engine_runtime_issues(engine),
        "estimated_bytes": _MODEL_SIZE_BYTES[engine],
        "estimated_vram_bytes": _MODEL_VRAM_BYTES[engine],
    }


def get_hymt2_status() -> dict:
    path = get_hymt2_model_path()
    return {
        "engine": "hymt2",
        "display_name": LOCAL_INFERENCE_DISPLAY_NAMES["hymt2"],
        "ready": is_hymt2_cached(),
        "model_ready": path is not None,
        "model_path": str(path) if path else None,
        "runtime_ready": _runtime_ready(qwen_llama_bin_dir()),
        "runtime_path": str(qwen_llama_bin_dir()),
        "runtime_issues": get_common_runtime_issues(),
        "install_dir": str((MODELS_DIR / HYMT2_DIR_NAME).resolve()),
        "estimated_bytes": _MODEL_SIZE_BYTES["hymt2"],
        "estimated_vram_bytes": _MODEL_VRAM_BYTES["hymt2"],
    }


def get_all_local_models_status() -> dict:
    return {
        "models_dir": str(MODELS_DIR.resolve()),
        "search_roots": [str(path) for path in model_roots()],
        "asr": {"qwen3-asr": get_engine_status("qwen3-asr")},
        "translation": {"hymt2": get_hymt2_status()},
    }
