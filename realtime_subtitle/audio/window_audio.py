"""Windows application loopback, using the system WASAPI API (no extra DLLs).

Captures the selected window's process tree, not its pixels or other apps.
Requires Windows build 20348+. See Microsoft's ApplicationLoopback sample:
https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os
import sys
import threading
import time
import uuid

import numpy as np

# Native COM references are invisible to Python's GC. Keep callback thunks
# alive until the final Release, including callbacks that arrive after timeout.
_activations = {}


def supported() -> bool:
    return sys.platform == "win32" and sys.getwindowsversion().build >= 20348


def _libraries():
    user = C.WinDLL("user32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    user.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    user.IsWindowVisible.argtypes = [W.HWND]
    user.GetWindowTextLengthW.argtypes = [W.HWND]
    user.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)]
    kernel.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [W.HANDLE]
    return user, kernel


def _process_identity(kernel, pid):
    handle = kernel.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        name = C.create_unicode_buffer(32768)
        size = W.DWORD(len(name))
        times = [W.FILETIME() for _ in range(4)]
        if not kernel.QueryFullProcessImageNameW(handle, 0, name, C.byref(size)):
            return None
        if not kernel.GetProcessTimes(handle, *(C.byref(t) for t in times)):
            return None
        created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return {"executable": name.value, "created": str(created)}
    finally:
        kernel.CloseHandle(handle)


def list_windows() -> list[dict]:
    if not supported():
        return []
    user, kernel = _libraries()
    result = []
    callback_type = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)

    @callback_type
    def visit(hwnd, _):
        if not user.IsWindowVisible(hwnd):
            return True
        length = user.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        title = C.create_unicode_buffer(length + 1)
        user.GetWindowTextW(hwnd, title, len(title))
        pid = W.DWORD()
        user.GetWindowThreadProcessId(hwnd, C.byref(pid))
        identity = _process_identity(kernel, pid.value)
        if identity and pid.value != os.getpid() and title.value.strip():
            result.append({"hwnd": str(hwnd), "pid": pid.value,
                           "title": title.value, **identity})
        return True

    user.EnumWindows.argtypes = [callback_type, W.LPARAM]
    user.EnumWindows(visit, 0)
    return sorted(result, key=lambda item: item["title"].casefold())


def resolve_window(selection, *, restore=False):
    """Validate a live identity, or restore by path/title then by path alone."""
    if not isinstance(selection, dict):
        return None
    windows = list_windows()
    if restore:
        executable = str(selection.get("executable", "")).casefold()
        if not executable:
            return None
        candidates = [item for item in windows if item["executable"].casefold() == executable]
        return next((item for item in candidates if item["title"] == selection.get("title")),
                    candidates[0] if candidates else None)
    return next((item for item in windows
                 if all(str(item[key]) == str(selection.get(key, ""))
                        for key in ("hwnd", "pid", "created", "executable"))), None)


class GUID(C.Structure):
    _fields_ = [("data", C.c_ubyte * 16)]

    def __init__(self, value):
        super().__init__((C.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))


def _call(pointer, index, result_type, arg_types, *args):
    table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    function = C.WINFUNCTYPE(result_type, C.c_void_p, *arg_types)(table[index])
    return function(pointer, *args)


def _check(hr):
    if hr < 0:
        raise OSError(f"WASAPI HRESULT 0x{hr & 0xffffffff:08X}")


class ProcessRecorder:
    """SoundCard-compatible context/record contract, 16 kHz mono float32.

    All COM objects are created, polled and released on the capture thread.
    Empty packets are paced silence. A lost process never falls back to the
    full desktop mix.
    """

    def __init__(self, selection, sample_rate=16000, stop=None):
        self.selection = selection
        self.sample_rate = sample_rate
        self.stop = stop or (lambda: False)
        self.client = C.c_void_p()
        self.capture = C.c_void_p()
        self.operation = C.c_void_p()
        self.pending = bytearray()
        self.com_initialized = False
        self._activation = None

    def __enter__(self):
        if not supported() or not resolve_window(self.selection):
            raise RuntimeError("Selected audio window is unavailable")
        self.ole = C.WinDLL("ole32")
        self.ole.CoInitializeEx.argtypes = [C.c_void_p, W.DWORD]
        _check(self.ole.CoInitializeEx(None, 0))  # MTA
        self.com_initialized = True
        try:
            self._activate()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _activate(self):
        # PROPVARIANT VT_BLOB layout is pointer-aligned on both x86 and x64.
        class Blob(C.Structure):
            _fields_ = [("size", W.ULONG), ("data", C.c_void_p)]

        class Variant(C.Structure):
            _fields_ = [("vt", W.WORD), ("reserved", W.WORD * 3), ("blob", Blob)]

        class Params(C.Structure):
            _fields_ = [("type", W.DWORD), ("pid", W.DWORD), ("mode", W.DWORD)]

        params = Params(1, int(self.selection["pid"]), 0)  # include process tree
        variant = Variant(65, (W.WORD * 3)(), Blob(C.sizeof(params), C.addressof(params)))
        completed = threading.Event()
        result = {"hr": -1}
        holder = {}
        completion_lock = threading.Lock()
        query_type = C.WINFUNCTYPE(C.c_long, C.c_void_p, C.POINTER(GUID), C.POINTER(C.c_void_p))
        ref_type = C.WINFUNCTYPE(W.ULONG, C.c_void_p)
        done_type = C.WINFUNCTYPE(C.c_long, C.c_void_p, C.c_void_p)
        accepted = {uuid.UUID(value).bytes_le for value in (
            "00000000-0000-0000-C000-000000000046",  # IUnknown
            "41D949AB-9862-444A-80F6-C261334DA5EB",  # completion handler
            "94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90",  # IAgileObject
        )}

        @query_type
        def query(this, iid, out):
            out[0] = None
            if C.string_at(iid, 16) not in accepted:
                return -2147467262  # E_NOINTERFACE
            out[0] = this
            holder["refs"] += 1
            return 0

        @ref_type
        def add_ref(_):
            holder["refs"] += 1
            return holder["refs"]

        @ref_type
        def release(_):
            holder["refs"] -= 1
            if holder["refs"] == 0:
                _activations.pop(id(holder), None)
            return holder["refs"]

        @done_type
        def done(_, operation):
            try:
                hr = C.c_long()
                client = C.c_void_p()
                result["hr"] = _call(operation, 3, C.c_long,
                                     [C.POINTER(C.c_long), C.POINTER(C.c_void_p)],
                                     C.byref(hr), C.byref(client))
                if result["hr"] >= 0:
                    result["hr"] = hr.value
                with completion_lock:
                    if holder.get("abandoned"):
                        if client:
                            _call(client, 2, W.ULONG, [])
                    else:
                        self.client = client
                    completed.set()
            finally:
                completed.set()
            return 0

        table = (C.c_void_p * 4)(*(C.cast(fn, C.c_void_p).value for fn in (query, add_ref, release, done)))
        handler = C.pointer(C.cast(table, C.POINTER(C.c_void_p)))
        holder.update(refs=1, callbacks=(query, add_ref, release, done), table=table,
                      handler=handler, params=params, variant=variant)
        self._activation = holder
        _activations[id(holder)] = holder
        activate = C.WinDLL("Mmdevapi").ActivateAudioInterfaceAsync
        activate.argtypes = [W.LPCWSTR, C.POINTER(GUID), C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)]
        _check(activate("VAD\\Process_Loopback", C.byref(GUID("1CB9AD4C-DBFA-4C32-B178-C2F568A703B2")),
                        C.byref(variant), handler, C.byref(self.operation)))
        if not completed.wait(5):
            with completion_lock:
                if not completed.is_set():
                    holder["abandoned"] = True
                    raise TimeoutError("Windows application audio activation timed out")
        _check(result["hr"])

        class WaveFormat(C.Structure):
            _pack_ = 1
            _fields_ = [("tag", W.WORD), ("channels", W.WORD), ("rate", W.DWORD),
                        ("bytes_per_second", W.DWORD), ("align", W.WORD),
                        ("bits", W.WORD), ("extra", W.WORD)]

        fmt = WaveFormat(1, 1, self.sample_rate, self.sample_rate * 2, 2, 16, 0)
        _check(_call(self.client, 3, C.c_long,
                     [C.c_int, W.DWORD, C.c_longlong, C.c_longlong, C.c_void_p, C.c_void_p],
                     0, 0x80020000, 0, 0, C.byref(fmt), None))  # AUTOCONVERTPCM | LOOPBACK
        _check(_call(self.client, 14, C.c_long, [C.POINTER(GUID), C.POINTER(C.c_void_p)],
                     C.byref(GUID("C8ADBD64-E71E-48A0-A4DE-185C395CD317")), C.byref(self.capture)))
        _check(_call(self.client, 10, C.c_long, []))  # Start

    def record(self, numframes):
        needed = numframes * 2
        deadline = time.monotonic() + numframes / self.sample_rate
        while len(self.pending) < needed and not self.stop():
            count = W.UINT()
            _check(_call(self.capture, 5, C.c_long, [C.POINTER(W.UINT)], C.byref(count)))
            if count.value:
                data, frames, flags = C.c_void_p(), W.UINT(), W.DWORD()
                _check(_call(self.capture, 3, C.c_long,
                             [C.POINTER(C.c_void_p), C.POINTER(W.UINT), C.POINTER(W.DWORD), C.c_void_p, C.c_void_p],
                             C.byref(data), C.byref(frames), C.byref(flags), None, None))
                try:
                    self.pending.extend(bytes(frames.value * 2) if flags.value & 2
                                        else C.string_at(data, frames.value * 2))
                finally:
                    _check(_call(self.capture, 4, C.c_long, [W.UINT], frames.value))
            elif time.monotonic() >= deadline:
                break
            else:
                time.sleep(0.005)
        payload = self.pending[:needed]
        del self.pending[:needed]
        payload.extend(bytes(needed - len(payload)))
        return np.frombuffer(payload, dtype=np.int16).astype(np.float32) / 32768.0

    def __exit__(self, *_):
        if self.client:
            _call(self.client, 11, C.c_long, [])  # Stop
        for pointer in (self.capture, self.client, self.operation):
            if pointer:
                _call(pointer, 2, W.ULONG, [])
                pointer.value = None
        if self._activation:
            self._activation["callbacks"][2](None)  # release our handler reference
        self._activation = None
        if self.com_initialized:
            self.ole.CoUninitialize()
            self.com_initialized = False
