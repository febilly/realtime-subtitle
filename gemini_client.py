"""
Gemini Live API客户端模块 - 处理与Gemini Live Translation服务的连接和音频流

Reference: https://ai.google.dev/gemini-api/docs/live-api/live-translate
"""
import os
import json
import base64
import requests
from websockets.sync.client import connect as sync_connect

from config import (
    GEMINI_WEBSOCKET_URL,
    GEMINI_MODEL,
    GEMINI_TEMP_KEY_URL,
    GEMINI_ECHO_TARGET_LANGUAGE,
    TARGET_LANG_1,
)

SETUP_COMPLETE_TIMEOUT_SECONDS = 15.0

# Known placements of the transcription/translation fields in the setup message.
# The first one that the server accepts is cached for subsequent streams.
SETUP_LAYOUTS = ("setup_level", "all_setup_level", "generation_config")
_working_setup_layout: str | None = None

_warned_two_way = False


def _get_temp_key_request_headers() -> dict | None:
    """Read optional temp-key request headers from .env via environment variable.

    Expected format:
    GEMINI_TEMP_KEY_HEADERS={"Authorization":"Bearer xxx","X-Token":"yyy"}
    """
    raw = os.environ.get("GEMINI_TEMP_KEY_HEADERS", "").strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid GEMINI_TEMP_KEY_HEADERS JSON: {e}")

    if not isinstance(parsed, dict):
        raise RuntimeError("GEMINI_TEMP_KEY_HEADERS must be a JSON object")

    headers = {}
    for key, value in parsed.items():
        header_name = str(key).strip()
        header_value = str(value).strip()
        if header_name and header_value:
            headers[header_name] = header_value

    return headers or None


def get_api_key() -> str:
    """
    获取API Key
    1. 先尝试从环境变量 GEMINI_API_KEY 加载
    2. 如果没有，则请求临时key（GEMINI_TEMP_KEY_URL，例如返回 ephemeral token 的服务）
    """
    api_key = os.environ.get("GEMINI_API_KEY")

    if api_key:
        print(f"✅ Using API Key from environment variable")
        return api_key

    print("⏳ API Key not found in environment, fetching temporary key...")
    try:
        headers = _get_temp_key_request_headers()
        request_kwargs = {"timeout": 10}
        if headers:
            request_kwargs["headers"] = headers

        response = requests.get(GEMINI_TEMP_KEY_URL, **request_kwargs)
        response.raise_for_status()

        temp_key = response.text.strip()

        if temp_key:
            print(f"✅ Successfully obtained temporary API Key")
            return temp_key
        else:
            raise RuntimeError("Temporary key response is empty")

    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch temporary API Key: {e}")
    except Exception as e:
        raise RuntimeError(f"Failed to parse temporary API Key: {e}")


def get_setup_message(translation: str, translation_target_lang: str | None = None, layout: str = "setup_level") -> dict:
    """构建Gemini Live API的setup消息。

    translation:
    - "none": 仅使用输入语音转写（输出转写在会话层被忽略）
    - "one_way": 翻译到 translation_target_lang
    - "two_way": Gemini Live Translation 仅支持单一目标语言；
      降级为 one_way，目标语言取 TARGET_LANG_1，并关闭 echo（目标语言语音保持原文显示）
    """
    from config import (
        TRANSLATION_TARGET_LANG,
        canonicalize_language_code,
        normalize_language_code,
        is_supported_language_code,
        to_gemini_language_code,
    )
    global _warned_two_way

    target_lang = TRANSLATION_TARGET_LANG
    echo_target_language = bool(GEMINI_ECHO_TARGET_LANGUAGE)

    if translation == "none":
        # The live-translate model always runs as a translator; we still need a
        # valid translationConfig, but the session layer drops output transcripts.
        pass
    elif translation == "one_way":
        if translation_target_lang is not None:
            canonical = canonicalize_language_code(translation_target_lang)
            if not is_supported_language_code(canonical):
                raise ValueError(f"Unsupported translation target language: {translation_target_lang}")
            target_lang = canonical
    elif translation == "two_way":
        if not _warned_two_way:
            print(
                "⚠️  Gemini Live Translation does not support two-way translation; "
                f"falling back to one-way into TARGET_LANG_1='{TARGET_LANG_1}'."
            )
            _warned_two_way = True
        normalized = normalize_language_code(TARGET_LANG_1)
        if is_supported_language_code(normalized):
            target_lang = normalized
        echo_target_language = False
    else:
        raise ValueError(f"Unsupported translation: {translation}")

    translation_config = {
        "targetLanguageCode": to_gemini_language_code(target_lang),
        "echoTargetLanguage": echo_target_language,
    }

    setup = {
        "model": f"models/{GEMINI_MODEL}",
        "generationConfig": {
            "responseModalities": ["AUDIO"],
        },
    }

    # Field placement differs between docs and the live v1beta schema, so we
    # support multiple layouts (see connect_live, which tries them in order).
    if layout == "setup_level":
        # BidiGenerateContentSetup level (matches the standard Live API schema)
        setup["inputAudioTranscription"] = {}
        setup["outputAudioTranscription"] = {}
        setup["generationConfig"]["translationConfig"] = translation_config
    elif layout == "all_setup_level":
        setup["inputAudioTranscription"] = {}
        setup["outputAudioTranscription"] = {}
        setup["translationConfig"] = translation_config
    elif layout == "generation_config":
        # Layout shown in the live-translate WebSocket docs example
        setup["generationConfig"]["inputAudioTranscription"] = {}
        setup["generationConfig"]["outputAudioTranscription"] = {}
        setup["generationConfig"]["translationConfig"] = translation_config
    else:
        raise ValueError(f"Unknown setup layout: {layout}")

    return {"setup": setup}


