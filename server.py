"""
主服务器入口文件 - 整合所有模块并启动服务
"""
import argparse
import signal
import sys
import asyncio
import threading
import socket
import os
import time
import queue
from dotenv import load_dotenv
from aiohttp import web

# 加载 .env 文件中的环境变量
load_dotenv()


def _set_env_if_provided(name: str, value) -> None:
    if value is None:
        return
    os.environ[name] = str(value)


def _set_env_bool_if_provided(name: str, value) -> None:
    if value is None:
        return
    os.environ[name] = "1" if bool(value) else "0"


def parse_cli_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=True)

    parser.add_argument('--debug', action='store_true', help='Enable WebView devtools (when WebView is enabled)')

    webview_group = parser.add_mutually_exclusive_group()
    webview_group.add_argument('--webview', dest='auto_open_webview', action='store_true', default=None,
                               help='Enable embedded WebView window')
    webview_group.add_argument('--no-webview', dest='auto_open_webview', action='store_false', default=None,
                               help='Disable embedded WebView; print URL only')

    lock_group = parser.add_mutually_exclusive_group()
    lock_group.add_argument('--lock-manual-controls', dest='lock_manual_controls', action='store_true', default=None,
                            help='Hide/disable manual controls in UI and reject related backend operations')
    lock_group.add_argument('--unlock-manual-controls', dest='lock_manual_controls', action='store_false', default=None,
                            help='Enable manual controls (default behavior when config allows)')

    lang_group = parser.add_mutually_exclusive_group()
    lang_group.add_argument('--use-system-language', dest='use_system_language', action='store_true', default=None,
                            help='Use OS language as translation target')
    lang_group.add_argument('--no-system-language', dest='use_system_language', action='store_false', default=None,
                            help='Do not use OS language; use --target-lang')

    parser.add_argument('--target-lang', dest='target_lang', default=None, help='Translation target language (ISO 639-1)')
    parser.add_argument('--target-lang-1', dest='target_lang_1', default=None)
    parser.add_argument('--target-lang-2', dest='target_lang_2', default=None)
    parser.add_argument(
        '--osc-send-text-mode',
        dest='osc_send_text_mode',
        choices=('smart', 'translation_only', 'source_only'),
        default=None,
        help='OSC text selection mode: smart | translation_only | source_only',
    )

    parser.add_argument('--server-host', dest='server_host', default=None)
    parser.add_argument('--server-port', dest='server_port', type=int, default=None)

    parser.add_argument('--soniox-temp-key-url', dest='soniox_temp_key_url', default=None)
    parser.add_argument('--soniox-websocket-url', dest='soniox_websocket_url', default=None)

    twitch_group = parser.add_mutually_exclusive_group()
    twitch_group.add_argument('--use-twitch-audio-stream', dest='use_twitch_audio_stream', action='store_true', default=None)
    twitch_group.add_argument('--no-twitch-audio-stream', dest='use_twitch_audio_stream', action='store_false', default=None)
    parser.add_argument('--twitch-channel', dest='twitch_channel', default=None)
    parser.add_argument('--twitch-stream-quality', dest='twitch_stream_quality', default=None)
    parser.add_argument('--ffmpeg-path', dest='ffmpeg_path', default=None)

    refine_diff_group = parser.add_mutually_exclusive_group()
    refine_diff_group.add_argument('--llm-refine-show-diff', dest='llm_refine_show_diff', action='store_true', default=None,
                                  help='Show diff highlights for refined translations (no UI toggle)')
    refine_diff_group.add_argument('--no-llm-refine-show-diff', dest='llm_refine_show_diff', action='store_false', default=None,
                                  help='Do not show diff highlights for refined translations (default)')

    refine_del_group = parser.add_mutually_exclusive_group()
    refine_del_group.add_argument('--llm-refine-show-deletions', dest='llm_refine_show_deletions', action='store_true', default=None,
                                 help='When diff is enabled, show deleted text with red strikethrough (no UI toggle)')
    refine_del_group.add_argument('--no-llm-refine-show-deletions', dest='llm_refine_show_deletions', action='store_false', default=None,
                                 help='When diff is enabled, do not show deleted text (default)')

    refine_toggle_group = parser.add_mutually_exclusive_group()
    refine_toggle_group.add_argument('--llm-refine', dest='llm_refine_default_enabled', action='store_true', default=None,
                                     help='Enable LLM refine by default (can be toggled in UI if unlocked)')
    refine_toggle_group.add_argument('--no-llm-refine', dest='llm_refine_default_enabled', action='store_false', default=None,
                                     help='Disable LLM refine by default (can be toggled in UI if unlocked)')

    return parser.parse_known_args(argv)


