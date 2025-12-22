"""
主服务器入口文件 - 整合所有模块并启动服务
"""
import signal
import sys
import asyncio
import threading
import socket
import os
import time
from dotenv import load_dotenv
from aiohttp import web

from config import SERVER_HOST, SERVER_PORT, AUTO_OPEN_WEBVIEW
from logger import TranscriptLogger
from soniox_session import SonioxSession
from web_server import WebServer
from soniox_client import get_api_key

# 加载 .env 文件中的环境变量
load_dotenv()


def run_server(app, sock):
    """在单独的线程中运行Web服务器"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        # 在非主线程运行时必须禁用信号处理（Linux 下否则会触发 set_wakeup_fd 报错）
        web.run_app(app, print=None, sock=sock, handle_signals=False)
    except Exception as e:
        print(f"Error in server thread: {e}")
    finally:
        sock.close()


def main():
    # 创建日志记录器
    logger = TranscriptLogger()
    
    # 创建Web服务器（会在创建session时传入）
    web_server = None
    window = None
    
    # 创建Soniox会话（传入logger和broadcast回调）
    def broadcast_callback(data):
        if web_server:
            return web_server.broadcast_to_clients(data)
        return asyncio.sleep(0)  # 返回一个空的协程
    
    soniox_session = SonioxSession(logger, broadcast_callback)
    
    # 创建Web服务器
    web_server = WebServer(soniox_session, logger)
    
    # 设置信号处理，优雅退出
    def signal_handler(sig, frame):
        print("\n👋 Received termination signal, shutting down server...")
        logger.close_log_file()
        os._exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 创建应用
    app = web_server.create_app()
    
    # 启动后台任务
    async def start_background_tasks(app_instance):
        try:
            api_key = get_api_key()
        except RuntimeError as e:
            print(f"❌ Error: {e}")
            print("Please set the SONIOX_API_KEY environment variable or ensure network connection is available")
            if window:
                window.destroy()
            raise
        
        loop = asyncio.get_event_loop()
        translation_mode = "one_way"
        soniox_session.start(api_key, "pcm_s16le", translation_mode, loop)
    
    app.on_startup.append(start_background_tasks)
    
    def create_listening_socket(host: str, preferred_port: int) -> tuple[socket.socket, int]:
        candidates = []
        if preferred_port and preferred_port > 0:
            candidates.append(preferred_port)
        candidates.append(0)

        last_error = None
        for port in candidates:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((host, port))
                sock.listen(128)
                sock.setblocking(False)
                actual_port = sock.getsockname()[1]
                return sock, actual_port
            except OSError as error:
                last_error = error
                sock.close()
                continue

        raise last_error if last_error else RuntimeError("Failed to allocate listening socket")

    if AUTO_OPEN_WEBVIEW:
        bind_host = "127.0.0.1"
    else:
        bind_host = SERVER_HOST if SERVER_HOST not in ("localhost", "127.0.0.1") else "0.0.0.0"

    listener_socket, actual_port = create_listening_socket(bind_host, SERVER_PORT)

    if SERVER_PORT != actual_port:
        print(f"⚠️  Port {SERVER_PORT} unavailable, switched to {actual_port}")

    def resolve_display_host() -> str:
        if AUTO_OPEN_WEBVIEW:
            return "127.0.0.1"
        if bind_host not in ("0.0.0.0", "127.0.0.1", "localhost"):
            return bind_host
        # Linux 上 hostname 可能解析成 127.0.1.1，浏览器访问不如 127.0.0.1 直观
        return "127.0.0.1"

    server_url = f"http://{resolve_display_host()}:{actual_port}"
    print(f"🚀 Server starting on {bind_host}:{actual_port}")

    # 解析命令行参数：若包含 --debug 则开启调试模式（显示 devtools）
    debug = ('--debug' in sys.argv)

    # 在新线程中启动 aiohttp 服务器
    server_thread = threading.Thread(target=run_server, args=(app, listener_socket))
    server_thread.daemon = True
    server_thread.start()

    if AUTO_OPEN_WEBVIEW:
        try:
            import webview
        except ImportError:
            print("⚠️  pywebview/Qt backend not available; falling back to browser mode")
            print("🌐 Open this URL in your browser:")
            print(server_url)
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n👋 Server closed by user")
            finally:
                logger.close_log_file()
                os._exit(0)

        title = "Real-time Subtitle"
        window = webview.create_window(title, server_url, width=350, height=600, resizable=True, on_top=True, text_select=True, zoomable=True)

        if not debug and os.name == 'nt':
            try:
                import ctypes
                wh = ctypes.windll.kernel32.GetConsoleWindow()
                if wh:
                    ctypes.windll.user32.ShowWindow(wh, 0)
            except Exception:
                pass

        def on_closed():
            print("👋 Window closed, shutting down application...")
            logger.close_log_file()
            os._exit(0)

        window.events.closed += on_closed

        try:
            webview.start(debug=debug, private_mode=False)
        except KeyboardInterrupt:
            print("\n👋 Server closed by user")
        finally:
            if window:
                window.destroy()
            logger.close_log_file()
            os._exit(0)
    else:
        print("🌐 WebView disabled. Open this URL in your browser:")
        print(server_url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n👋 Server closed by user")
        finally:
            logger.close_log_file()
            os._exit(0)


if __name__ == "__main__":
    main()
