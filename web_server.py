"""
Web服务器模块 - 处理HTTP和WebSocket连接
"""
import json
import asyncio
import os
import re
from aiohttp import web
from aiohttp import WSMsgType

from config import get_resource_path, LOCK_MANUAL_CONTROLS, EXTERNAL_WS_URI
from audio_capture import get_audio_devices

# 日语假名注音支持
try:
    import pykakasi
    kakasi = pykakasi.kakasi()
    FURIGANA_AVAILABLE = True
except ImportError:
    kakasi = None
    FURIGANA_AVAILABLE = False
    print("⚠️  pykakasi not installed, furigana feature disabled")

@web.middleware
async def cache_bypass_middleware(request, handler):
    """Add no-cache headers to all non-WS responses."""
    response = await handler(request)
    if isinstance(response, web.StreamResponse):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


def add_furigana(text):
    """为日语文本添加假名注音，返回带有ruby标签的HTML"""
    if not FURIGANA_AVAILABLE or not text:
        return text
    
    result = kakasi.convert(text)
    html_parts = []
    
    for item in result:
        orig = item['orig']
        hira = item['hira']
        
        # 检查是否包含汉字（需要注音）
        has_kanji = any('\u4e00' <= c <= '\u9fff' for c in orig)
        
        # 检查是否包含片假名（需要注音）
        has_katakana = any('\u30a0' <= c <= '\u30ff' for c in orig)
        
        if (has_kanji or has_katakana) and orig != hira:
            # 有汉字或片假名且读音不同，添加ruby注音
            html_parts.append(f'<ruby>{orig}<rp>(</rp><rt>{hira}</rt><rp>)</rp></ruby>')
        else:
            # 无需注音
            html_parts.append(orig)
    
    return ''.join(html_parts)


