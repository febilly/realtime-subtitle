"""
Soniox客户端模块 - 处理与Soniox STT服务的连接和音频流
"""
import os
import requests
from config import SONIOX_TEMP_KEY_URL, TARGET_LANG_1, TARGET_LANG_2


def get_api_key() -> str:
    """
    获取API Key
    1. 先尝试从环境变量 SONIOX_API_KEY 加载
    2. 如果没有，则请求临时key
    """
    # 尝试从环境变量获取
    api_key = os.environ.get("SONIOX_API_KEY")
    
    if api_key:
        print(f"✅ Using API Key from environment variable")
        return api_key
    
    # 如果没有，获取临时key
    print("⏳ API Key not found in environment, fetching temporary key...")
    try:
        response = requests.post(SONIOX_TEMP_KEY_URL, timeout=10)
        response.raise_for_status()
        
        data = response.json()
        temp_key = data.get("apiKey")
        expires_at = data.get("expiresAt")
        
        if temp_key:
            print(f"✅ Successfully obtained temporary API Key")
            print(f"   Key: {temp_key}")
            print(f"   Expires at: {expires_at}")
            return temp_key
        else:
            raise RuntimeError("Invalid temporary key response format")
            
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch temporary API Key: {e}")
    except Exception as e:
        raise RuntimeError(f"Failed to parse temporary API Key: {e}")


def get_config(api_key: str, audio_format: str, translation: str, translation_target_lang: str | None = None) -> dict:
    """获取Soniox STT配置"""
    from config import (
        TRANSLATION_TARGET_LANG,
        normalize_language_code,
        is_supported_language_code,
    )

    config = {
        "api_key": api_key,
        "model": "stt-rt-v3",
        "language_hints": ["en", "zh", "ja", "ko", "ru"],
        "enable_language_identification": True,
        "enable_speaker_diarization": True,
        "enable_endpoint_detection": True,
    }

    # Audio format for microphone input
    if audio_format == "auto":
        config["audio_format"] = "auto"
    elif audio_format == "pcm_s16le":
        config["audio_format"] = "pcm_s16le"
        config["sample_rate"] = 16000
        config["num_channels"] = 1
    else:
        raise ValueError(f"Unsupported audio_format: {audio_format}")

    # Translation options
    if translation == "none":
        pass
    elif translation == "one_way":
        target_lang = TRANSLATION_TARGET_LANG
        if translation_target_lang is not None:
            normalized = normalize_language_code(translation_target_lang)
            if not is_supported_language_code(normalized):
                raise ValueError(f"Unsupported translation target language: {translation_target_lang}")
            target_lang = normalized

        config["translation"] = {
            "type": "one_way",
            "target_language": target_lang,
        }
    elif translation == "two_way":
        config["translation"] = {
            "type": "two_way",
            "language_a": TARGET_LANG_1,
            "language_b": TARGET_LANG_2,
        }
    else:
        raise ValueError(f"Unsupported translation: {translation}")

    return config