def apply_cli_overrides_to_env(args: argparse.Namespace) -> None:
    _set_env_bool_if_provided('AUTO_OPEN_WEBVIEW', args.auto_open_webview)
    _set_env_bool_if_provided('LOCK_MANUAL_CONTROLS', args.lock_manual_controls)

    _set_env_bool_if_provided('USE_SYSTEM_LANGUAGE', args.use_system_language)
    _set_env_if_provided('TARGET_LANG', args.target_lang)
    _set_env_if_provided('TARGET_LANG_1', args.target_lang_1)
    _set_env_if_provided('TARGET_LANG_2', args.target_lang_2)
    _set_env_if_provided('OSC_SEND_TEXT_MODE', args.osc_send_text_mode)

    if args.target_lang is not None and args.use_system_language is None:
        os.environ['USE_SYSTEM_LANGUAGE'] = '0'

    _set_env_if_provided('SERVER_HOST', args.server_host)
    if args.server_port is not None:
        _set_env_if_provided('SERVER_PORT', int(args.server_port))

    _set_env_if_provided('SONIOX_TEMP_KEY_URL', args.soniox_temp_key_url)
    _set_env_if_provided('SONIOX_WEBSOCKET_URL', args.soniox_websocket_url)

    _set_env_bool_if_provided('USE_TWITCH_AUDIO_STREAM', args.use_twitch_audio_stream)
    _set_env_if_provided('TWITCH_CHANNEL', args.twitch_channel)
    _set_env_if_provided('TWITCH_STREAM_QUALITY', args.twitch_stream_quality)
    _set_env_if_provided('FFMPEG_PATH', args.ffmpeg_path)

    _set_env_bool_if_provided('LLM_REFINE_SHOW_DIFF', args.llm_refine_show_diff)
    _set_env_bool_if_provided('LLM_REFINE_SHOW_DELETIONS', args.llm_refine_show_deletions)
    _set_env_bool_if_provided('LLM_REFINE_DEFAULT_ENABLED', args.llm_refine_default_enabled)


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
    args, _unknown = parse_cli_args(sys.argv[1:])
    apply_cli_overrides_to_env(args)

    from config import SERVER_HOST, SERVER_PORT, AUTO_OPEN_WEBVIEW, TRANSLATION_MODE, MUTE_MIC_WHEN_VRCHAT_SELF_MUTED
    from logger import TranscriptLogger
    from soniox_session import SonioxSession
    from web_server import WebServer
    from soniox_client import get_api_key
    from ipc_server import IPCServer

    # 创建日志记录器
    logger = TranscriptLogger()
    
    # 创建Web服务器（会在创建session时传入）
    web_server = None
    window = None
    window_title = "Real-time Subtitle"
    window_on_top_requests: queue.SimpleQueue[bool | None] = queue.SimpleQueue()
    
    # 创建Soniox会话（传入logger和broadcast回调）
    def broadcast_callback(data):
        if web_server:
            return web_server.broadcast_to_clients(data)
        return asyncio.sleep(0)  # 返回一个空的协程
    
    soniox_session = SonioxSession(logger, broadcast_callback)
    
    # 创建Web服务器
    web_server = WebServer(soniox_session, logger)

    ipc_server = IPCServer()
    ipc_server.set_soniox_session(soniox_session)
    import soniox_session as soniox_session_mod
    soniox_session_mod.ipc_server = ipc_server
    web_server.ipc_server = ipc_server

    def apply_window_on_top_fallback(on_top: bool) -> bool:
        """在 pywebview 动态置顶失败时，使用 Win32 兜底。"""
        if os.name != 'nt' or window is None:
            return False

        try:
            import ctypes

            user32 = ctypes.windll.user32

            hwnd = None
            native = getattr(window, 'native', None)
            handle = getattr(native, 'Handle', None) if native is not None else None
            if handle is not None:
                if hasattr(handle, 'ToInt64'):
                    hwnd = int(handle.ToInt64())
                elif hasattr(handle, 'ToInt32'):
                    hwnd = int(handle.ToInt32())

            if not hwnd:
                hwnd = user32.FindWindowW(None, window_title)
            if not hwnd:
                return False

            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOACTIVATE = 0x0010

            insert_after = HWND_TOPMOST if bool(on_top) else HWND_NOTOPMOST
            flags = SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE

            return bool(user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags))
        except Exception:
            return False

    def set_window_on_top(on_top: bool) -> bool:
        """请求动态切换 WebView 窗口置顶状态。"""
        if not AUTO_OPEN_WEBVIEW or window is None:
            return False

        try:
            window_on_top_requests.put_nowait(bool(on_top))
            return True
        except Exception:
            return False

    web_server.set_window_on_top_callback(set_window_on_top)
    
    # 设置信号处理，优雅退出
    def signal_handler(sig, frame):
        print("\n👋 Received termination signal, shutting down server...")
        logger.close_log_file()
        os._exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 创建应用
    app = web_server.create_app()

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
        translation_mode = TRANSLATION_MODE
        soniox_session.start(api_key, "pcm_s16le", translation_mode, loop)

        from config import IPC_ENABLED
        if IPC_ENABLED and TRANSLATION_MODE != "none":
            try:
                await ipc_server.start()
            except Exception as e:
                print(f"⚠️  Failed to start IPC server: {e}")
        else:
            if not IPC_ENABLED:
                print("[IPC] IPC is disabled in config")
            elif TRANSLATION_MODE == "none":
                print("[IPC] IPC server not started because translation is disabled")

    async def cleanup_background_tasks(app_instance):
        try:
            await ipc_server.stop()
        except Exception:
            pass

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(cleanup_background_tasks)
    
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

    if SERVER_PORT and SERVER_PORT > 0 and SERVER_PORT != actual_port:
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

    debug = bool(args.debug)

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
        window = webview.create_window(window_title, server_url, width=350, height=600, resizable=True, on_top=True, text_select=True, zoomable=True)

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
            try:
                window_on_top_requests.put_nowait(None)
            except Exception:
                pass
            logger.close_log_file()
            os._exit(0)

        window.events.closed += on_closed

        def process_window_commands(window_instance):
            last_applied = True

            while True:
                requested = window_on_top_requests.get()
                if requested is None:
                    return

                requested = bool(requested)
                if requested == last_applied:
                    continue

                applied = False
                try:
                    window_instance.on_top = requested
                    applied = True
                except Exception:
                    applied = False

                if not applied:
                    applied = apply_window_on_top_fallback(requested)

                if applied:
                    last_applied = requested

        try:
            webview.start(process_window_commands, window, debug=debug, private_mode=False)
        except KeyboardInterrupt:
            print("\n👋 Server closed by user")
        finally:
            try:
                window_on_top_requests.put_nowait(None)
            except Exception:
                pass
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
