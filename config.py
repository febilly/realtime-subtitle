"""
配置文件 - 存储所有配置项和常量
"""
import os
import sys
import locale
import time
from dotenv import load_dotenv

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
            lang_code = system_locale.split('_')[0].lower()
            print(f"🌐 Detected system language: {system_locale} -> {lang_code}")
            return lang_code
        else:
            print("⚠️  Unable to detect system language, using default: zh")
            return "zh"
    except Exception as e:
        print(f"⚠️  Failed to get system language: {e}, using default: zh")
        return "zh"


# 根据配置决定使用哪个目标语言
if USE_SYSTEM_LANGUAGE:
    TRANSLATION_TARGET_LANG = get_system_language()
else:
    TRANSLATION_TARGET_LANG = TARGET_LANG

print(f"✅ Translation target language set to: {TRANSLATION_TARGET_LANG}")

# 强校验：如果既没有提供永久 API Key，也没有提供用于获取临时 key 的 URL，则退出。
if not os.environ.get("SONIOX_API_KEY") and not SONIOX_TEMP_KEY_URL:
    print("❌ Configuration error: neither SONIOX_API_KEY nor SONIOX_TEMP_KEY_URL is set.\nPlease set one of them in your environment or in the .env file.")
    time.sleep(5)
    sys.exit(1)
