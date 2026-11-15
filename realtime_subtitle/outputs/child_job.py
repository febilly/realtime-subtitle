"""Tie overlay child processes to the lifetime of the main app (Windows).

The subtitle overlay and the SteamVR overlay run as separate processes. A
graceful shutdown terminates them, but a crash, a hang during shutdown or
"End task" in Task Manager skips that code and used to leave them running in
the background.

Every overlay process is assigned to one Job Object created with
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE. Only this process holds a handle to the
job, so whenever it exits, for whatever reason, Windows closes the handle and
terminates every process still in the job.

On other platforms these functions do nothing; the overlay's own parent
watchdog (overlay_window.install_parent_watchdog) covers them.
"""

from __future__ import annotations

import sys
import threading

IS_WINDOWS = sys.platform == "win32"

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100

_lock = threading.Lock()
_job = None  # job handle, kept open for the life of this process
_api = None


def _load_api():
    global _api
    if _api is not None:
        return _api

    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    )
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    _api = (ctypes, kernel32, JOBOBJECT_EXTENDED_LIMIT_INFORMATION)
    return _api


def _get_job():
    """Create the kill-on-close job once; None when that is not possible."""
    global _job
    if _job is not None:
        return _job
    ctypes, kernel32, info_type = _load_api()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = info_type()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    ok = kernel32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info),
    )
    if not ok:
        kernel32.CloseHandle(job)
        return None
    _job = job
    return _job


def bind_to_app_lifetime(pid: int) -> bool:
    """Make Windows end process ``pid`` when this process exits.

    Returns False when the process could not be assigned (non-Windows, the
    process already exited, or the API refused); callers treat that as
    best-effort and keep their own shutdown handling.
    """
    if not IS_WINDOWS or not pid:
        return False
    try:
        with _lock:
            job = _get_job()
            if job is None:
                return False
            _ctypes, kernel32, _info = _load_api()
            process = kernel32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(pid))
            if not process:
                return False
            try:
                return bool(kernel32.AssignProcessToJobObject(job, process))
            finally:
                kernel32.CloseHandle(process)
    except Exception:
        return False