class GeminiLiveStream:
    """Gemini Live API WebSocket连接的轻量包装。

    暴露与旧Soniox流相同的接口（send/recv/close），以便音频路由、
    静音填充等组件无需修改即可复用：
    - send(bytes)  -> 包装为 realtimeInput PCM 音频消息
    - send(str)    -> 原样发送（JSON控制消息）
    - finalize()   -> 发送 audioStreamEnd，让服务端尽快吐出剩余转写
    - recv/close   -> 透传
    """

    def __init__(self, ws, sample_rate: int = 16000):
        self._ws = ws
        self._sample_rate = int(sample_rate)
        self._mime_type = f"audio/pcm;rate={self._sample_rate}"

    def send(self, payload) -> None:
        if isinstance(payload, (bytes, bytearray, memoryview)):
            message = {
                "realtimeInput": {
                    "audio": {
                        "data": base64.b64encode(bytes(payload)).decode("ascii"),
                        "mimeType": self._mime_type,
                    }
                }
            }
            self._ws.send(json.dumps(message))
            return
        self._ws.send(payload)

    def finalize(self) -> None:
        """通知服务端音频流暂告一段落，催促其输出剩余转写。"""
        self._ws.send(json.dumps({"realtimeInput": {"audioStreamEnd": True}}))

    def recv(self, timeout: float | None = None):
        return self._ws.recv(timeout=timeout)

    def close(self) -> None:
        self._ws.close()


def connect_live(
    api_key: str,
    translation: str,
    translation_target_lang: str | None = None,
    sample_rate: int = 16000,
) -> GeminiLiveStream:
    """连接Gemini Live API并完成setup握手，返回可用的流包装对象。"""
    if not api_key:
        raise RuntimeError("Gemini API key is missing")

    url = f"{GEMINI_WEBSOCKET_URL}?key={api_key}"

    global _working_setup_layout
    layouts = [_working_setup_layout] if _working_setup_layout else list(SETUP_LAYOUTS)

    last_error: Exception | None = None
    for layout in layouts:
        ws = sync_connect(url, max_size=None)
        try:
            setup_message = get_setup_message(translation, translation_target_lang, layout=layout)
            ws.send(json.dumps(setup_message))

            # Wait for setupComplete before streaming audio.
            message = ws.recv(timeout=SETUP_COMPLETE_TIMEOUT_SECONDS)
            try:
                res = json.loads(message)
            except Exception as error:
                raise RuntimeError(f"Invalid Gemini setup response: {error}")

            if isinstance(res, dict) and "setupComplete" in res:
                if _working_setup_layout != layout:
                    _working_setup_layout = layout
                return GeminiLiveStream(ws, sample_rate=sample_rate)

            if isinstance(res, dict) and res.get("error"):
                error = res["error"]
                raise RuntimeError(
                    f"Gemini setup failed: {error.get('code')} {error.get('message', '')}"
                )

            raise RuntimeError(f"Unexpected Gemini setup response: {str(res)[:200]}")
        except Exception as error:
            try:
                ws.close()
            except Exception:
                pass
            last_error = error
            message_text = str(error)
            # Schema mismatch (server rejects a field name): try the next layout.
            if "Cannot find field" in message_text or "Unknown name" in message_text:
                print(f"⚠️  Gemini setup layout '{layout}' rejected; trying next layout...")
                continue
            raise

    raise RuntimeError(f"Gemini setup failed for all known layouts: {last_error}")


def validate_api_key(api_key: str, timeout_seconds: float = 10.0) -> tuple[bool, str | None]:
    """通过REST端点快速校验API key是否可用。"""
    try:
        response = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key, "pageSize": 1},
            timeout=timeout_seconds,
        )
    except requests.RequestException as error:
        return False, str(error)

    if response.status_code == 200:
        return True, None

    try:
        payload = response.json()
        message = payload.get("error", {}).get("message") or response.text
    except Exception:
        message = response.text
    return False, f"HTTP {response.status_code}: {str(message)[:300]}"
