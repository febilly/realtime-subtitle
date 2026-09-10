"""Twitch 音频捕获模块 - 从 Twitch 串流提取音频并输出 PCM_s16le"""

from collections import deque
import subprocess
import threading
import time
from typing import Optional


class TwitchAudioStreamer:
    """从 Twitch 直播串流提取音频并输出 PCM_s16le 到 Soniox。

    依赖：streamlink + ffmpeg。

    说明：streamlink 为可选依赖，仅在使用 Twitch 作为音频源时才会尝试导入。
    """

    def __init__(
        self,
        ws,
        channel: str,
        quality: str = "audio_only",
        ffmpeg_path: str = "ffmpeg",
        sample_rate: int = 16000,
        chunk_size: int = 3840,
    ):
        if not channel:
            raise ValueError("Twitch channel is empty")

        self.ws = ws
        self.channel = channel
        self.quality = quality
        self.ffmpeg_path = ffmpeg_path
        self.sample_rate = sample_rate
        self.chunk_size = chunk_size

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._process_lock = threading.Lock()
        self._stderr_thread: Optional[threading.Thread] = None
        self._error_log: deque[str] = deque(maxlen=20)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="TwitchAudioStreamer", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        with self._process_lock:
            proc = self._process
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass

        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
            if thread.is_alive():
                # 若仍未退出，实施二次强制 kill
                with self._process_lock:
                    proc = self._process
                    if proc and proc.poll() is None:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                thread.join(timeout=1.0)
        self._thread = None

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen, error_log: deque) -> None:
        try:
            if proc.stderr:
                for line in iter(proc.stderr.readline, b""):
                    if not line:
                        break
                    if isinstance(line, bytes):
                        text = line.decode("utf-8", errors="ignore")
                    else:
                        text = str(line)
                    for subline in text.splitlines():
                        stripped = subline.strip()
                        if stripped:
                            error_log.append(stripped)
        except Exception:
            pass

    def _resolve_stream_url(self) -> str:
        try:
            import streamlink
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "streamlink is not installed. Please install it (pip install streamlink)."
            ) from exc

        session = streamlink.Streamlink()
        url = f"https://www.twitch.tv/{self.channel}"

        # Twitch low-latency mode: initialize the Twitch plugin explicitly so we can pass plugin options.
        # If the installed streamlink version/plugin doesn't support this option, fall back to session.streams().
        streams = None
        try:
            from streamlink.plugins.twitch import __plugin__ as Twitch

            plugin = Twitch(session, url, options={"low-latency": True})
            streams = plugin.streams()
        except Exception:
            print("⚠️  Unable to enable Twitch low-latency mode; falling back to standard streamlink behavior")
            streams = session.streams(url)

        if not streams:
            raise RuntimeError(f"No streams available for {url}")

        preferred = self.quality
        stream = streams.get(preferred) or streams.get("audio_only") or streams.get("best")
        if stream is None:
            raise RuntimeError(f"Unable to find suitable stream quality (preferred={preferred})")

        try:
            return stream.to_url()
        except Exception as error:
            raise RuntimeError(f"Failed to resolve stream URL: {error}")

    def _run(self) -> None:
        bytes_per_chunk = int(self.chunk_size) * 2  # int16 mono
        retry_delay = 2.0
        max_delay = 60.0

        while not self._stop_event.is_set():
            process: Optional[subprocess.Popen] = None
            stderr_thread: Optional[threading.Thread] = None
            had_error = False

            try:
                stream_url = self._resolve_stream_url()
                retry_delay = 2.0  # 成功解析后重置退避延迟
                print(f"📺 Twitch audio streaming: {self.channel} ({self.quality})")

                cmd = [
                    self.ffmpeg_path,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    stream_url,
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    str(self.sample_rate),
                    "-f",
                    "s16le",
                    "-acodec",
                    "pcm_s16le",
                    "pipe:1",
                ]

                with self._process_lock:
                    if self._stop_event.is_set():
                        return
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        bufsize=0,
                    )
                    self._process = process

                error_log: deque[str] = deque(maxlen=20)
                self._error_log = error_log

                stderr_thread = threading.Thread(
                    target=self._drain_stderr,
                    args=(process, error_log),
                    name="TwitchFFmpegStderrDrain",
                    daemon=True,
                )
                self._stderr_thread = stderr_thread
                stderr_thread.start()

                assert process.stdout is not None
                while not self._stop_event.is_set():
                    try:
                        data = process.stdout.read(bytes_per_chunk)
                    except Exception:
                        break
                    if not data:
                        break
                    try:
                        self.ws.send(data)
                    except Exception as send_error:
                        print(f"Error sending Twitch audio data: {send_error}")
                        return

                if self._stop_event.is_set():
                    return

                if stderr_thread.is_alive():
                    stderr_thread.join(timeout=0.5)

                if error_log:
                    stderr_text = "\n".join(error_log)
                    print(f"ffmpeg error: {stderr_text}")

            except FileNotFoundError:
                print("❌ ffmpeg not found. Please install ffmpeg and ensure it's in PATH, or set FFMPEG_PATH in config.py")
                return
            except ModuleNotFoundError as error:
                print(f"❌ {error}")
                return
            except Exception as error:
                print(f"Error streaming Twitch audio: {error}")
                had_error = True
            finally:
                with self._process_lock:
                    if process is not None:
                        if process.poll() is None:
                            try:
                                process.terminate()
                            except Exception:
                                pass
                        if self._process is process:
                            self._process = None

                if stderr_thread and stderr_thread.is_alive():
                    stderr_thread.join(timeout=0.5)

                if process is not None:
                    try:
                        if process.stdout:
                            process.stdout.close()
                    except Exception:
                        pass
                    try:
                        if process.stderr:
                            process.stderr.close()
                    except Exception:
                        pass

            if self._stop_event.is_set():
                return

            if had_error:
                wait_time = retry_delay
                retry_delay = min(max_delay, retry_delay * 2.0)
            else:
                wait_time = 1.0
                retry_delay = 2.0

            if self._stop_event.wait(wait_time):
                return
