"""
主服务器入口文件 - 整合所有模块并启动服务
"""
import signal
import sys
import asyncio
import webbrowser
import threading
import socket
from dotenv import load_dotenv
from aiohttp import web

from config import AUTO_OPEN_BROWSER, SERVER_HOST, SERVER_PORT
from logger import TranscriptLogger
from soniox_session import SonioxSession
from web_server import WebServer
from soniox_client import get_api_key

# 加载 .env 文件中的环境变量
load_dotenv()


def main():
    # 创建日志记录器
    logger = TranscriptLogger()
    
    # 创建Web服务器（会在创建session时传入）
    web_server = None
    
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
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 创建应用
    app = web_server.create_app()
    
    # 启动后台任务
    async def start_background_tasks(app):
        # 在后台线程中运行Soniox会话
        try:
            api_key = get_api_key()
        except RuntimeError as e:
            print(f"❌ Error: {e}")
            print("Please set the SONIOX_API_KEY environment variable or ensure network connection is available")
            raise
        
        loop = asyncio.get_event_loop()
        
        # 总是启用翻译
        translation_mode = "one_way"
        
        # 启动Soniox会话
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
            # 在 Windows 上避免“address already in use”问题
            # sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
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

    listener_socket, actual_port = create_listening_socket(SERVER_HOST, SERVER_PORT)

    if SERVER_PORT != actual_port:
        print(f"⚠️  Port {SERVER_PORT} unavailable, switched to {actual_port}")

    print(f"🚀 Server starting on http://{SERVER_HOST}:{actual_port}")
    
    if AUTO_OPEN_BROWSER:
        print("🌐 Opening browser...")
        # 延迟一点打开浏览器，确保服务器已经启动
        threading.Timer(1.5, lambda: webbrowser.open(f'http://{SERVER_HOST}:{actual_port}')).start()
    else:
        print(f"📱 Please manually open http://{SERVER_HOST}:{actual_port}")
    
    try:
        web.run_app(app, print=None, sock=listener_socket)
    except KeyboardInterrupt:
        print("\n👋 Server closed")
    finally:
        logger.close_log_file()
        if 'listener_socket' in locals():
            try:
                listener_socket.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
