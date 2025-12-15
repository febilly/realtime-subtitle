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

# Soniox API配置
SONIOX_WEBSOCKET_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
SONIOX_TEMP_KEY_URL = os.environ.get("SONIOX_TEMP_KEY_URL")

# 自动使用系统语言
# True: 自动读取系统语言设置作为目标翻译语言
# False: 使用下面手动指定的 TARGET_LANG
USE_SYSTEM_LANGUAGE = True

# 手动指定目标语言（当 USE_SYSTEM_LANGUAGE=False 时使用）
TARGET_LANG = "ja"
TARGET_LANG_1 = "en"
TARGET_LANG_2 = "zh"

# 自动打开浏览器
# True: 启动服务器后自动在浏览器中打开前端
# False: 需要手动打开浏览器
# AUTO_OPEN_BROWSER = True

# 服务器配置
# SERVER_PORT 设置为 0 时将自动选择一个空闲端口
SERVER_HOST = 'localhost'
SERVER_PORT = 8080


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
