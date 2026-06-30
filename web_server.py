"""
Web服务器模块 - 处理HTTP和WebSocket连接
"""
import json
import asyncio
import os
import aiohttp
from aiohttp import web
from aiohttp import WSMsgType

import config
from config import (
    get_resource_path,
    LOCK_MANUAL_CONTROLS,
    ENABLE_CHROMA_THEME,
    ENABLE_SPEAKER_DIARIZATION,
    HIDE_SPEAKER_LABELS,
    LLM_REFINE_DEFAULT_MODE,
    TRANSLATION_MODE,
    TRANSLATION_PROVIDER,
    get_capabilities,
    get_language_codes_ordered,
)
from config import (
    is_llm_refine_available,
    LLM_REFINE_CONTEXT_MIN_COUNT,
    LLM_REFINE_CONTEXT_MAX_COUNT,
)
from config import LLM_REFINE_SHOW_DIFF, LLM_REFINE_SHOW_DELETIONS

from audio_capture import list_microphone_devices, normalize_microphone_device_id
from llm_client import close_llm_http_session
import local_store

@web.middleware
async def cache_bypass_middleware(request, handler):
    """Add no-cache headers to all non-WS responses."""
    response = await handler(request)
    if isinstance(response, web.StreamResponse):
        path = str(request.path or "")
        if path.startswith("/kuromoji/"):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
            response.headers.pop('Pragma', None)
            response.headers.pop('Expires', None)
        else:
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
    return response



