"""
Web服务器模块 - 处理HTTP和WebSocket连接
"""
import json
import asyncio
import os
from aiohttp import web
from aiohttp import WSMsgType

from config import (
    get_resource_path,
    LOCK_MANUAL_CONTROLS,
    ENABLE_SPEAKER_DIARIZATION,
    HIDE_SPEAKER_LABELS,
    LLM_REFINE_DEFAULT_MODE,
    TRANSLATION_MODE,
)
from config import (
    is_llm_refine_available,
    LLM_REFINE_CONTEXT_MIN_COUNT,
    LLM_REFINE_CONTEXT_MAX_COUNT,
)
from config import LLM_REFINE_SHOW_DIFF, LLM_REFINE_SHOW_DELETIONS

from llm_client import close_llm_http_session

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
    
    def __init__(self, soniox_session, logger):
        self.soniox_session = soniox_session
        self.logger = logger
        self.websocket_clients = set()
        self.app_runner = None
        self.api_key_error_message = None # 新增属性

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
        
        # 添加到客户端列表
        self.websocket_clients.add(ws)
        print(f"Client connected. Total clients: {len(self.websocket_clients)}")
        
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
            # 从客户端列表移除
            self.websocket_clients.discard(ws)
            print(f"Client disconnected. Total clients: {len(self.websocket_clients)}")
        
        return ws
    
    async def health_handler(self, request):
        """健康检查端点 - 用于浏览器定期检测服务器是否存活"""
        return web.json_response({"status": "ok"})

    async def ui_config_handler(self, request):
        """前端 UI 配置下发"""
        return web.json_response({
            "lock_manual_controls": bool(LOCK_MANUAL_CONTROLS),
            "translation_target_lang": self.soniox_session.get_translation_target_lang(),
            "llm_refine_available": bool(is_llm_refine_available()),
            "llm_refine_mode": self.soniox_session.get_llm_refine_mode(),
            "llm_refine_default_mode": str(LLM_REFINE_DEFAULT_MODE or "off"),
            "llm_refine_context_min_count": int(LLM_REFINE_CONTEXT_MIN_COUNT),
            "llm_refine_context_max_count": int(LLM_REFINE_CONTEXT_MAX_COUNT),
            "llm_refine_show_diff": bool(LLM_REFINE_SHOW_DIFF),
            "llm_refine_show_deletions": bool(LLM_REFINE_SHOW_DELETIONS),
            "segment_mode": self.soniox_session.get_segment_mode(),
            "speaker_diarization_enabled": bool(ENABLE_SPEAKER_DIARIZATION),
            "hide_speaker_labels": bool(HIDE_SPEAKER_LABELS),
        })

    async def segment_mode_get_handler(self, request):
        """获取当前断句模式"""
        return web.json_response({"mode": self.soniox_session.get_segment_mode()})

    async def segment_mode_set_handler(self, request):
        """设置断句模式（会广播给所有前端）"""
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
        ok, message = self.soniox_session.set_segment_mode(mode)
        if not ok:
            return web.json_response({"status": "error", "message": message}, status=400)
        return web.json_response({"status": "ok", "mode": mode})

    async def llm_refine_get_handler(self, request):
        """获取 LLM 改进开关状态"""
        mode = self.soniox_session.get_llm_refine_mode()
        return web.json_response({
            "enabled": self.soniox_session.get_llm_refine_enabled(),
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

        ok, message = self.soniox_session.set_llm_refine_mode(str(mode))
        if not ok:
            return web.json_response({"status": "error", "message": message}, status=400)

        return web.json_response({
            "status": "ok",
            "mode": self.soniox_session.get_llm_refine_mode(),
            "enabled": self.soniox_session.get_llm_refine_enabled(),
        })

    async def restart_handler(self, request):
        """重启识别端点"""
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Manual restart is disabled by server config"},
                status=403
            )

        from soniox_client import get_api_key

        is_auto = False
        requested_target_lang = None
        try:
            payload = await request.json()
            if isinstance(payload, dict):
                is_auto = bool(payload.get("auto"))
                if payload.get("target_lang") is not None:
                    requested_target_lang = payload.get("target_lang")
        except Exception:
            # 兼容旧客户端：无 body 时视为手动
            is_auto = False
        
        print("\n[Server] Received restart request...")

        if requested_target_lang is not None:
            ok, message = self.soniox_session.set_translation_target_lang(requested_target_lang)
            if not ok:
                return web.json_response({"status": "error", "message": message}, status=400)
        
        # 先停止当前的Soniox会话
        self.soniox_session.stop()
        
        # 关闭当前日志文件
        self.logger.close_log_file()
        
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
            translation = TRANSLATION_MODE
            
            loop = asyncio.get_event_loop()
            self.soniox_session.start(
                api_key,
                audio_format,
                translation,
                loop,
                translation_target_lang=self.soniox_session.get_translation_target_lang(),
            )
            
            print("[Server] New session started successfully")
            return web.json_response({"status": "ok", "message": "Recognition restarted"})
        except Exception as e:
            print(f"[Server] Failed to restart: {e}")
            return web.json_response({"status": "error", "message": str(e)}, status=500)

    async def osc_translation_get_handler(self, request):
        """查询翻译结果 OSC 发送开关状态"""
        enabled = self.soniox_session.get_osc_translation_enabled()
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
        self.soniox_session.set_osc_translation_enabled(enabled)
        return web.json_response({"enabled": self.soniox_session.get_osc_translation_enabled()})

    async def pause_handler(self, request):
        """暂停识别端点"""
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Pause is disabled by server config"},
                status=403
            )

        print("\n[Server] Received pause request...")
        paused = self.soniox_session.pause()

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
        from soniox_client import get_api_key

        if not self.soniox_session.is_paused:
            return web.json_response({"status": "ok", "message": "Recognition already running"})

        try:
            api_key = get_api_key()
        except RuntimeError as error:
            print(f"[Server] Resume failed: {error}")
            return web.json_response({"status": "error", "message": str(error)}, status=500)

        loop = asyncio.get_event_loop()
        resumed = self.soniox_session.resume(
            api_key=api_key,
            audio_format="pcm_s16le",
            translation=TRANSLATION_MODE,
            loop=loop
        )

        if resumed:
            return web.json_response({"status": "ok", "message": "Recognition resumed"})

        # resume 请求失败但仍处于暂停状态，返回错误
        return web.json_response({"status": "error", "message": "Failed to resume recognition"}, status=500)

    async def get_audio_source_handler(self, request):
        """获取当前音频源"""
        source = self.soniox_session.get_audio_source()
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

        success, message = self.soniox_session.set_audio_source(source.strip().lower())
        status_code = 200 if success else 400
        response = {
            "status": "ok" if success else "error",
            "message": message,
            "source": self.soniox_session.get_audio_source()
        }
        return web.json_response(response, status=status_code)

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

        async def _cleanup_llm_session(app_instance):
            try:
                await close_llm_http_session()
            except Exception:
                # Best-effort cleanup.
                pass

        app.on_cleanup.append(_cleanup_llm_session)
        
        # 路由设置
        app.router.add_get('/', self.index_handler)
        app.router.add_get('/ws', self.websocket_handler)
        app.router.add_get('/health', self.health_handler)
        app.router.add_get('/ui-config', self.ui_config_handler)
        app.router.add_get('/segment-mode', self.segment_mode_get_handler)
        app.router.add_post('/segment-mode', self.segment_mode_set_handler)
        app.router.add_get('/llm-refine', self.llm_refine_get_handler)
        app.router.add_post('/llm-refine', self.llm_refine_set_handler)
        app.router.add_get('/api-key-status', self.api_key_status_handler) # 新增路由
        app.router.add_post('/restart', self.restart_handler)
        app.router.add_post('/pause', self.pause_handler)
        app.router.add_post('/resume', self.resume_handler)
        app.router.add_get('/osc-translation', self.osc_translation_get_handler)
        app.router.add_post('/osc-translation', self.osc_translation_set_handler)
        app.router.add_get('/audio-source', self.get_audio_source_handler)
        app.router.add_post('/audio-source', self.set_audio_source_handler)
        
        # 静态文件服务 - 放在最后以避免覆盖API路由
        # 将 static 目录下的文件映射到根路径
        app.router.add_static('/', path=get_resource_path('static'), name='static')
        
        return app
