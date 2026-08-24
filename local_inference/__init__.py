from __future__ import annotations

import importlib.util
from typing import Dict, List

LOCAL_INFERENCE_ENGINES = ("qwen3-asr",)
LOCAL_MT_ENGINES = ("hymt2",)
LOCAL_INFERENCE_DISPLAY_NAMES: Dict[str, str] = {
    "qwen3-asr": "Qwen3-ASR 1.7B",
    "hymt2": "Hy-MT2 1.8B",
}

COMMON_RUNTIME_MODULES = (
    "numpy",
    "onnxruntime",
)
ENGINE_RUNTIME_MODULES = {"qwen3-asr": ("gguf",)}


def _missing_modules(modules: tuple[str, ...]) -> List[str]:
    return [name for name in modules if importlib.util.find_spec(name) is None]


def get_common_runtime_issues() -> List[str]:
    return _missing_modules(COMMON_RUNTIME_MODULES)


def get_engine_runtime_issues(engine: str) -> List[str]:
    issues = get_common_runtime_issues()
    issues.extend(_missing_modules(ENGINE_RUNTIME_MODULES.get(engine, ())))
    return sorted(set(issues))


def is_engine_runtime_available(engine: str) -> bool:
    return not get_engine_runtime_issues(engine)


def is_local_inference_ui_enabled() -> bool:
    return True


def get_local_inference_features() -> dict:
    import config

    return {
        "local_inference_build_enabled": True,
        "local_inference_ui_enabled": True,
        "qwen3_vram_mb": int(getattr(config, "QWEN3_ASR_VRAM_MB", 0)),
        "qwen3_encoder_vram_mb": int(getattr(config, "QWEN3_ASR_ENCODER_VRAM_MB", 0)),
        "hymt2_vram_mb": int(getattr(config, "HYMT2_VRAM_MB", 0)),
        "engines": {
            "qwen3-asr": {
                "display_name": LOCAL_INFERENCE_DISPLAY_NAMES["qwen3-asr"],
                "runtime_available": is_engine_runtime_available("qwen3-asr"),
                "runtime_issues": get_engine_runtime_issues("qwen3-asr"),
            }
        },
    }
