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
import secrets
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

    parser.add_argument(
        '--provider', dest='translation_provider', choices=('soniox', 'gemini'), default=None,
        help='Translation provider to use (otherwise read from TRANSLATION_PROVIDER / prompted at startup)',
    )

    # --- Soniox-specific options ---
    parser.add_argument('--soniox-temp-key-url', dest='soniox_temp_key_url', default=None)
    parser.add_argument('--soniox-websocket-url', dest='soniox_websocket_url', default=None)
    soniox_sleep_group = parser.add_mutually_exclusive_group()
    soniox_sleep_group.add_argument(
        '--soniox-sleep-on-silence',
        dest='soniox_sleep_on_silence',
        action='store_true',
        default=None,
        help='Close the Soniox stream after long local silence and reopen when speech resumes',
    )
    soniox_sleep_group.add_argument(
        '--no-soniox-sleep-on-silence',
        dest='soniox_sleep_on_silence',
        action='store_false',
        default=None,
        help='Disable long-silence Soniox stream sleeping',
    )
    parser.add_argument('--soniox-sleep-idle-seconds', dest='soniox_sleep_idle_seconds', type=float, default=None)
    parser.add_argument('--soniox-sleep-pre-roll-seconds', dest='soniox_sleep_pre_roll_seconds', type=float, default=None)
    parser.add_argument('--soniox-sleep-speech-grace-seconds', dest='soniox_sleep_speech_grace_seconds', type=float, default=None)

    # --- Gemini-specific options ---
    parser.add_argument('--gemini-temp-key-url', dest='gemini_temp_key_url', default=None)
    parser.add_argument('--gemini-websocket-url', dest='gemini_websocket_url', default=None)
    parser.add_argument('--gemini-model', dest='gemini_model', default=None)
    gemini_sleep_group = parser.add_mutually_exclusive_group()
    gemini_sleep_group.add_argument(
        '--gemini-sleep-on-silence',
        dest='gemini_sleep_on_silence',
        action='store_true',
        default=None,
        help='Close the Gemini stream after long local silence and reopen when speech resumes',
    )
    gemini_sleep_group.add_argument(
        '--no-gemini-sleep-on-silence',
        dest='gemini_sleep_on_silence',
        action='store_false',
        default=None,
        help='Disable long-silence Gemini stream sleeping',
    )
    parser.add_argument('--gemini-sleep-idle-seconds', dest='gemini_sleep_idle_seconds', type=float, default=None)
    parser.add_argument('--gemini-sleep-pre-roll-seconds', dest='gemini_sleep_pre_roll_seconds', type=float, default=None)
    parser.add_argument('--gemini-sleep-speech-grace-seconds', dest='gemini_sleep_speech_grace_seconds', type=float, default=None)

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

    _set_env_if_provided('TRANSLATION_PROVIDER', args.translation_provider)

    _set_env_if_provided('SONIOX_TEMP_KEY_URL', args.soniox_temp_key_url)
    _set_env_if_provided('SONIOX_WEBSOCKET_URL', args.soniox_websocket_url)
    _set_env_bool_if_provided('SONIOX_SLEEP_ON_SILENCE', args.soniox_sleep_on_silence)
    _set_env_if_provided('SONIOX_SLEEP_IDLE_SECONDS', args.soniox_sleep_idle_seconds)
    _set_env_if_provided('SONIOX_SLEEP_PRE_ROLL_SECONDS', args.soniox_sleep_pre_roll_seconds)
    _set_env_if_provided('SONIOX_SLEEP_SPEECH_GRACE_SECONDS', args.soniox_sleep_speech_grace_seconds)

    _set_env_if_provided('GEMINI_TEMP_KEY_URL', args.gemini_temp_key_url)
    _set_env_if_provided('GEMINI_WEBSOCKET_URL', args.gemini_websocket_url)
    _set_env_if_provided('GEMINI_MODEL', args.gemini_model)
    _set_env_bool_if_provided('GEMINI_SLEEP_ON_SILENCE', args.gemini_sleep_on_silence)
    _set_env_if_provided('GEMINI_SLEEP_IDLE_SECONDS', args.gemini_sleep_idle_seconds)
    _set_env_if_provided('GEMINI_SLEEP_PRE_ROLL_SECONDS', args.gemini_sleep_pre_roll_seconds)
    _set_env_if_provided('GEMINI_SLEEP_SPEECH_GRACE_SECONDS', args.gemini_sleep_speech_grace_seconds)

    _set_env_bool_if_provided('USE_TWITCH_AUDIO_STREAM', args.use_twitch_audio_stream)
    _set_env_if_provided('TWITCH_CHANNEL', args.twitch_channel)
    _set_env_if_provided('TWITCH_STREAM_QUALITY', args.twitch_stream_quality)
    _set_env_if_provided('FFMPEG_PATH', args.ffmpeg_path)

    _set_env_bool_if_provided('LLM_REFINE_SHOW_DIFF', args.llm_refine_show_diff)
    _set_env_bool_if_provided('LLM_REFINE_SHOW_DELETIONS', args.llm_refine_show_deletions)
    _set_env_bool_if_provided('LLM_REFINE_DEFAULT_ENABLED', args.llm_refine_default_enabled)


