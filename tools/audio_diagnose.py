"""桌面音频捕获自查工具（面向最终用户，输出压缩到一屏）。

用户反馈"客户端听不到桌面声音"时，让对方在**放着声音**的情况下运行，
把窗口截图发回来即可定位。

除了复刻 audio_capture.py 的系统音频捕获路径（设备解析 -> loopback 查找
-> recorder 参数），还会并行扫描**所有**输出端点的电平，直接指出声音到底
落在哪个设备上——客户端只录 Windows 默认输出端点，这是最常见的失配原因。

用法（源码环境）：
    python tools/audio_diagnose.py [--seconds 8]
打包：
    tools/build_audio_diagnose.bat
"""
import argparse
import os
import sys
import threading
import time
import traceback
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VERSION = "v1"
SAMPLE_RATE = 16000
BLOCK_FRAMES = 1600      # 0.1s
CAPTURE_BLOCKSIZE = 4096  # 给 WASAPI 一个 256ms 的缓冲，避免抖动丢数据
NAME_W = 42
SOUND_THRESHOLD = 0.001
MAX_RENDER_ROWS = 14
MAX_CAPTURE_ROWS = 10


# ---------------------------------------------------------------- 终端宽度
def _wide(char: str) -> bool:
    return unicodedata.east_asian_width(char) in ("W", "F")


def wlen(text: str) -> int:
    return sum(2 if _wide(c) else 1 for c in text)


def pad(text: str, width: int) -> str:
    """按显示宽度截断/补齐，中日韩按两列算。"""
    out = []
    used = 0
    for char in text:
        step = 2 if _wide(char) else 1
        if used + step > width - 2:
            out.append("..")
            used += 2
            break
        out.append(char)
        used += step
    return "".join(out) + " " * max(0, width - used)


def setup_console() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
        ctypes.windll.kernel32.SetConsoleTitleW(f"Desktop Audio Diagnose {VERSION}")
        # 默认 80x25 装不下整份报告，撑大一点保证用户一张截图就能截全
        if ctypes.windll.kernel32.GetConsoleWindow():
            os.system("mode con: cols=92 lines=44")
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ------------------------------------------------------- 默认通信设备(eComm)
def default_comm_speaker_id(sc) -> str:
    """soundcard 只暴露 eConsole，这里补一个 eCommunications 的查询。

    Discord / VRChat 之类的语音走通信设备，和默认设备不是一回事。
    """
    try:
        from soundcard import mediafoundation as mf
        with mf._DeviceEnumerator() as enum:
            ppDevice = mf._ffi.new("IMMDevice **")
            hr = enum._ptr[0][0].lpVtbl.GetDefaultAudioEndpoint(enum._ptr[0], 0, 2, ppDevice)
            mf._com.check_error(hr)
            device_id = enum._device_id(ppDevice)
            mf._com.release(ppDevice)
            return str(device_id)
    except Exception:
        return ""


# ------------------------------------------------------------------ 电平扫描
def scan_one(mic, seconds, results, index, np):
    try:
        with mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=CAPTURE_BLOCKSIZE) as recorder:
            peak = 0.0
            square_sum = 0.0
            count = 0
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                data = np.asarray(recorder.record(numframes=BLOCK_FRAMES), dtype=np.float32)
                mono = data if data.ndim == 1 else data[:, 0]
                if mono.size:
                    peak = max(peak, float(np.max(np.abs(mono))))
                    square_sum += float(np.sum(mono * mono))
                    count += int(mono.size)
            rms = (square_sum / count) ** 0.5 if count else 0.0
            results[index] = (peak, rms, "")
    except Exception as error:
        results[index] = (None, None, f"{type(error).__name__}: {error}"[:34])


def bar_for(peak: float) -> str:
    if peak <= 0:
        return ""
    filled = max(1, min(10, int(peak * 22)))
    return "#" * filled


