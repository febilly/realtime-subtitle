"""
配置文件 - 存储所有配置项和常量
"""
import os
import sys
import locale
import time
import threading
from dotenv import load_dotenv


# Soniox 支持的语言（ISO 639-1），用于校验系统语言/目标语言。
# 来源：docs/supported-languages.mdx
SUPPORTED_LANGUAGE_CODES = {
    "af", "sq", "ar", "az", "eu", "be", "bn", "bs", "bg", "ca",
    "zh", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "gl",
    "de", "el", "gu", "he", "hi", "hu", "id", "it", "ja", "kn",
    "kk", "ko", "lv", "lt", "mk", "ms", "ml", "mr", "no", "fa",
    "pl", "pt", "pa", "ro", "ru", "sr", "sk", "sl", "es", "sw",
    "sv", "tl", "ta", "te", "th", "tr", "uk", "ur", "vi", "cy",
}


def normalize_language_code(lang: str) -> str:
    """Normalize language code to ISO 639-1 lowercase where possible.

    Examples:
    - 'zh_CN' -> 'zh'
    - 'en-US' -> 'en'
    - ' JA '  -> 'ja'
    """
    if lang is None:
        return ""
    value = str(lang).strip().lower()
    if not value:
        return ""

    # common separators
    for sep in ("_", "-"):
        if sep in value:
            value = value.split(sep, 1)[0]
            break

    return value


def is_supported_language_code(lang: str) -> bool:
    code = normalize_language_code(lang)
    return bool(code) and code in SUPPORTED_LANGUAGE_CODES

# 加载 .env（在此处加载确保在其他模块导入本配置时也能读取到环境变量）
load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    value = str(value).strip().lower()
    if value in ("1", "true", "yes", "y", "on"):
        return True
    if value in ("0", "false", "no", "n", "off"):
        return False
    return default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None else str(value)

# Soniox API配置
SONIOX_WEBSOCKET_URL = _env_str("SONIOX_WEBSOCKET_URL", "wss://stt-rt.soniox.com/transcribe-websocket")
SONIOX_TEMP_KEY_URL = os.environ.get("SONIOX_TEMP_KEY_URL")

# 自动使用系统语言
# True: 自动读取系统语言设置作为目标翻译语言
# False: 使用下面手动指定的 TARGET_LANG
USE_SYSTEM_LANGUAGE = _env_bool("USE_SYSTEM_LANGUAGE", True)

# 手动指定目标语言（当 USE_SYSTEM_LANGUAGE=False 时使用）
TARGET_LANG = _env_str("TARGET_LANG", "ja")
TARGET_LANG_1 = _env_str("TARGET_LANG_1", "en")
TARGET_LANG_2 = _env_str("TARGET_LANG_2", "zh")

# 翻译模式: none | one_way | two_way
# - none: 不启用翻译
# - one_way: 单向翻译（目标语言由 TRANSLATION_TARGET_LANG 决定）
# - two_way: 双向翻译（语言对由 TARGET_LANG_1/TARGET_LANG_2 决定）
_TRANSLATION_MODE_RAW = _env_str("TRANSLATION_MODE", "one_way")
TRANSLATION_MODE = str(_TRANSLATION_MODE_RAW).strip().lower()
if TRANSLATION_MODE not in ("none", "one_way", "two_way"):
    print(f"⚠️  Invalid TRANSLATION_MODE: {_TRANSLATION_MODE_RAW}, fallback to: one_way")
    TRANSLATION_MODE = "one_way"

# 自动打开内置 WebView（默认开启）
# True: 启动后创建嵌入式 webview 窗口
# False: 仅在命令行打印访问 URL，需要手动在浏览器打开；关闭网页时不会自动退出程序
AUTO_OPEN_WEBVIEW = _env_bool("AUTO_OPEN_WEBVIEW", True)

# UI 锁定：隐藏“手动控制”相关按钮，并在后端禁用对应操作
# True: 前端隐藏“重启/暂停/自动重启开关/音频源/OSC 发送”；后端拒绝 /pause、/resume、手动 /restart、
#       /audio-source（切换）以及 /osc-translation（切换）；同时前端强制开启“断线自动重启”
# False: 正常显示并允许手动控制
LOCK_MANUAL_CONTROLS = _env_bool("LOCK_MANUAL_CONTROLS", False)

# Twitch 音频串流识别（默认关闭）
# True: 使用 streamlink 从指定 Twitch 频道拉取直播流，并通过 ffmpeg 仅提取音频转为 16kHz mono PCM 供识别
# False: 使用本机系统音频/麦克风采集
USE_TWITCH_AUDIO_STREAM = _env_bool("USE_TWITCH_AUDIO_STREAM", False)

