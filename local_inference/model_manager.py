from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.request import Request, urlopen, urlretrieve

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
            files = sorted(model_dir.glob("*.gguf"))
            if files:
                return files[0]
    return None


def is_hymt2_cached() -> bool:
    return get_hymt2_model_path() is not None and _runtime_ready(qwen_llama_bin_dir())


def _download_file(url: str, dest: Path, description: str) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s", description)
    urlretrieve(url, str(dest))


def download_silero() -> None:
    if is_silero_cached():
        return
    _download_file(
        SILERO_VAD_ONNX_URL,
        MODELS_DIR / SILERO_VAD_DIR_NAME / SILERO_VAD_ONNX_NAME,
        "Silero VAD",
    )


def _download_llama_runtime(destination: Path) -> None:
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
    archive_path = MODELS_DIR / "llama-cpp-vulkan.zip"
    _download_file(LLAMA_CPP_DLL_URL_TEMPLATE.format(tag=tag), archive_path, f"llama.cpp Vulkan {tag}")
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.namelist():
            name = os.path.basename(member)
            if not name:
                continue
            if name in _runtime_names() or name.startswith("ggml-") and name.endswith(".dll"):
                with archive.open(member) as source, open(destination / name, "wb") as target:
                    shutil.copyfileobj(source, target)
    archive_path.unlink(missing_ok=True)


def download_qwen3_asr() -> None:
    ensure_vendor_sources("qwen3-asr")
    model_dir = MODELS_DIR / QWEN3_ASR_DIR_NAME
    model_dir.mkdir(parents=True, exist_ok=True)
    if not _qwen_model_ready(model_dir):
        archive_path = MODELS_DIR / "qwen3-asr-1.7b-gguf.zip"
        _download_file(QWEN3_ASR_MODEL_URL, archive_path, "Qwen3-ASR 1.7B")
        with zipfile.ZipFile(archive_path, "r") as archive:
            for member in archive.namelist():
                name = os.path.basename(member)
                if name in QWEN3_ASR_FILES:
                    with archive.open(member) as source, open(model_dir / name, "wb") as target:
                        shutil.copyfileobj(source, target)
        archive_path.unlink(missing_ok=True)
    _download_llama_runtime(MODELS_DIR / QWEN_LLAMA_VULKAN_BIN_DIR_NAME)


def prepare_engine(engine: str) -> None:
    if engine != "qwen3-asr":
        raise ValueError(f"Unknown local ASR engine: {engine}")
    download_silero()
    if not is_qwen3_asr_ready():
        download_qwen3_asr()


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
    }


def get_all_local_models_status() -> dict:
    return {
        "models_dir": str(MODELS_DIR.resolve()),
        "search_roots": [str(path) for path in model_roots()],
        "asr": {"qwen3-asr": get_engine_status("qwen3-asr")},
        "translation": {"hymt2": get_hymt2_status()},
    }