# ---------------------------------------------------------------------- main
def run(args) -> int:
    print("=" * 78)
    print(f" 桌面音频捕获自查 {VERSION}   —— 请把整个窗口截图发回给开发者".rstrip())
    print("=" * 78)

    try:
        import numpy as np
        import soundcard as sc
    except Exception as error:
        print(f"[致命] 无法导入 soundcard/numpy：{type(error).__name__}: {error}")
        traceback.print_exc()
        return 1

    frozen = "exe" if getattr(sys, "frozen", False) else "src"
    try:
        from importlib.metadata import version as _pkg_version
        sc_version = _pkg_version("soundcard")
    except Exception:
        sc_version = getattr(sc, "__version__", "?")
    try:
        win = sys.getwindowsversion()
        os_text = f"win{win.major}.{win.minor}.{win.build}"
    except Exception:
        os_text = sys.platform
    print(f"env : {os_text} | py{sys.version.split()[0]} {'x64' if sys.maxsize > 2**32 else 'x86'} | "
          f"soundcard {sc_version} | {frozen}")

    try:
        speakers = list(sc.all_speakers())
        mics_all = list(sc.all_microphones(include_loopback=True))
        capture_mics = [m for m in mics_all if not m.isloopback]
    except Exception as error:
        print(f"[致命] 枚举音频设备失败：{type(error).__name__}: {error}")
        traceback.print_exc()
        return 1

    try:
        default_speaker = sc.default_speaker()
        default_id = default_speaker.id
    except Exception as error:
        print(f"[致命] 没有可用的默认输出设备，loopback 无法工作：{error}")
        return 1

    comm_id = default_comm_speaker_id(sc)
    ids = [s.id for s in speakers]
    default_idx = ids.index(default_id) if default_id in ids else -1
    comm_idx = ids.index(comm_id) if comm_id in ids else -1

    print(f"默认输出(Console) : #{default_idx}  {pad(default_speaker.name, 50).rstrip()}")
    if comm_idx == default_idx:
        print("默认通信(Comm)    : 同上")
    elif comm_idx >= 0:
        print(f"默认通信(Comm)    : #{comm_idx}  {pad(speakers[comm_idx].name, 50).rstrip()}  <- 语音类程序走这个")
    else:
        print("默认通信(Comm)    : 未知")

    # ---- 复刻 audio_capture.py:577 的按名字查 loopback ----
    verdict_lookup = ""
    try:
        by_name = sc.get_microphone(id=str(default_speaker.name), include_loopback=True)
        name_hit = f"#{ids.index(by_name.id)}" if by_name.id in ids else "非输出端点"
        if not by_name.isloopback:
            verdict_lookup = f"[!!] 名字匹配命中真实麦克风 {by_name.name!r} —— 录到的是麦克风"
        elif by_name.id != default_id:
            verdict_lookup = f"[!!] 名字匹配命中了另一个端点 {name_hit} —— 会录到静音"
        else:
            verdict_lookup = "OK  名字匹配与 id 匹配一致"
    except Exception as error:
        verdict_lookup = f"[!!] 名字匹配抛异常：{type(error).__name__}: {error}"
    print(f"loopback 名字匹配 : {verdict_lookup}")

    names = [m.name for m in mics_all]
    dups = sorted({n for n in names if names.count(n) > 1})
    if dups:
        print(f"[!] 存在重名端点(会导致按名字匹配拿错设备)：{', '.join(dups)[:60]}")

    # ---- 并行扫描所有输出端点 ----
    seconds = float(args.seconds)
    print("-" * 78)
    print(f">>> 现在请让桌面持续发声（音乐/视频/游戏/语音），正在采集 {seconds:.0f} 秒 ...")
    loopbacks = []
    for speaker in speakers:
        try:
            loopbacks.append(sc.get_microphone(id=speaker.id, include_loopback=True))
        except Exception:
            loopbacks.append(None)

    results = [None] * len(speakers)
    threads = []
    for index, loopback in enumerate(loopbacks):
        if loopback is None:
            results[index] = (None, None, "no loopback endpoint")
            continue
        thread = threading.Thread(target=scan_one, args=(loopback, seconds, results, index, np), daemon=True)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join(timeout=seconds + 10)

    # ---- 输出表 ----
    print("-" * 78)
    print("  # " + pad("输出设备 render  [*默认 c通信]", NAME_W) + "   peak     rms 电平")
    sounding = []
    for index, speaker in enumerate(speakers):
        if index >= MAX_RENDER_ROWS:
            print(f"  ... 另有 {len(speakers) - MAX_RENDER_ROWS} 个输出设备未显示")
            break
        mark = "*" if index == default_idx else ("c" if index == comm_idx else " ")
        peak, rms, err = results[index] or (None, None, "no data")
        if err:
            print(f"{mark}{index:<2} " + pad(speaker.name, NAME_W) + f" ERR {err}")
            continue
        if peak >= SOUND_THRESHOLD:
            sounding.append(index)
        print(f"{mark}{index:<2} " + pad(speaker.name, NAME_W)
              + f" {peak:6.4f} {rms:7.5f} {bar_for(peak)}")

    # 输入设备只在可能干扰名字匹配时才展开，平时一行带过，保证一屏能截完
    if dups or not verdict_lookup.startswith("OK"):
        print("  # 输入设备 capture (名字匹配可能被它们干扰)")
        for index, mic in enumerate(capture_mics):
            if index >= MAX_CAPTURE_ROWS:
                print(f"  ... 另有 {len(capture_mics) - MAX_CAPTURE_ROWS} 个输入设备未显示")
                break
            print(f" {index:<2} " + pad(mic.name, NAME_W))
    else:
        print(f"    输入设备 capture: {len(capture_mics)} 个，与输出设备无重名")

    # ---- 结论 ----
    print("=" * 78)
    default_ok = default_idx in sounding
    if default_ok:
        print("结论: OK  默认输出设备有声音，loopback 通路正常。")
        print("      若客户端仍无字幕，问题在识别/网络侧，不是音频采集。")
    elif sounding:
        hit = ", ".join(f"#{i}" for i in sounding)
        print(f"结论: [X] 默认输出设备(#{default_idx})全程静音，但 {hit} 有声音。")
        print("      客户端默认只录【默认输出设备】。请二选一：")
        print("      a) Windows 设置里把出声的那个设为默认输出设备；")
        print("      b) 在客户端 设置->输出设备 里手动选中出声的那个。")
        print("      注意 Win11 可给单个应用单独指定输出设备；Discord/VRChat 也有自己的输出设置。")
    else:
        print("结论: [X] 所有输出设备全程静音。请检查：")
        print("      - 采集这几秒里桌面确实在出声吗？（先放首歌再重跑）")
        print("      - 设备是否被独占：声音设置->该设备->高级->取消勾选『允许应用程序独占控制』")
        print("      - 上表若有 ERR 行，把该行一并发回")
    print("=" * 78)
    return 0


def main() -> int:
    setup_console()
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--seconds", type=float, default=8.0, help="采集时长，默认 8 秒")
    try:
        args = parser.parse_args()
    except SystemExit as exit_error:
        input("\n按回车退出...")
        return int(exit_error.code or 0)

    code = 1
    try:
        code = run(args)
    except KeyboardInterrupt:
        print("\n已取消")
    except Exception:
        print("\n[致命] 未预期的异常：")
        traceback.print_exc()
    try:
        input("\n按回车键退出 / Press Enter to exit ...")
    except Exception:
        pass
    return code


if __name__ == "__main__":
    sys.exit(main())