# 当 VRChat 游戏内麦克风静音(MuteSelf=true)时，是否静音发送中的“麦克风音频分量”
# - True(默认):
#   - microphone 模式：发送静音帧（保持发送节奏，不暂停）
#   - mix 模式：仅将麦克风分量置零，系统分量继续发送
# - False: 忽略 VRChat 的 MuteSelf 状态
MUTE_MIC_WHEN_VRCHAT_SELF_MUTED = _env_bool("MUTE_MIC_WHEN_VRCHAT_SELF_MUTED", True)

# 混合音频权重（仅在音频源为 mix 时生效）
# 约定：
# - "自己" = 麦克风（microphone）
# - "别人" = 系统/扬声器环回（system）
#
# 你只需要设置其中一个变量，另一个会自动按 1-该值 计算。
_MIX_OWN_VOLUME_RAW = _env_float("MIX_OWN_VOLUME", 0.5)
MIX_OWN_VOLUME = min(1.0, max(0.0, _MIX_OWN_VOLUME_RAW))
MIX_OTHER_VOLUME = 1.0 - MIX_OWN_VOLUME

# 说话人分离开关（默认开启）
# True: 启用说话人分离（前端显示说话人标签）
# False: 关闭说话人分离（前端隐藏说话人标签）
ENABLE_SPEAKER_DIARIZATION = _env_bool("ENABLE_SPEAKER_DIARIZATION", True)

# 隐藏说话人标签（默认关闭）
# True: 前端隐藏说话人序号标签（即使启用说话人分离）
# False: 正常显示说话人标签
HIDE_SPEAKER_LABELS = _env_bool("HIDE_SPEAKER_LABELS", False)

# 默认断句模式: 'translation' | 'endpoint' | 'punctuation'
# - translation: 基于 Soniox 的 <end> 标记
# - endpoint: 基于 Soniox 的 endpoint_detected 标志
# - punctuation: 基于句末标点符号（默认）
DEFAULT_SEGMENT_MODE = _env_str("DEFAULT_SEGMENT_MODE", "punctuation")

# Twitch 频道名（不含 https://www.twitch.tv/ 前缀）
TWITCH_CHANNEL = _env_str("TWITCH_CHANNEL", "")

# 优先选择的码流（通常可用：audio_only / best）
TWITCH_STREAM_QUALITY = _env_str("TWITCH_STREAM_QUALITY", "audio_only")

# ffmpeg 可执行文件路径（默认依赖 PATH 中的 ffmpeg）
FFMPEG_PATH = _env_str("FFMPEG_PATH", "ffmpeg")

# 服务器配置
# SERVER_PORT 设置为 0 时将自动选择一个空闲端口
# AUTO_OPEN_WEBVIEW=True 时强制绑定到 127.0.0.1；关闭后默认绑定到 0.0.0.0 以便局域网访问
SERVER_HOST = _env_str("SERVER_HOST", "0.0.0.0")
SERVER_PORT = _env_int("SERVER_PORT", 8080)

# LLM（OpenAI 兼容）配置：用于对“已完成的译文段落”做最小改动修复。
# 说明：
# - LLM_BASE_URL 示例：https://openrouter.ai/api/v1
# - LLM_API_KEY 用于鉴权
# - LLM_MODEL 示例：openai/gpt-oss-120b:google-vertex
LLM_BASE_URL = _env_str("LLM_BASE_URL", "")
LLM_API_KEY = _env_str("LLM_API_KEY", "")
LLM_MODEL = _env_str("LLM_MODEL", "openai/gpt-oss-120b:google-vertex")

# LLM refine 默认开关（启动时的默认值；若前端未锁定，可被用户手动切换）
LLM_REFINE_DEFAULT_ENABLED = _env_bool("LLM_REFINE_DEFAULT_ENABLED", True)

# LLM 默认翻译模式: off | refine | translate
# 仅在浏览器没有历史记录或开启 LOCK_MANUAL_CONTROLS 时生效
_LLM_DEFAULT_MODE_RAW = _env_str("LLM_REFINE_DEFAULT_MODE", "")
_LLM_DEFAULT_MODE = str(_LLM_DEFAULT_MODE_RAW).strip().lower()
if _LLM_DEFAULT_MODE not in ("off", "refine", "translate"):
    _LLM_DEFAULT_MODE = "refine" if LLM_REFINE_DEFAULT_ENABLED else "off"
LLM_REFINE_DEFAULT_MODE = _LLM_DEFAULT_MODE

# Optional suffix appended to the end of the LLM prompt.
# Default: empty string (no suffix). Example: "/no_think"
LLM_PROMPT_SUFFIX = _env_str("LLM_PROMPT_SUFFIX", "")

# LLM temperature (0.0-2.0). Lower is more deterministic.
LLM_TEMPERATURE = min(2.0, max(0.0, _env_float("LLM_TEMPERATURE", 0.2)))

# 是否在前端展示 refined 译文相对原始译文的修订（diff 高亮）。
# - True: 删除内容红底+删除线；新增内容绿底
# - False: 仅展示最终译文（默认）
LLM_REFINE_SHOW_DIFF = _env_bool("LLM_REFINE_SHOW_DIFF", True)