class ProviderManager:
    """Holds the active translation provider + API key as runtime state and
    performs in-process hot-switching (rebuild the session without restarting the
    process). The frontend localStorage is the source of truth for provider/key;
    env keys act as a read-only fallback. The program never writes .env.
    """

    def __init__(self, logger, ipc_server, osc_manager_obj, broadcast_callback):
        import config

        self.config = config
        self.logger = logger
        self.ipc_server = ipc_server
        self.osc_manager = osc_manager_obj
        self.broadcast_callback = broadcast_callback
        self.web_server = None
        self.loop = None

        # New random id per process; lets the frontend tell whether its
        # localStorage override has already been pushed to this backend instance.
        self.boot_id = secrets.token_hex(8)

        # Runtime key overrides pushed from the UI; None => fall back to env.
        self.runtime_keys = {"soniox": None, "gemini": None}

        self.provider = config.TRANSLATION_PROVIDER
        self.translation_mode = config.TRANSLATION_MODE
        self.target_lang = config.TRANSLATION_TARGET_LANG
        self.target_lang_1 = config.normalize_language_code(config.TARGET_LANG_1) or "en"
        self.target_lang_2 = config.normalize_language_code(config.TARGET_LANG_2) or "zh"

        self.lock_manual_controls = bool(config.LOCK_MANUAL_CONTROLS)
        self.setup_required = False

        self._env_get_api_key = None
        self._session_mod = None
        self._ipc_started = False

    # ----- provider module wiring -----
    def _provider_modules(self, provider):
        if provider == "gemini":
            import gemini_session as session_mod
            from gemini_session import GeminiSession as SessionClass
            from gemini_client import get_api_key
        else:
            import soniox_session as session_mod
            from soniox_session import SonioxSession as SessionClass
            from soniox_client import get_api_key
        return session_mod, SessionClass, get_api_key

    # ----- key helpers -----
    def env_key_present(self, provider) -> bool:
        from provider_setup import provider_has_env_key
        return provider_has_env_key(provider)

    def key_source(self) -> str:
        if self.runtime_keys.get(self.provider):
            return "localstorage"
        if self.env_key_present(self.provider):
            return "env"
        return "none"

    def uses_temp_api_key(self, provider) -> bool:
        """Whether the active key for a provider is a dispenser-fetched temp key.

        A runtime override (localStorage) or a persistent env key counts as a
        real key; otherwise the key comes from the temp-key dispenser URL. Note
        this is NOT env_key_present(), which also treats a temp-key URL as a
        present key.
        """
        if self.runtime_keys.get(provider):
            return False
        env_key = "GEMINI_API_KEY" if provider == "gemini" else "SONIOX_API_KEY"
        return not bool(os.environ.get(env_key, "").strip())

    def get_api_key(self) -> str:
        """Return the active key for the current provider (may raise if none).

        Used by web_server restart/resume handlers.
        """
        override = self.runtime_keys.get(self.provider)
        if override:
            return override
        if self._env_get_api_key is None:
            raise RuntimeError("No API key getter configured")
        return self._env_get_api_key()

    def _resolve_current_key(self):
        """Non-raising key resolution. Returns (key_or_none, error_or_none)."""
        override = self.runtime_keys.get(self.provider)
        if override:
            return override, None
        if self._env_get_api_key is None:
            return None, "No API key getter configured"
        try:
            return self._env_get_api_key(), None
        except Exception as error:
            return None, str(error)

    # ----- IPC lifecycle -----
    async def _sync_ipc(self, session_running: bool):
        from config import IPC_ENABLED
        want = bool(IPC_ENABLED) and self.translation_mode != "none" and session_running
        if want and not self._ipc_started:
            try:
                await self.ipc_server.start()
                self._ipc_started = True
            except Exception as e:
                print(f"⚠️  Failed to start IPC server: {e}")
        elif not want and self._ipc_started:
            try:
                await self.ipc_server.stop()
            except Exception:
                pass
            self._ipc_started = False

    # ----- hot switch -----
    async def apply_provider(
        self,
        provider,
        *,
        api_key=None,
        use_env=False,
        soniox_region=None,
        translation_mode=None,
        target_lang=None,
        target_lang_1=None,
        target_lang_2=None,
    ) -> dict:
        """Switch provider/key (and optionally translation settings) in-process."""
        provider = self.config.set_active_provider(provider)
        self.provider = provider

        # Soniox regional endpoint (us | eu | jp); only meaningful for Soniox.
        if provider == "soniox" and soniox_region is not None:
            self.config.set_soniox_region(soniox_region)

        if use_env:
            self.runtime_keys[provider] = None
        elif api_key is not None:
            self.runtime_keys[provider] = api_key

        # Keep the silence-sleep cost saver in sync with the active key type:
        # temporary (dispenser) keys keep the stream open; real keys may sleep.
        self.config.set_uses_temp_api_key(provider, self.uses_temp_api_key(provider))

        if translation_mode is not None:
            self.translation_mode = translation_mode
        if target_lang is not None:
            self.target_lang = target_lang
        if target_lang_1 is not None:
            self.target_lang_1 = target_lang_1
        if target_lang_2 is not None:
            self.target_lang_2 = target_lang_2

        # Gemini Live Translation has no two-way mode: downgrade to one-way and
        # use the first language as the target.
        downgraded_two_way = False
        if provider == "gemini" and self.translation_mode == "two_way":
            self.translation_mode = "one_way"
            if self.target_lang_1:
                self.target_lang = self.target_lang_1
            downgraded_two_way = True

        # Stop old session.
        old_session = self.web_server.session if self.web_server else None
        if old_session is not None:
            try:
                old_session.stop()
            except Exception as e:
                print(f"⚠️  Error stopping previous session: {e}")
        try:
            self.logger.close_log_file()
        except Exception:
            pass

        # Build + wire new session.
        session_mod, SessionClass, env_get_api_key = self._provider_modules(provider)
        self._session_mod = session_mod
        self._env_get_api_key = env_get_api_key
        new_session = SessionClass(self.logger, self.broadcast_callback)

        try:
            new_session.set_translation_target_lang(self.target_lang)
        except Exception:
            pass
        if self.translation_mode == "two_way" and hasattr(new_session, "set_target_langs"):
            new_session.set_target_langs(self.target_lang_1, self.target_lang_2)

        if self.web_server is not None:
            self.web_server.session = new_session
            self.web_server.get_api_key = self.get_api_key
        self.ipc_server.set_session(new_session)
        session_mod.ipc_server = self.ipc_server
        self.osc_manager.set_speaker_labels_enabled(bool(self.config.ENABLE_SPEAKER_DIARIZATION))

        # Resolve key and start (or mark setup_required).
        key, error = self._resolve_current_key()
        started = False
        if key:
            try:
                new_session.start(
                    key,
                    "pcm_s16le",
                    self.translation_mode,
                    self.loop,
                    translation_target_lang=self.target_lang,
                )
                started = True
                self.setup_required = False
            except Exception as e:
                error = str(e)
                print(f"❌ Failed to start session: {e}")
        if not started:
            self.setup_required = True

        await self._sync_ipc(started)

        return {
            "started": started,
            "setup_required": self.setup_required,
            "downgraded_two_way": downgraded_two_way,
            "error": error,
        }

    async def seed_start(self):
        """Initial startup: try to start the resolved provider using its env key."""
        result = await self.apply_provider(self.provider, use_env=True)
        if result["started"]:
            print(f"✅ Session started with provider '{self.provider}'")
        else:
            print("ℹ️  No usable API key found; waiting for configuration via Settings panel.")
        return result


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

    # 非交互式解析翻译 provider（soniox|gemini）。必须在导入 config 之前完成，
    # 以便 config 在求值时能读到 TRANSLATION_PROVIDER。
    from provider_setup import resolve_provider
    provider = resolve_provider()

    import config
    from config import (
        SERVER_HOST, SERVER_PORT, AUTO_OPEN_WEBVIEW,
    )
    from logger import TranscriptLogger
    from web_server import WebServer
    from ipc_server import IPCServer
    from osc_manager import osc_manager

    # 创建日志记录器
    logger = TranscriptLogger()

    # 创建Web服务器（会在创建session时传入）
    web_server = None
    window = None
    window_title = "Real-time Subtitle"
    window_on_top_requests: queue.SimpleQueue[bool | None] = queue.SimpleQueue()

    # broadcast 回调引用 web_server（在 ProviderManager 重建 session 后仍然有效，
    # 因为广播是 WebServer 的方法而非 session 的）。
    def broadcast_callback(data):
        if web_server:
            return web_server.broadcast_to_clients(data)
        return asyncio.sleep(0)  # 返回一个空的协程

    ipc_server = IPCServer()

    # 运行时 provider/key 状态管理器（支持热切换）。
    provider_manager = ProviderManager(logger, ipc_server, osc_manager, broadcast_callback)

    # 锁定手动控制且当前 provider 没有可用的 env key ⇒ 直接报错退出
    # （锁定模式下 UI 不能配置，配置只能来自环境变量）。
    if provider_manager.lock_manual_controls and not provider_manager.env_key_present(provider):
        print("❌ LOCK_MANUAL_CONTROLS is enabled but no API key is configured for "
              f"provider '{provider}'.")
        print("   In locked mode the key can only come from the environment "
              f"(set {'GEMINI_API_KEY' if provider == 'gemini' else 'SONIOX_API_KEY'} "
              "or the corresponding TEMP_KEY_URL).")
        sys.exit(1)

    # 先创建一个占位 session 对象，供 WebServer 在尚未配置 key 时也能响应 /ui-config。
    _seed_mod, _SeedSession, _seed_getter = provider_manager._provider_modules(provider)
    session = _SeedSession(logger, broadcast_callback)
    provider_manager._session_mod = _seed_mod
    provider_manager._env_get_api_key = _seed_getter

    # OSC 输出是否带说话人标签：Soniox 分离说话人时启用，Gemini 关闭
    osc_manager.set_speaker_labels_enabled(bool(config.ENABLE_SPEAKER_DIARIZATION))

    # 创建Web服务器
    web_server = WebServer(session, logger)
    web_server.get_api_key = provider_manager.get_api_key
    web_server.provider_manager = provider_manager

    ipc_server.set_session(session)
    _seed_mod.ipc_server = ipc_server
    web_server.ipc_server = ipc_server

    provider_manager.web_server = web_server

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

    def request_shutdown() -> None:
        """退出整个应用（重置设置后由前端触发）。"""
        print("\n👋 Reset requested, shutting down application...")
        try:
            logger.close_log_file()
        except Exception:
            pass
        os._exit(0)

    web_server.set_shutdown_callback(request_shutdown)

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
        loop = asyncio.get_event_loop()
        provider_manager.loop = loop
        # 启动 seeding：用环境变量里的 key 起 session；没有就保持等待（前端弹设置面板）。
        await provider_manager.seed_start()

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