class WebServer:
    """Web服务器管理器"""
    
    def __init__(self, session, logger):
        self.session = session
        self.logger = logger
        self.websocket_clients = set()
        self.app_runner = None
        self.api_key_error_message = None # 新增属性
        self.window_on_top_callback = None
        self.shutdown_callback = None
        # 原生（PySide6）字幕悬浮窗进程管理器；由 server.py 注入。
        self.overlay_manager = None
        self.overlay_ws = None
        self.ipc_server = None
        # Provider-specific API key getter; injected by server.py.
        self.get_api_key = None
        # Runtime provider/key manager (hot-switch); injected by server.py.
        self.provider_manager = None
        self._ipc_polling_task = None
        # Lazy aiohttp client for proxying REST calls to the subtitle-server.
        self._http = None
        self.use_bundled_cjk_fonts = False

    def set_window_on_top_callback(self, callback):
        self.window_on_top_callback = callback

    def set_shutdown_callback(self, callback):
        self.shutdown_callback = callback
        
    async def _start_ipc_status_polling(self, app):
        self._ipc_polling_task = asyncio.create_task(self._poll_ipc_status())
        
    async def _stop_ipc_status_polling(self, app):
        if self._ipc_polling_task:
            self._ipc_polling_task.cancel()
            try:
                await self._ipc_polling_task
            except asyncio.CancelledError:
                pass
                
    async def _poll_ipc_status(self):
        last_status = None
        while True:
            try:
                connected = False
                if self.ipc_server is not None:
                    connected = len(self.ipc_server._clients) > 0
                
                if last_status != connected:
                    last_status = connected
                    await self.broadcast_to_clients({
                        "type": "ipc_status",
                        "connected": connected
                    })
            except Exception as e:
                self.logger.error(f"Error polling IPC status: {e}")
            await asyncio.sleep(2)

    async def ipc_status_handler(self, request):
        connected = False
        if self.ipc_server is not None:
            connected = len(self.ipc_server._clients) > 0
        return web.json_response({"connected": connected})

    async def api_key_status_handler(self, request):
        """返回API Key状态"""
        status = "ok" if self.api_key_error_message is None else "error"
        return web.json_response({"status": status, "message": self.api_key_error_message})
    
    async def broadcast_to_clients(self, data: dict):
        """向所有连接的客户端广播数据"""
        if self.websocket_clients:
            # 创建消息
            message = json.dumps(data)
            # 向所有客户端发送
            await asyncio.gather(
                *[client.send_str(message) for client in self.websocket_clients],
                return_exceptions=True
            )
    
    async def websocket_handler(self, request):
        """WebSocket处理函数"""
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        
        # 识别是否是悬浮窗客户端
        client_type = request.query.get("client")
        if client_type == "overlay":
            self.overlay_ws = ws
            print("Overlay window client connected via WebSocket.")

        # 添加到客户端列表
        self.websocket_clients.add(ws)
        print(f"Client connected. Total clients: {len(self.websocket_clients)}")
        
        try:
            connected = False
            if self.ipc_server is not None:
                connected = len(self.ipc_server._clients) > 0
            await ws.send_str(json.dumps({"type": "ipc_status", "connected": connected}))
            
            # 发送当前悬浮窗显隐状态给新连入的客户端
            is_visible = False
            if self.overlay_manager is not None:
                is_visible = getattr(self.overlay_manager, "is_visible", False)
            await ws.send_str(json.dumps({"type": "overlay_visibility", "visible": is_visible}))

            # 发送当前识别暂停状态给新连入的客户端
            is_paused = getattr(self.session, "is_paused", False)
            await ws.send_str(json.dumps({"type": "recognition_paused", "paused": is_paused}))

            await ws.send_str(json.dumps({
                "type": "subtitle_font_preference",
                "use_bundled_cjk_fonts": bool(self.use_bundled_cjk_fonts and self.check_custom_font_exists()),
            }))

            manager = self.provider_manager
            if manager is not None:
                if getattr(self.session, "_relay_session_active", False):
                    if manager.mode == "relay":
                        await ws.send_str(json.dumps({"type": "session_connected"}))
                else:
                    last_disconnect = getattr(self.session, "last_disconnect_payload", None)
                    if last_disconnect is not None:
                        await ws.send_str(json.dumps(last_disconnect))
        except Exception as e:
            self.logger.error(f"Failed to send initial IPC status to client: {e}")

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # 处理客户端消息（如果需要）
                    pass
                elif msg.type == WSMsgType.ERROR:
                    print(f'WebSocket connection closed with exception {ws.exception()}')
        except Exception as e:
            print(f"WebSocket error: {e}")
        finally:
            if client_type == "overlay":
                if getattr(self, "overlay_ws", None) == ws:
                    self.overlay_ws = None
                    print("Overlay window client disconnected.")
            # 从客户端列表移除
            self.websocket_clients.discard(ws)
            print(f"Client disconnected. Total clients: {len(self.websocket_clients)}")
        
        return ws
    
    async def health_handler(self, request):
        """健康检查端点 - 用于浏览器定期检测服务器是否存活"""
        return web.json_response({"status": "ok"})

    async def local_store_get_handler(self, request):
        """返回跨实例共享的浏览器设置（localStorage 镜像）。

        每个实例都连自己的后端，但后端读写的是同一个共享文件，因此第二个
        实例（不同端口/origin）也能拿到第一个实例保存的设置与登录信息。
        """
        if not self._is_loopback_request(request):
            return web.json_response({"status": "error", "message": "localhost only"}, status=403)
        return web.json_response({"store": local_store.load()})

    async def local_store_post_handler(self, request):
        """写入共享设置：{set:{k:v}, remove:[k], clear:bool}。"""
        if not self._is_loopback_request(request):
            return web.json_response({"status": "error", "message": "localhost only"}, status=403)
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"status": "error", "message": "Invalid payload"}, status=400)

        if payload.get("clear"):
            store = local_store.clear()
            return web.json_response({"status": "ok", "store": store})

        updates = payload.get("set")
        removals = payload.get("remove")
        if updates is not None and not isinstance(updates, dict):
            return web.json_response({"status": "error", "message": "'set' must be an object"}, status=400)
        if removals is not None and not isinstance(removals, list):
            return web.json_response({"status": "error", "message": "'remove' must be an array"}, status=400)
        store = local_store.merge(updates=updates, removals=removals)
        return web.json_response({"status": "ok", "store": store})

    def _supports_segment_mode(self) -> bool:
        return hasattr(self.session, "get_segment_mode")

    async def ui_config_handler(self, request):
        """前端 UI 配置下发"""
        # Read provider-dependent values dynamically so runtime hot-switches are
        # reflected immediately.
        provider = config.TRANSLATION_PROVIDER
        manager = self.provider_manager
        capabilities = get_capabilities(provider)
        payload = {
            "provider": provider,
            "providers": ["soniox", "gemini"],
            "capabilities": capabilities,
            "languages": get_language_codes_ordered(provider),
            "lock_manual_controls": bool(LOCK_MANUAL_CONTROLS),
            "enable_chroma_theme": bool(ENABLE_CHROMA_THEME),
            "translation_target_lang": self.session.get_translation_target_lang(),
            "llm_refine_available": bool(is_llm_refine_available()),
            "llm_refine_mode": self.session.get_llm_refine_mode(),
            "llm_refine_default_mode": str(LLM_REFINE_DEFAULT_MODE or "off"),
            "llm_refine_context_min_count": int(LLM_REFINE_CONTEXT_MIN_COUNT),
            "llm_refine_context_max_count": int(LLM_REFINE_CONTEXT_MAX_COUNT),
            "llm_refine_show_diff": bool(LLM_REFINE_SHOW_DIFF),
            "llm_refine_show_deletions": bool(LLM_REFINE_SHOW_DELETIONS),
            "speaker_diarization_enabled": bool(config.ENABLE_SPEAKER_DIARIZATION),
            "hide_speaker_labels": bool(HIDE_SPEAKER_LABELS),
            "soniox_region": config.SONIOX_REGION,
            "soniox_custom_url": bool(config.SONIOX_CUSTOM_URL),
            # Subtitle-server relay (hosted mode) availability. The server URL is
            # read only from .env and is never editable in the UI.
            "relay_available": bool(config.RELAY_AVAILABLE),
            "server_url": config.SUBTITLE_SERVER_URL,
            "credits_purchase_url": "",
            "first_redeem_bonus_credits": 0,
            "client_version": config.CLIENT_VERSION,
            "client_latest_version": "",
            "client_minimum_version": "",
            "client_update_url": "",
            "client_update_notes": "",
            "custom_font_available": self.check_custom_font_exists(),
        }

        if config.RELAY_AVAILABLE:
            status, public_settings = await self._server_request(
                "GET", "/public/settings", timeout=5
            )
            if status == 200 and isinstance(public_settings, dict):
                payload["credits_purchase_url"] = str(
                    public_settings.get("credits_purchase_url") or ""
                ).strip()
                try:
                    payload["first_redeem_bonus_credits"] = max(
                        0, float(public_settings.get("first_redeem_bonus_credits") or 0)
                    )
                except (TypeError, ValueError):
                    payload["first_redeem_bonus_credits"] = 0
                payload["client_latest_version"] = str(
                    public_settings.get("client_latest_version") or ""
                ).strip()
                payload["client_minimum_version"] = str(
                    public_settings.get("client_minimum_version") or ""
                ).strip()
                payload["client_update_url"] = str(
                    public_settings.get("client_update_url")
                    or public_settings.get("client_download_url")
                    or ""
                ).strip()
                payload["client_update_notes"] = str(
                    public_settings.get("client_update_notes") or ""
                ).strip()

        if manager is not None:
            payload.update({
                "boot_id": manager.boot_id,
                "setup_required": bool(manager.setup_required),
                "key_source": manager.key_source(),
                "env_key_present": {
                    "soniox": manager.env_key_present("soniox"),
                    "gemini": manager.env_key_present("gemini"),
                },
                "mode": manager.mode,
                "logged_in": bool(manager.relay_token),
                "translation_mode": manager.translation_mode,
                "target_lang_1": manager.target_lang_1,
                "target_lang_2": manager.target_lang_2,
            })
        else:
            payload.update({
                "boot_id": "",
                "setup_required": False,
                "key_source": "env",
                "env_key_present": {"soniox": False, "gemini": False},
                "mode": "direct",
                "logged_in": False,
                "translation_mode": str(getattr(self.session, "translation", None) or TRANSLATION_MODE),
                "target_lang_1": "en",
                "target_lang_2": "zh",
            })

        # Segment mode is a Soniox-only capability.
        if self._supports_segment_mode():
            payload["segment_mode"] = self.session.get_segment_mode()
        return web.json_response(payload)

    @staticmethod
    def _is_loopback_request(request) -> bool:
        """Whether the request originates from localhost (loopback)."""
        remote = str(getattr(request, "remote", "") or "")
        if remote in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"):
            return True
        # aiohttp may report None for in-process/test transports.
        return remote == "" or remote == "None"

    async def setup_handler(self, request):
        """配置/切换 provider + API key（前端设置面板），在进程内热切换。"""
        manager = self.provider_manager
        if manager is None:
            return web.json_response({"status": "error", "message": "Setup unavailable"}, status=503)
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Configuration is locked by server config"},
                status=403,
            )
        if not self._is_loopback_request(request):
            return web.json_response(
                {"status": "error", "message": "Setup is only allowed from localhost"},
                status=403,
            )

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)

        if not isinstance(payload, dict):
            return web.json_response({"status": "error", "message": "Invalid payload"}, status=400)

        provider = str(payload.get("provider") or "").strip().lower()
        if provider not in ("soniox", "gemini"):
            return web.json_response({"status": "error", "message": "Invalid provider"}, status=400)

        # Connection mode: "direct" (own provider key) or "relay" (hosted).
        mode = str(payload.get("mode") or "").strip().lower()
        if mode not in ("direct", "relay"):
            mode = "direct"

        # Soniox regional endpoint (us | eu | jp); ignored for other providers.
        soniox_region = str(payload.get("soniox_region") or "").strip().lower() or None
        if soniox_region is not None and soniox_region not in config.SONIOX_REGION_URLS:
            soniox_region = None

        if mode == "relay":
            if not config.RELAY_AVAILABLE:
                return web.json_response(
                    {"status": "error", "message": "Subtitle server not configured"}, status=400
                )
            # The relay token (ss_ account key) may come from the request or from
            # a prior login already held in memory.
            token = str(payload.get("token") or payload.get("api_key") or "").strip()
            if not token:
                token = manager.relay_token
            if token:
                ok, error, _account, auth_failed = await self._validate_relay_token(token)
                if not ok and auth_failed:
                    # The server actively rejected the token: the user must
                    # sign in again.
                    return web.json_response(
                        {"status": "error", "message": error or "Token validation failed",
                         "setup_required": True, "boot_id": manager.boot_id},
                        status=400,
                    )
                # On a transient validation failure (server unreachable / 5xx)
                # we keep the saved token and proceed, so a brief server outage
                # at startup doesn't log the user out.
            result = await manager.apply_provider(
                provider, mode="relay", relay_token=token, soniox_region=soniox_region
            )
            if not result.get("started") and result.get("error") and not token:
                return web.json_response(
                    {"status": "error", "message": result.get("error"),
                     "boot_id": manager.boot_id, "setup_required": True},
                    status=400,
                )
            return self._setup_response(manager, result)

        # ----- direct mode (user's own provider key) -----
        api_key = payload.get("api_key")
        if api_key is not None:
            api_key = str(api_key).strip()
            if not api_key:
                api_key = None

        # Validate the key (if provided) before activating it.
        if api_key is not None:
            ok, error = self._validate_provider_key(provider, api_key, soniox_region=soniox_region)
            if not ok:
                return web.json_response(
                    {"status": "error", "message": error or "API key validation failed"},
                    status=400,
                )

        result = await manager.apply_provider(
            provider, mode="direct", api_key=api_key, use_env=(api_key is None),
            soniox_region=soniox_region,
        )
        if not result.get("started") and result.get("error") and api_key is None:
            # No key provided and env had none either.
            return web.json_response(
                {"status": "error", "message": result.get("error"), "boot_id": manager.boot_id},
                status=400,
            )

        return self._setup_response(manager, result)

    @staticmethod
    def _setup_response(manager, result):
        return web.json_response({
            "status": "ok",
            "boot_id": manager.boot_id,
            "provider": manager.provider,
            "mode": manager.mode,
            "logged_in": bool(manager.relay_token) if manager.mode == "relay" else None,
            "translation_mode": manager.translation_mode,
            "setup_required": bool(manager.setup_required),
            "downgraded_two_way": bool(result.get("downgraded_two_way")),
            "soniox_region": config.SONIOX_REGION,
            "soniox_custom_url": bool(config.SONIOX_CUSTOM_URL),
        })

    async def use_env_handler(self, request):
        """从环境变量读取 key（清除内存 override 并热切换）。"""
        manager = self.provider_manager
        if manager is None:
            return web.json_response({"status": "error", "message": "Setup unavailable"}, status=503)
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Configuration is locked by server config"},
                status=403,
            )
        if not self._is_loopback_request(request):
            return web.json_response(
                {"status": "error", "message": "Setup is only allowed from localhost"},
                status=403,
            )

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        provider = str((payload or {}).get("provider") or config.TRANSLATION_PROVIDER).strip().lower()
        if provider not in ("soniox", "gemini"):
            return web.json_response({"status": "error", "message": "Invalid provider"}, status=400)

        soniox_region = str((payload or {}).get("soniox_region") or "").strip().lower() or None
        if soniox_region is not None and soniox_region not in config.SONIOX_REGION_URLS:
            soniox_region = None

        result = await manager.apply_provider(provider, use_env=True, soniox_region=soniox_region)
        return web.json_response({
            "status": "ok",
            "boot_id": manager.boot_id,
            "provider": manager.provider,
            "translation_mode": manager.translation_mode,
            "setup_required": bool(manager.setup_required),
            "downgraded_two_way": bool(result.get("downgraded_two_way")),
            "soniox_region": config.SONIOX_REGION,
            "soniox_custom_url": bool(config.SONIOX_CUSTOM_URL),
        })

    @staticmethod
    def _validate_provider_key(
        provider: str, api_key: str, *, soniox_region: str | None = None
    ) -> tuple[bool, str | None]:
        try:
            if provider == "gemini":
                from gemini_key_setup import validate_gemini_api_key
                return validate_gemini_api_key(api_key)
            from soniox_key_setup import validate_soniox_api_key
            # Validate against the selected regional endpoint when provided.
            websocket_url = config.SONIOX_REGION_URLS.get(soniox_region) if soniox_region else None
            return validate_soniox_api_key(api_key, websocket_url)
        except Exception as error:
            return False, str(error)

    # ===================== Subtitle-server relay (hosted) =====================

    async def _get_http_session(self):
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    async def _server_request(self, method, path, *, json_body=None, token=None, timeout=15):
        """Proxy a REST call to the configured subtitle-server.

        Returns (status, data). FastAPI errors arrive as {"detail": ...}.
        """
        if not config.RELAY_AVAILABLE:
            return 503, {"detail": "Subtitle server not configured"}
        url = config.relay_rest_url(path)
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            session = await self._get_http_session()
            async with session.request(
                method, url, json=json_body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    data = {"detail": (await resp.text())[:500]}
                return resp.status, data
        except Exception as e:
            return 502, {"detail": str(e)}

    async def _validate_relay_token(self, token):
        """Validate an ss_ account token via GET /me.

        Returns (ok, error, account, auth_failed). ``auth_failed`` is True only
        when the server actively rejects the token (401/403) — i.e. the user
        really needs to sign in again. A transient failure (server unreachable
        or 5xx) returns auth_failed=False so callers can keep the saved token
        instead of forcing a needless re-login.
        """
        if not token:
            return False, "Missing token", None, True
        status, data = await self._server_request("GET", "/me", token=token)
        if status == 200 and isinstance(data, dict):
            return True, None, data, False
        if status in (401, 403):
            return False, "Invalid or unauthorized token", None, True
        msg = data.get("detail") if isinstance(data, dict) else None
        return False, msg or f"Server error {status}", None, False

    def _relay_token(self):
        manager = self.provider_manager
        return (manager.relay_token if manager else "") or ""

    async def account_login_code_handler(self, request):
        """Redeem a one-time login code generated on the user web page. On
        success, remember the token so /account/* works immediately."""
        if not self._is_loopback_request(request):
            return web.json_response({"status": "error", "message": "localhost only"}, status=403)
        if not config.RELAY_AVAILABLE:
            return web.json_response({"status": "error", "message": "Subtitle server not configured"}, status=503)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        code = str((payload or {}).get("code") or "").strip()
        if not code:
            return web.json_response({"status": "error", "message": "Missing code"}, status=400)
        status, data = await self._server_request("POST", "/auth/login-code", json_body={"code": code})
        if (
            status == 200 and isinstance(data, dict)
            and data.get("success") and data.get("api_key")
            and self.provider_manager is not None
        ):
            self.provider_manager.relay_token = str(data["api_key"]).strip()
            config.set_relay_token(self.provider_manager.relay_token)
        return web.json_response(data, status=status)

    async def account_registration_info_handler(self, request):
        if not config.RELAY_AVAILABLE:
            return web.json_response({"status": "error", "message": "Subtitle server not configured"}, status=503)
        status, data = await self._server_request("GET", "/auth/registration-info")
        return web.json_response(data, status=status)

    async def account_status_handler(self, request):
        manager = self.provider_manager
        token = self._relay_token()
        payload = {
            "relay_available": bool(config.RELAY_AVAILABLE),
            "server_url": config.SUBTITLE_SERVER_URL,
            "mode": manager.mode if manager else "direct",
            "logged_in": bool(token),
            "display_name": None,
            "trust_rank": None,
            "first_redeem_bonus_credits": 0,
            "first_redeem_bonus_eligible": False,
        }
        if token and config.RELAY_AVAILABLE:
            status, data = await self._server_request("GET", "/me", token=token)
            if status == 200 and isinstance(data, dict):
                payload["display_name"] = data.get("display_name")
                payload["trust_rank"] = data.get("trust_rank")
                try:
                    payload["first_redeem_bonus_credits"] = max(
                        0, float(data.get("first_redeem_bonus_credits") or 0)
                    )
                except (TypeError, ValueError):
                    payload["first_redeem_bonus_credits"] = 0
                payload["first_redeem_bonus_eligible"] = bool(
                    data.get("first_redeem_bonus_eligible")
                    and payload["first_redeem_bonus_credits"] > 0
                )
            elif status in (401, 403):
                payload["logged_in"] = False
        return web.json_response(payload)

    @staticmethod
    def _active_relay_model(provider):
        return (
            "models/gemini-3.5-live-translate-preview"
            if provider == "gemini" else "stt-rt-v5"
        )

    async def account_balance_handler(self, request):
        if not config.RELAY_AVAILABLE:
            return web.json_response({"status": "error", "message": "Subtitle server not configured"}, status=503)
        token = self._relay_token()
        if not token:
            return web.json_response({"status": "error", "message": "Not signed in"}, status=401)

        provider = request.query.get("provider", config.TRANSLATION_PROVIDER)
        if provider not in ("soniox", "gemini"):
            provider = config.TRANSLATION_PROVIDER
        model = self._active_relay_model(provider)

        # /billing/summary gives prepaid balance + subscription used/remaining +
        # free used-today/remaining in one call.
        s_sum, summary = await self._server_request("GET", "/billing/summary", token=token)
        if s_sum != 200:
            return web.json_response(summary, status=s_sum)

        prepaid = summary.get("prepaid_balance")
        subscriptions = []
        price_per_second = 1.0
        free = None
        for api in summary.get("apis", []):
            if api.get("name") == provider or api.get("provider") == provider:
                if api.get("prepaid_balance") is not None:
                    prepaid = api.get("prepaid_balance")
                subscriptions = api.get("subscriptions") or []
                for m in api.get("models", []):
                    if m.get("model_name") == model:
                        price_per_second = m.get("price_per_second", 1.0)
                        free = m.get("free")  # None unless the model offers free quota
                        break
                break

        return web.json_response({
            "provider": provider,
            "model": model,
            "prepaid_balance": prepaid,
            "subscriptions": subscriptions,
            "price_per_second": float(price_per_second),
            "free": free,
            "first_redeem_bonus_credits": summary.get("first_redeem_bonus_credits") or 0,
            "first_redeem_bonus_eligible": bool(summary.get("first_redeem_bonus_eligible")),
        })

    async def account_pricing_handler(self, request):
        """Per-provider unit price of the active relay model, for the Settings UI.

        Uses the public /billing/policies endpoint (no token needed) so the
        Settings panel can show each provider's price before a session starts.
        """
        if not config.RELAY_AVAILABLE:
            return web.json_response({"status": "error", "message": "Subtitle server not configured"}, status=503)
        status, pol = await self._server_request("GET", "/billing/policies")
        pricing = {}
        if status == 200 and isinstance(pol, dict):
            for provider in ("soniox", "gemini"):
                model = self._active_relay_model(provider)
                for p in pol.get("policies", []):
                    if p.get("name") == provider or p.get("provider") == provider:
                        for m in p.get("models", []):
                            if m.get("model_name") == model:
                                pps = float(m.get("price_rate", 1.0)) * float(m.get("price_multiplier", 1.0))
                                entry = {"model": model, "price_per_second": pps}
                                # Free pools (daily/weekly/monthly) the model offers,
                                # so the Settings UI can show each configured quota.
                                if m.get("free_pools"):
                                    entry["free_pools"] = m["free_pools"]
                                pricing[provider] = entry
                                break
                        break
        return web.json_response({"pricing": pricing})

    async def account_usage_handler(self, request):
        if not config.RELAY_AVAILABLE:
            return web.json_response({"status": "error", "message": "Subtitle server not configured"}, status=503)
        token = self._relay_token()
        if not token:
            return web.json_response({"status": "error", "message": "Not signed in"}, status=401)
        limit = request.query.get("limit", "50")
        status, data = await self._server_request("GET", f"/me/usage?limit={limit}", token=token)
        return web.json_response(data, status=status)

    async def account_invite_handler(self, request):
        if not config.RELAY_AVAILABLE:
            return web.json_response({"status": "error", "message": "Subtitle server not configured"}, status=503)
        token = self._relay_token()
        if not token:
            return web.json_response({"status": "error", "message": "Not signed in"}, status=401)
        status, data = await self._server_request("GET", "/me/invite", token=token)
        return web.json_response(data, status=status)

    async def account_web_login_url_handler(self, request):
        if not self._is_loopback_request(request):
            return web.json_response({"status": "error", "message": "localhost only"}, status=403)
        if not config.RELAY_AVAILABLE:
            return web.json_response({"status": "error", "message": "Subtitle server not configured"}, status=503)
        token = self._relay_token()
        if not token:
            return web.json_response({"status": "error", "message": "Not signed in"}, status=401)
        status, data = await self._server_request("POST", "/me/web-login-code", token=token)
        if status != 200 or not isinstance(data, dict) or not data.get("web_login_code"):
            return web.json_response(data, status=status)
        base = config.relay_rest_url("/app/")
        from urllib.parse import quote
        url = f"{base}#/login?web_login_code={quote(str(data['web_login_code']))}"
        return web.json_response({"url": url, "expires_at": data.get("expires_at")})

    async def account_redeem_handler(self, request):
        if not self._is_loopback_request(request):
            return web.json_response({"status": "error", "message": "localhost only"}, status=403)
        if not config.RELAY_AVAILABLE:
            return web.json_response({"status": "error", "message": "Subtitle server not configured"}, status=503)
        token = self._relay_token()
        if not token:
            return web.json_response({"status": "error", "message": "Not signed in"}, status=401)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        code = str((payload or {}).get("code") or "").strip()
        if not code:
            return web.json_response({"status": "error", "message": "Missing code"}, status=400)
        status, data = await self._server_request(
            "POST", "/redeem", json_body={"code": code}, token=token
        )
        return web.json_response(data, status=status)

    async def account_logout_handler(self, request):
        if not self._is_loopback_request(request):
            return web.json_response({"status": "error", "message": "localhost only"}, status=403)
        manager = self.provider_manager
        if manager is None:
            return web.json_response({"status": "error", "message": "Setup unavailable"}, status=503)
        # Clear the token and stop any running relay session.
        await manager.apply_provider(manager.provider, mode="relay", relay_token="")
        return web.json_response({"status": "ok", "boot_id": manager.boot_id})

    async def segment_mode_get_handler(self, request):
        """获取当前断句模式（仅支持断句的 provider）"""
        if not self._supports_segment_mode():
            return web.json_response({"status": "error", "message": "Segment mode not supported"}, status=404)
        return web.json_response({"mode": self.session.get_segment_mode()})

    async def segment_mode_set_handler(self, request):
        """设置断句模式（会广播给所有前端）"""
        if not self._supports_segment_mode():
            return web.json_response({"status": "error", "message": "Segment mode not supported"}, status=404)
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Segment mode switching is disabled"},
                status=403,
            )

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)

        mode = payload.get("mode") if isinstance(payload, dict) else None
        ok, message = self.session.set_segment_mode(mode)
        if not ok:
            return web.json_response({"status": "error", "message": message}, status=400)
        return web.json_response({"status": "ok", "mode": mode})

    async def llm_refine_get_handler(self, request):
        """获取 LLM 改进开关状态"""
        mode = self.session.get_llm_refine_mode()
        return web.json_response({
            "enabled": self.session.get_llm_refine_enabled(),
            "mode": mode,
            "available": bool(is_llm_refine_available()),
        })

    async def llm_refine_set_handler(self, request):
        """设置 LLM 改进开关"""
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "LLM refine toggle is disabled by server config"},
                status=403,
            )

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON"}, status=400)

        mode = None
        if isinstance(payload, dict):
            if "mode" in payload:
                mode = payload.get("mode")
            elif "enabled" in payload:
                enabled = bool(payload.get("enabled"))
                mode = "refine" if enabled else "off"

        if not mode:
            return web.json_response({"status": "error", "message": "Missing 'mode' field"}, status=400)

        ok, message = self.session.set_llm_refine_mode(str(mode))
        if not ok:
            return web.json_response({"status": "error", "message": message}, status=400)

        return web.json_response({
            "status": "ok",
            "mode": self.session.get_llm_refine_mode(),
            "enabled": self.session.get_llm_refine_enabled(),
        })

    async def restart_handler(self, request):
        """重启识别端点（也用于运行时切换翻译模式 / 双向语言对）"""
        is_auto = False
        requested_target_lang = None
        requested_mode = None
        requested_lang_1 = None
        requested_lang_2 = None
        try:
            payload = await request.json()
            if isinstance(payload, dict):
                is_auto = bool(payload.get("auto"))
                if payload.get("target_lang") is not None:
                    requested_target_lang = payload.get("target_lang")
                if payload.get("translation_mode") is not None:
                    requested_mode = str(payload.get("translation_mode")).strip().lower()
                if payload.get("target_lang_1") is not None:
                    requested_lang_1 = payload.get("target_lang_1")
                if payload.get("target_lang_2") is not None:
                    requested_lang_2 = payload.get("target_lang_2")
        except Exception:
            # 兼容旧客户端：无 body 时视为手动
            is_auto = False

        if LOCK_MANUAL_CONTROLS and not is_auto:
            return web.json_response(
                {"status": "error", "message": "Manual restart is disabled by server config"},
                status=403
            )

        if requested_mode is not None and requested_mode not in ("none", "one_way", "two_way"):
            return web.json_response(
                {"status": "error", "message": f"Invalid translation_mode: {requested_mode}"},
                status=400,
            )

        # Two-way is Soniox-only.
        if requested_mode == "two_way" and config.TRANSLATION_PROVIDER == "gemini":
            return web.json_response(
                {"status": "error", "message": "Gemini does not support two-way translation"},
                status=400,
            )

        get_api_key = self.get_api_key

        print(f"\n[Server] Received {'auto ' if is_auto else ''}restart request...")

        if requested_target_lang is not None:
            ok, message = self.session.set_translation_target_lang(requested_target_lang)
            if not ok:
                return web.json_response({"status": "error", "message": message}, status=400)

        if requested_mode == "two_way":
            lang_a = requested_lang_1 if requested_lang_1 is not None else self.session.get_target_langs()[0]
            lang_b = requested_lang_2 if requested_lang_2 is not None else self.session.get_target_langs()[1]
            if hasattr(self.session, "set_target_langs"):
                ok, message = self.session.set_target_langs(lang_a, lang_b)
                if not ok:
                    return web.json_response({"status": "error", "message": message}, status=400)

        # Determine the translation mode to (re)start with.
        if requested_mode is not None:
            translation_mode = requested_mode
        elif self.provider_manager is not None:
            translation_mode = self.provider_manager.translation_mode
        else:
            translation_mode = str(getattr(self.session, "translation", None) or TRANSLATION_MODE)

        # Keep the manager state in sync so subsequent hot-switches preserve it.
        if self.provider_manager is not None:
            self.provider_manager.translation_mode = translation_mode
            self.provider_manager.target_lang = self.session.get_translation_target_lang()
            if hasattr(self.session, "get_target_langs"):
                l1, l2 = self.session.get_target_langs()
                self.provider_manager.target_lang_1 = l1
                self.provider_manager.target_lang_2 = l2

        # 先停止当前的Soniox会话
        self.session.stop()
        
        # 关闭当前日志文件
        self.logger.close_log_file()
        
        if is_auto:
            await self.broadcast_to_clients({
                "type": "clear",
                "message": "Recognition reconnecting...",
                "preserve_existing": True,
            })
        else:
            # 向所有客户端发送清空指令
            await self.broadcast_to_clients({
                "type": "clear",
                "message": "Recognition restarting..."
            })

            # 给客户端一点时间处理clear消息
            await asyncio.sleep(0.3)

            # 关闭所有现有的WebSocket连接
            print(f"[Server] Closing {len(self.websocket_clients)} WebSocket connections...")
            clients_to_close = list(self.websocket_clients)
            for client in clients_to_close:
                try:
                    await client.close()
                except Exception as e:
                    print(f"[Server] Error closing client connection: {e}")
            self.websocket_clients.clear()
        
        # 启动新的Soniox会话
        try:
            print("[Server] Starting new recognition session...")
            api_key = get_api_key()
            audio_format = "pcm_s16le"

            loop = asyncio.get_event_loop()
            self.session.start(
                api_key,
                audio_format,
                translation_mode,
                loop,
                translation_target_lang=self.session.get_translation_target_lang(),
            )

            # Translation may have been toggled on/off; keep IPC in sync.
            if self.provider_manager is not None:
                try:
                    await self.provider_manager._sync_ipc(True)
                except Exception:
                    pass

            print("[Server] New session started successfully")
            return web.json_response({"status": "ok", "message": "Recognition restarted", "paused": False})
        except Exception as e:
            print(f"[Server] Failed to restart: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def osc_translation_get_handler(self, request):
        """查询翻译结果 OSC 发送开关状态"""
        enabled = self.session.get_osc_translation_enabled()
        return web.json_response({"enabled": enabled})

    async def osc_translation_set_handler(self, request):
        """设置翻译结果 OSC 发送开关"""
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "OSC translation toggle is disabled by server config"},
                status=403
            )

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON payload"}, status=400)

        enabled = bool(payload.get("enabled")) if isinstance(payload, dict) else False
        self.session.set_osc_translation_enabled(enabled)
        return web.json_response({"enabled": self.session.get_osc_translation_enabled()})

    async def pause_handler(self, request):
        """暂停识别端点"""
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Pause is disabled by server config"},
                status=403
            )

        print("\n[Server] Received pause request...")
        paused = self.session.pause()

        # 广播暂停状态给所有 WebSocket 客户端
        await self.broadcast_to_clients({
            "type": "recognition_paused",
            "paused": True
        })

        if paused:
            message = "Recognition paused"
        else:
            message = "Recognition already paused"

        return web.json_response({"status": "ok", "message": message})
    
    async def resume_handler(self, request):
        """恢复识别端点"""
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Resume is disabled by server config"},
                status=403
            )

        print("\n[Server] Received resume request...")
        get_api_key = self.get_api_key

        if not self.session.is_paused:
            # 即使已经运行，也确保状态同步
            await self.broadcast_to_clients({
                "type": "recognition_paused",
                "paused": False
            })
            return web.json_response({"status": "ok", "message": "Recognition already running"})

        try:
            api_key = get_api_key()
        except RuntimeError as error:
            print(f"[Server] Resume failed: {error}")
            return web.json_response({"status": "error", "message": str(error)}, status=500)

        loop = asyncio.get_event_loop()
        resumed = self.session.resume(
            api_key=api_key,
            audio_format="pcm_s16le",
            translation=TRANSLATION_MODE,
            loop=loop
        )

        # 广播恢复状态给所有 WebSocket 客户端
        await self.broadcast_to_clients({
            "type": "recognition_paused",
            "paused": False
        })

        if resumed:
            return web.json_response({"status": "ok", "message": "Recognition resumed"})

        # resume 请求失败但仍处于暂停状态，返回错误
        return web.json_response({"status": "error", "message": "Failed to resume recognition"}, status=500)

    async def get_audio_source_handler(self, request):
        """获取当前音频源"""
        source = self.session.get_audio_source()
        return web.json_response({"status": "ok", "source": source})

    async def set_audio_source_handler(self, request):
        """切换音频源"""
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Audio source switching is disabled by server config"},
                status=403
            )

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON payload"}, status=400)

        if not isinstance(payload, dict) or "source" not in payload:
            return web.json_response({"status": "error", "message": "Missing 'source' field"}, status=400)

        source = payload.get("source")
        if not isinstance(source, str):
            return web.json_response({"status": "error", "message": "'source' must be a string"}, status=400)

        source = source.strip().lower()
        if source not in ("system", "microphone", "mix"):
            return web.json_response({"status": "error", "message": "Invalid audio source"}, status=400)

        success, message = self.session.set_audio_source(source)
        status_code = 200 if success else 400
        response = {
            "status": "ok" if success else "error",
            "message": message,
            "source": self.session.get_audio_source()
        }
        return web.json_response(response, status=status_code)

    def _microphone_payload(self) -> dict:
        data = list_microphone_devices()
        selected_id = ""
        if hasattr(self.session, "get_microphone_device_id"):
            selected_id = normalize_microphone_device_id(self.session.get_microphone_device_id())
        data["selected_id"] = selected_id
        return data

    async def microphones_handler(self, request):
        """List available microphone devices for the settings UI."""
        payload = self._microphone_payload()
        payload["status"] = "ok"
        return web.json_response(payload)

    async def microphone_device_get_handler(self, request):
        """Get the selected microphone device."""
        payload = self._microphone_payload()
        payload["status"] = "ok"
        return web.json_response(payload)

    async def microphone_device_set_handler(self, request):
        """Set the selected microphone device."""
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Microphone device switching is disabled by server config"},
                status=403,
            )

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON payload"}, status=400)

        if not isinstance(payload, dict):
            return web.json_response({"status": "error", "message": "Invalid JSON payload"}, status=400)

        device_id = normalize_microphone_device_id(payload.get("id"))
        if device_id:
            devices = self._microphone_payload().get("devices") or []
            known_ids = {str(device.get("id") or "") for device in devices if isinstance(device, dict)}
            if known_ids and device_id not in known_ids:
                return web.json_response({"status": "error", "message": "Unknown microphone device"}, status=400)

        if not hasattr(self.session, "set_microphone_device_id"):
            return web.json_response(
                {"status": "error", "message": "Microphone device switching is unavailable"},
                status=503,
            )

        success, message = self.session.set_microphone_device_id(device_id)
        status_code = 200 if success else 400
        return web.json_response({
            "status": "ok" if success else "error",
            "message": message,
            "id": self.session.get_microphone_device_id(),
        }, status=status_code)

    async def window_on_top_handler(self, request):
        """切换窗口始终置顶状态（仅 WebView 模式有效）"""
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON payload"}, status=400)

        if not isinstance(payload, dict) or "on_top" not in payload:
            return web.json_response({"status": "error", "message": "Missing 'on_top' field"}, status=400)

        on_top = bool(payload.get("on_top"))

        if not callable(self.window_on_top_callback):
            return web.json_response({"status": "ignored", "message": "Window on-top control unavailable"})

        try:
            applied = bool(self.window_on_top_callback(on_top))
            return web.json_response({"status": "ok", "on_top": on_top, "applied": applied})
        except Exception as error:
            return web.json_response({"status": "error", "message": str(error)}, status=500)

    def get_custom_font_path(self) -> str | None:
        import sys
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            return os.path.join(exe_dir, "NotoSansCJK-Regular.ttc")
        else:
            return os.path.join(os.getcwd(), "NotoSansCJK-Regular.ttc")

    def check_custom_font_exists(self) -> bool:
        font_path = self.get_custom_font_path()
        return font_path is not None and os.path.exists(font_path)

    async def custom_font_file_handler(self, request):
        """Serve the custom font file if it exists, fallback to static version, or return 404."""
        font_path = self.get_custom_font_path()
        if font_path and os.path.exists(font_path):
            return web.FileResponse(font_path)
        
        # Fallback to static folder version
        static_font_path = get_resource_path(os.path.join('static', 'fonts', 'NotoSansCJK-Regular.ttc'))
        if os.path.exists(static_font_path):
            return web.FileResponse(static_font_path)
        
        return web.HTTPNotFound()

    async def subtitle_font_get_handler(self, request):
        """Return the current subtitle font preference for native overlay clients."""
        return web.json_response({
            "use_bundled_cjk_fonts": bool(self.use_bundled_cjk_fonts and self.check_custom_font_exists()),
        })

    async def subtitle_font_post_handler(self, request):
        """Update subtitle font preference and broadcast it to connected windows."""
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON payload"}, status=400)

        if not isinstance(payload, dict):
            return web.json_response({"status": "error", "message": "Invalid JSON payload"}, status=400)

        enabled = bool(payload.get("use_bundled_cjk_fonts"))
        if enabled and not self.check_custom_font_exists():
            enabled = False

        self.use_bundled_cjk_fonts = enabled
        await self.broadcast_to_clients({
            "type": "subtitle_font_preference",
            "use_bundled_cjk_fonts": enabled,
        })
        return web.json_response({"status": "ok", "use_bundled_cjk_fonts": enabled})

    async def overlay_get_handler(self, request):
        """查询原生字幕悬浮窗当前是否打开。"""
        manager = self.overlay_manager
        if manager is None:
            return web.json_response({"status": "ok", "available": False, "open": False})
        return web.json_response({
            "status": "ok",
            "available": True,
            "open": bool(manager.is_open() and manager.is_visible),
        })

    async def overlay_post_handler(self, request):
        """打开/关闭/切换原生字幕悬浮窗。

        请求体：{"action": "toggle" | "open" | "close"}（缺省为 toggle）。
        """
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Overlay control is disabled by server config"},
                status=403,
            )

        manager = self.overlay_manager
        if manager is None:
            return web.json_response(
                {"status": "ignored", "available": False, "message": "Overlay unavailable"}
            )

        try:
            payload = await request.json()
        except Exception:
            payload = {}
        action = (payload.get("action") if isinstance(payload, dict) else None) or "toggle"

        try:
            if action == "open":
                is_visible = True
            elif action == "close":
                is_visible = False
            else:  # toggle
                is_visible = not getattr(manager, "is_visible", False)

            manager.is_visible = is_visible

            # Ensure the process is alive (spawn it if it died or hasn't started)
            if not manager.is_open():
                manager.open(hidden=True)
            
            # Broadcast new visibility state to all websocket clients
            await self.broadcast_to_clients({"type": "overlay_visibility", "visible": is_visible})

            return web.json_response({"status": "ok", "available": True, "open": bool(is_visible)})
        except Exception as error:
            self.logger.error(f"Failed to handle overlay request: {error}")
            return web.json_response({"status": "error", "message": str(error)}, status=500)

    async def shutdown_handler(self, request):
        """请求退出应用（前端“重置所有设置并退出”调用）。"""
        if not callable(self.shutdown_callback):
            return web.json_response({"status": "ignored", "message": "Shutdown unavailable"})
        # 先返回响应，再延迟退出，避免连接被强制中断导致前端报错。
        asyncio.get_event_loop().call_later(0.3, self.shutdown_callback)
        return web.json_response({"status": "ok"})

    async def index_handler(self, request):
        """静态文件处理"""
        index_path = get_resource_path(os.path.join('static', 'index.html'))
        with open(index_path, 'r', encoding='utf-8') as f:
            return web.Response(
                text=f.read(),
                content_type='text/html',
                headers={
                    "Cache-Control": "no-store, no-cache, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
    
    def create_app(self):
        """创建aiohttp应用"""
        app = web.Application(middlewares=[cache_bypass_middleware])

        app.on_startup.append(self._start_ipc_status_polling)
        app.on_cleanup.append(self._stop_ipc_status_polling)

        async def _cleanup_llm_session(app_instance):
            try:
                await close_llm_http_session()
            except Exception:
                # Best-effort cleanup.
                pass

        app.on_cleanup.append(_cleanup_llm_session)

        async def _cleanup_http_session(app_instance):
            try:
                if self._http is not None and not self._http.closed:
                    await self._http.close()
            except Exception:
                pass

        app.on_cleanup.append(_cleanup_http_session)

        # 路由设置
        app.router.add_get('/', self.index_handler)
        app.router.add_get('/ws', self.websocket_handler)
        app.router.add_get('/health', self.health_handler)
        app.router.add_get('/local-store', self.local_store_get_handler)
        app.router.add_post('/local-store', self.local_store_post_handler)
        app.router.add_get('/ui-config', self.ui_config_handler)
        app.router.add_get('/api/ipc_status', self.ipc_status_handler)
        app.router.add_get('/segment-mode', self.segment_mode_get_handler)
        app.router.add_post('/segment-mode', self.segment_mode_set_handler)
        app.router.add_get('/llm-refine', self.llm_refine_get_handler)
        app.router.add_post('/llm-refine', self.llm_refine_set_handler)
        app.router.add_get('/api-key-status', self.api_key_status_handler) # 新增路由
        app.router.add_post('/setup', self.setup_handler)
        app.router.add_post('/use-env', self.use_env_handler)
        # Subtitle-server relay (hosted mode) account endpoints.
        app.router.add_post('/account/login-code', self.account_login_code_handler)
        app.router.add_get('/account/registration-info', self.account_registration_info_handler)
        app.router.add_get('/account/status', self.account_status_handler)
        app.router.add_get('/account/balance', self.account_balance_handler)
        app.router.add_get('/account/pricing', self.account_pricing_handler)
        app.router.add_get('/account/usage', self.account_usage_handler)
        app.router.add_get('/account/invite', self.account_invite_handler)
        app.router.add_get('/account/web-login-url', self.account_web_login_url_handler)
        app.router.add_post('/account/redeem', self.account_redeem_handler)
        app.router.add_post('/account/logout', self.account_logout_handler)
        app.router.add_post('/restart', self.restart_handler)
        app.router.add_post('/pause', self.pause_handler)
        app.router.add_post('/resume', self.resume_handler)
        app.router.add_get('/osc-translation', self.osc_translation_get_handler)
        app.router.add_post('/osc-translation', self.osc_translation_set_handler)
        app.router.add_get('/audio-source', self.get_audio_source_handler)
        app.router.add_post('/audio-source', self.set_audio_source_handler)
        app.router.add_get('/microphones', self.microphones_handler)
        app.router.add_get('/microphone-device', self.microphone_device_get_handler)
        app.router.add_post('/microphone-device', self.microphone_device_set_handler)
        app.router.add_get('/subtitle-font', self.subtitle_font_get_handler)
        app.router.add_post('/subtitle-font', self.subtitle_font_post_handler)
        app.router.add_post('/window-on-top', self.window_on_top_handler)
        app.router.add_get('/overlay', self.overlay_get_handler)
        app.router.add_post('/overlay', self.overlay_post_handler)
        app.router.add_post('/shutdown', self.shutdown_handler)
        app.router.add_get('/fonts/NotoSansCJK-Regular.ttc', self.custom_font_file_handler)
        
        # 静态文件服务 - 放在最后以避免覆盖API路由
        # 将 static 目录下的文件映射到根路径
        app.router.add_static('/', path=get_resource_path('static'), name='static')
        
        return app