# Diff 高亮时，是否显示“被删除”的文本。
# - True: 和当前行为一致（红底+删除线显示被删内容）
# - False: 只标绿新增内容，不显示被删内容（默认）
LLM_REFINE_SHOW_DELETIONS = _env_bool("LLM_REFINE_SHOW_DELETIONS", False)

# LLM refine 时携带的“上文语境”条数（已完结句子对：原文+译文）。
# - 可设为 0 表示不携带上文
# - 默认 5
LLM_REFINE_CONTEXT_COUNT = max(0, _env_int("LLM_REFINE_CONTEXT_COUNT", 5))

# LLM refine 的最大输出 tokens。
# 注意：不同服务商/模型对 max_tokens 上限不同。
LLM_REFINE_MAX_TOKENS = min(8192, max(1, _env_int("LLM_REFINE_MAX_TOKENS", 1024)))


def _parse_llm_api_keys(raw: str) -> list[str]:
        """Parse LLM_API_KEY which may contain multiple keys separated by commas.

        Example:
            "keyA, keyB,keyC" -> ["keyA", "keyB", "keyC"]
        """
        if raw is None:
                return []
        parts = [p.strip() for p in str(raw).split(",")]
        # Filter empties and common placeholder values.
        keys = [p for p in parts if p and p != "LLM_API_KEY"]
        return keys


_LLM_API_KEYS: list[str] = _parse_llm_api_keys(LLM_API_KEY)
_LLM_API_KEY_LOCK = threading.Lock()
_LLM_API_KEY_INDEX = 0


def get_llm_api_key() -> str:
    """Return one configured LLM API key.

    Supports multiple keys via comma-separated LLM_API_KEY and returns them in round-robin.
    """
    global _LLM_API_KEY_INDEX

    keys = _LLM_API_KEYS
    if not keys:
        return ""
    if len(keys) == 1:
        return keys[0]

    with _LLM_API_KEY_LOCK:
        key = keys[_LLM_API_KEY_INDEX % len(keys)]
        _LLM_API_KEY_INDEX += 1
        return key


def get_llm_api_keys() -> list[str]:
    """Return all configured LLM API keys (after parsing/stripping)."""
    return list(_LLM_API_KEYS)


def is_llm_refine_available() -> bool:
    """Whether the backend has enough configuration to use LLM refine feature."""
    keys = get_llm_api_keys()
    base_url = (LLM_BASE_URL or "").strip()
    model = (LLM_MODEL or "").strip()
    if not keys:
        return False
    if not base_url or not model:
        return False
    return True


def get_resource_path(relative_path):
    """获取资源文件的绝对路径，兼容开发环境和PyInstaller打包后的环境"""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller创建的临时文件夹
        return os.path.join(sys._MEIPASS, relative_path)
    # 开发环境
    return os.path.join(os.path.abspath('.'), relative_path)


def get_system_language() -> str:
    """
    获取系统语言代码
    返回 ISO 639-1 两字母代码（如 'zh', 'en', 'ja', 'ko' 等）
    """
    try:
        # 获取系统语言设置
        system_locale = locale.getdefaultlocale()[0]  # 例如: 'zh_CN', 'en_US', 'ja_JP'
        
        if system_locale:
            # 提取语言代码（前两个字母）
            lang_code = normalize_language_code(system_locale)
            if is_supported_language_code(lang_code):
                print(f"🌐 Detected system language: {system_locale} -> {lang_code}")
                return lang_code
            print(f"⚠️  Detected system language not supported: {system_locale} -> {lang_code}, fallback to: en")
            return "en"
        else:
            print("⚠️  Unable to detect system language, using default: en")
            return "en"
    except Exception as e:
        print(f"⚠️  Failed to get system language: {e}, using default: en")
        return "en"


# 根据配置决定使用哪个目标语言
if USE_SYSTEM_LANGUAGE:
    TRANSLATION_TARGET_LANG = get_system_language()
else:
    normalized_target = normalize_language_code(TARGET_LANG)
    if is_supported_language_code(normalized_target):
        TRANSLATION_TARGET_LANG = normalized_target
    else:
        print(f"⚠️  Config TARGET_LANG not supported: {TARGET_LANG} -> {normalized_target}, fallback to: en")
        TRANSLATION_TARGET_LANG = "en"

print(f"✅ Translation target language set to: {TRANSLATION_TARGET_LANG}")

# 强校验：如果既没有提供永久 API Key，也没有提供用于获取临时 key 的 URL，则退出。
if not os.environ.get("SONIOX_API_KEY") and not SONIOX_TEMP_KEY_URL:
    print("❌ Configuration error: neither SONIOX_API_KEY nor SONIOX_TEMP_KEY_URL is set.\nPlease set one of them in your environment or in the .env file.")
    input("Press Enter to exit...")
    sys.exit(1)