class WebServer:
    """Web服务器管理器"""
    
    def __init__(self, soniox_session, logger):
        self.soniox_session = soniox_session
        self.logger = logger
        self.websocket_clients = set()
        self.app_runner = None
        self.api_key_error_message = None # 新增属性
        self.external_websocket_clients = set()  # External WebSocket clients
        self.external_ws_uri = EXTERNAL_WS_URI  # External WebSocket URI from config

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
    
    async def external_websocket_handler(self, request):
        """External WebSocket handler for external applications"""
        remote_addr = request.remote
        path = request.path
        
        ws = web.WebSocketResponse()
        try:
            await ws.prepare(request)
        except Exception as e:
            raise
        
        # Add to external client list
        self.external_websocket_clients.add(ws)
        client_id = f"{remote_addr}-{id(ws)}"
        print(f"[External WS] Client connected (id={client_id}). Total external clients: {len(self.external_websocket_clients)}")
        
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # Handle client messages if needed
                    pass
                elif msg.type == WSMsgType.ERROR:
                    break
                elif msg.type == WSMsgType.CLOSE:
                    break
        except Exception as e:
            pass
        finally:
            # Remove from client list
            self.external_websocket_clients.discard(ws)
            remaining_count = len(self.external_websocket_clients)
            print(f"[External WS] Client disconnected (id={client_id}). Total external clients: {remaining_count}")
            # Ensure connection is closed
            if not ws.closed:
                try:
                    await ws.close()
                except Exception as close_err:
                    pass
        
        return ws
    
    async def send_to_external_clients(self, text: str):
        """Send text to all external WebSocket clients"""
        if not text:
            return
        
        # Send plain text (not JSON)
        # Send to external WebSocket clients
        if self.external_websocket_clients:
            # Remove disconnected clients
            disconnected_clients = []
            for client in list(self.external_websocket_clients):
                if client.closed:
                    disconnected_clients.append(client)
            
            for client in disconnected_clients:
                self.external_websocket_clients.discard(client)
            
            if self.external_websocket_clients:
                client_list = list(self.external_websocket_clients)
                
                # Track send attempts and results
                send_start_time = asyncio.get_event_loop().time()
                
                # Send to each client individually to prevent one blocking client from affecting others
                async def send_to_client_safely(client, text_to_send, client_index):
                    try:
                        # Use asyncio.wait_for to add timeout (5 seconds)
                        # This prevents blocking if the client's send buffer is full
                        await asyncio.wait_for(client.send_str(text_to_send), timeout=5.0)
                        return True
                    except asyncio.TimeoutError:
                        # Client's send buffer is full or client is not reading messages
                        # Remove the client to prevent blocking other clients
                        self.external_websocket_clients.discard(client)
                        return False
                    except Exception as e:
                        self.external_websocket_clients.discard(client)
                        return False
                
                # Create tasks for each client but don't await them all
                # This ensures that if one client blocks, others can still receive messages
                # The event loop will process tasks as they become ready
                tasks = []
                for idx, client in enumerate(client_list):
                    task = asyncio.create_task(send_to_client_safely(client, text, idx))
                    tasks.append(task)
                
                # Wait for at least one task to complete or timeout
                # This ensures the event loop processes the sends even if there's only one client
                if tasks:
                    # Use asyncio.wait to wait for at least one task to complete
                    # This ensures the event loop processes the sends
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=0.1)
                    
                    # Check results of completed tasks
                    for task in done:
                        try:
                            task.result()
                        except Exception as e:
                            pass
                    
                    # Let remaining tasks continue in the background
                    # They will complete when the client reads the messages
    
    async def external_ws_config_get_handler(self, request):
        """Get external WebSocket configuration"""
        return web.json_response({
            "uri": self.external_ws_uri
        })
    
    async def external_ws_config_set_handler(self, request):
        """Set external WebSocket configuration"""
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON payload"}, status=400)
        
        if "uri" in payload:
            self.external_ws_uri = str(payload["uri"])
        
        return web.json_response({
            "status": "ok",
            "uri": self.external_ws_uri
        })
    
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
            translation = "one_way"  # 总是启用翻译
            
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
            translation="one_way",
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

    async def furigana_handler(self, request):
        """为日语文本添加假名注音"""
        if not FURIGANA_AVAILABLE:
            return web.json_response({
                "status": "error",
                "message": "Furigana feature not available (pykakasi not installed)"
            }, status=503)
        
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON payload"}, status=400)
        
        text = payload.get("text", "")
        if not text:
            return web.json_response({"status": "ok", "html": ""})
        
        html = add_furigana(text)
        return web.json_response({"status": "ok", "html": html})

    async def get_audio_devices_handler(self, request):
        """获取所有可用的音频设备列表"""
        devices = get_audio_devices()
        return web.json_response({
            "status": "ok",
            "devices": devices
        })

    async def get_audio_device_settings_handler(self, request):
        """获取当前音频设备设置"""
        return web.json_response({
            "status": "ok",
            "input_device_id": self.soniox_session.get_input_device(),
            "output_device_id": self.soniox_session.get_output_device()
        })

    async def set_input_device_handler(self, request):
        """设置输入设备（麦克风）"""
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Audio device switching is disabled by server config"},
                status=403
            )

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON payload"}, status=400)

        device_id = payload.get("device_id")
        if device_id is not None and not isinstance(device_id, str):
            return web.json_response({"status": "error", "message": "'device_id' must be a string or null"}, status=400)

        # 空文字列をNoneに変換
        if device_id == "":
            device_id = None

        success, message = self.soniox_session.set_input_device(device_id)
        status_code = 200 if success else 400
        return web.json_response({
            "status": "ok" if success else "error",
            "message": message,
            "input_device_id": self.soniox_session.get_input_device()
        }, status=status_code)

    async def set_output_device_handler(self, request):
        """设置输出设备（扬声器，用于系统音频捕获）"""
        if LOCK_MANUAL_CONTROLS:
            return web.json_response(
                {"status": "error", "message": "Audio device switching is disabled by server config"},
                status=403
            )

        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"status": "error", "message": "Invalid JSON payload"}, status=400)

        device_id = payload.get("device_id")
        if device_id is not None and not isinstance(device_id, str):
            return web.json_response({"status": "error", "message": "'device_id' must be a string or null"}, status=400)

        # 空文字列をNoneに変換
        if device_id == "":
            device_id = None

        success, message = self.soniox_session.set_output_device(device_id)
        status_code = 200 if success else 400
        return web.json_response({
            "status": "ok" if success else "error",
            "message": message,
            "output_device_id": self.soniox_session.get_output_device()
        }, status=status_code)
    
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
        
        # 路由设置
        app.router.add_get('/', self.index_handler)
        app.router.add_get('/ws', self.websocket_handler)
        app.router.add_get('/health', self.health_handler)
        app.router.add_get('/ui-config', self.ui_config_handler)
        app.router.add_get('/api-key-status', self.api_key_status_handler) # 新增路由
        app.router.add_post('/restart', self.restart_handler)
        app.router.add_post('/pause', self.pause_handler)
        app.router.add_post('/resume', self.resume_handler)
        app.router.add_get('/osc-translation', self.osc_translation_get_handler)
        app.router.add_post('/osc-translation', self.osc_translation_set_handler)
        app.router.add_get('/audio-source', self.get_audio_source_handler)
        app.router.add_post('/audio-source', self.set_audio_source_handler)
        app.router.add_get('/audio-devices', self.get_audio_devices_handler)
        app.router.add_get('/audio-device-settings', self.get_audio_device_settings_handler)
        app.router.add_post('/audio-device-input', self.set_input_device_handler)
        app.router.add_post('/audio-device-output', self.set_output_device_handler)
        app.router.add_post('/furigana', self.furigana_handler)
        app.router.add_get('/external-ws-config', self.external_ws_config_get_handler)
        app.router.add_post('/external-ws-config', self.external_ws_config_set_handler)
        
        # 静态文件服务 - 放在最后以避免覆盖API路由
        # 将 static 目录下的文件映射到根路径
        app.router.add_static('/', path=get_resource_path('static'), name='static')
        
        return app
    
    def create_external_ws_app(self):
        """Create external WebSocket application"""
        app = web.Application()
        
        # Add WebSocket route
        app.router.add_get('/', self.external_websocket_handler)
        
        # Add a catch-all handler for 404
        async def not_found_handler(request):
            return web.Response(text=f"404: Path '{request.path}' not found. Available path: /", status=404)
        
        app.router.add_route('*', '/{path:.*}', not_found_handler)
        
        return app
