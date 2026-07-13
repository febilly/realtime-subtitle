"""Native Windows desktop shortcut creation and startup repair helpers."""

from __future__ import annotations

import contextlib
import ctypes
import os
import re
import sys
import uuid
from pathlib import Path


SHORTCUT_FILENAME = "Real-time Subtitle.lnk"
APP_DESCRIPTION = "Real-time Subtitle"
_APP_EXECUTABLE_RE = re.compile(
    r"^RealtimeSubtitle-hosted-v(?P<version>.+)\.exe$",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"^(?P<core>\d+(?:\.\d+)*)(?:[-_.]?(?P<pre>.*))?$")
_VERSION_TOKEN_RE = re.compile(r"\d+|[A-Za-z]+")


class DesktopShortcutError(RuntimeError):
    """Raised when Windows fails to inspect or update desktop shortcuts."""


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> "_GUID":
        raw = uuid.UUID(value).bytes_le
        return cls.from_buffer_copy(raw)


_CLSID_SHELL_LINK = _GUID.from_string("00021401-0000-0000-C000-000000000046")
_IID_ISHELL_LINK_W = _GUID.from_string("000214F9-0000-0000-C000-000000000046")
_IID_IPERSIST_FILE = _GUID.from_string("0000010B-0000-0000-C000-000000000046")
_CLSCTX_INPROC_SERVER = 0x1
_COINIT_APARTMENTTHREADED = 0x2
_RPC_E_CHANGED_MODE = 0x80010106
_STGM_READ = 0x0
_SLGP_RAWPATH = 0x4
_MAX_UNICODE_PATH = 32768
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


def current_executable_path() -> Path | None:
    """Return the packaged hosted-client executable, or None outside its Windows build."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable).resolve()
    if executable.suffix.lower() != ".exe" or executable_version(executable) is None:
        return None
    return executable


def is_supported() -> bool:
    return current_executable_path() is not None


def executable_version(path: str | Path) -> str | None:
    match = _APP_EXECUTABLE_RE.fullmatch(Path(path).name)
    return match.group("version") if match else None


def _parse_version(value: str) -> tuple[tuple[int, ...], tuple[int | str, ...] | None] | None:
    # SemVer build metadata does not affect precedence.
    precedence = str(value or "").strip().split("+", 1)[0]
    match = _VERSION_RE.fullmatch(precedence)
    if not match:
        return None
    core = tuple(int(part) for part in match.group("core").split("."))
    raw_pre = (match.group("pre") or "").strip("-_.")
    if not raw_pre:
        return core, None
    tokens: list[int | str] = []
    for token in _VERSION_TOKEN_RE.findall(raw_pre):
        tokens.append(int(token) if token.isdigit() else token.casefold())
    return (core, tuple(tokens)) if tokens else None


def compare_versions(left: str, right: str) -> int | None:
    """Compare version strings using SemVer-style numeric and prerelease precedence."""
    parsed_left = _parse_version(left)
    parsed_right = _parse_version(right)
    if parsed_left is None or parsed_right is None:
        return None
    left_core, left_pre = parsed_left
    right_core, right_pre = parsed_right
    core_length = max(len(left_core), len(right_core))
    padded_left = left_core + (0,) * (core_length - len(left_core))
    padded_right = right_core + (0,) * (core_length - len(right_core))
    if padded_left != padded_right:
        return 1 if padded_left > padded_right else -1
    if left_pre is None or right_pre is None:
        if left_pre is right_pre:
            return 0
        return 1 if left_pre is None else -1
    for left_token, right_token in zip(left_pre, right_pre):
        if left_token == right_token:
            continue
        if isinstance(left_token, int) and isinstance(right_token, int):
            return 1 if left_token > right_token else -1
        if isinstance(left_token, int) != isinstance(right_token, int):
            # SemVer: numeric prerelease identifiers have lower precedence.
            return -1 if isinstance(left_token, int) else 1
        return 1 if str(left_token) > str(right_token) else -1
    if len(left_pre) == len(right_pre):
        return 0
    return 1 if len(left_pre) > len(right_pre) else -1


def should_replace_target(current: Path, target: str | Path) -> bool:
    try:
        if current.resolve() == Path(target).resolve():
            return False
    except (OSError, RuntimeError):
        if os.path.normcase(os.path.abspath(current)) == os.path.normcase(os.path.abspath(target)):
            return False
    current_version = executable_version(current)
    target_version = executable_version(target)
    if current_version is None or target_version is None:
        return False
    comparison = compare_versions(current_version, target_version)
    # Equal versions at different paths follow the executable launched this time.
    return comparison is not None and comparison >= 0


def _desktop_path() -> Path:
    buffer = ctypes.create_unicode_buffer(_MAX_UNICODE_PATH)
    # CSIDL_DESKTOPDIRECTORY resolves localized and OneDrive-redirected desktops.
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buffer)
    if result != 0 or not buffer.value:
        raise DesktopShortcutError(f"Unable to resolve the Windows desktop (HRESULT {result})")
    return Path(buffer.value)


def _check_hresult(result: int, operation: str) -> None:
    if result < 0:
        code = result & 0xFFFFFFFF
        raise DesktopShortcutError(f"{operation} failed (HRESULT 0x{code:08X})")


def _com_method(interface: ctypes.c_void_p, index: int, *argument_types):
    vtable = ctypes.cast(
        interface,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ).contents
    prototype = _WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argument_types)
    return prototype(vtable[index])


@contextlib.contextmanager
def _com_initialized():
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole32.CoInitializeEx.restype = ctypes.c_long
    result = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    changed_mode = (result & 0xFFFFFFFF) == _RPC_E_CHANGED_MODE
    if result < 0 and not changed_mode:
        _check_hresult(result, "CoInitializeEx")
    try:
        yield
    finally:
        if result >= 0:
            ole32.CoUninitialize()


class _NativeShellLink:
    """Minimal ctypes wrapper around IShellLinkW and IPersistFile."""

    def __init__(self):
        self.shell_link = ctypes.c_void_p()
        self.persist_file = ctypes.c_void_p()
        ole32 = ctypes.windll.ole32
        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(_GUID),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.c_long
        result = ole32.CoCreateInstance(
            ctypes.byref(_CLSID_SHELL_LINK),
            None,
            _CLSCTX_INPROC_SERVER,
            ctypes.byref(_IID_ISHELL_LINK_W),
            ctypes.byref(self.shell_link),
        )
        _check_hresult(result, "CoCreateInstance(IShellLinkW)")
        try:
            query_interface = _com_method(
                self.shell_link,
                0,
                ctypes.POINTER(_GUID),
                ctypes.POINTER(ctypes.c_void_p),
            )
            result = query_interface(
                self.shell_link,
                ctypes.byref(_IID_IPERSIST_FILE),
                ctypes.byref(self.persist_file),
            )
            _check_hresult(result, "QueryInterface(IPersistFile)")
        except Exception:
            self.close()
            raise

    def _shell_call(self, index: int, operation: str, argument_types, *arguments) -> None:
        method = _com_method(self.shell_link, index, *argument_types)
        _check_hresult(method(self.shell_link, *arguments), operation)

    def _persist_call(self, index: int, operation: str, argument_types, *arguments) -> None:
        method = _com_method(self.persist_file, index, *argument_types)
        _check_hresult(method(self.persist_file, *arguments), operation)

    def load(self, shortcut_path: Path) -> None:
        self._persist_call(
            5,
            "IPersistFile.Load",
            (ctypes.c_wchar_p, ctypes.c_uint32),
            str(shortcut_path),
            _STGM_READ,
        )

    def target_path(self) -> str:
        buffer = ctypes.create_unicode_buffer(_MAX_UNICODE_PATH)
        self._shell_call(
            3,
            "IShellLinkW.GetPath",
            (ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32),
            buffer,
            len(buffer),
            None,
            _SLGP_RAWPATH,
        )
        return buffer.value

    def configure(self, executable: Path) -> None:
        target = str(executable)
        working_directory = str(executable.parent)
        self._shell_call(20, "IShellLinkW.SetPath", (ctypes.c_wchar_p,), target)
        self._shell_call(
            9,
            "IShellLinkW.SetWorkingDirectory",
            (ctypes.c_wchar_p,),
            working_directory,
        )
        self._shell_call(11, "IShellLinkW.SetArguments", (ctypes.c_wchar_p,), "")
        self._shell_call(
            17,
            "IShellLinkW.SetIconLocation",
            (ctypes.c_wchar_p, ctypes.c_int),
            target,
            0,
        )
        self._shell_call(
            7,
            "IShellLinkW.SetDescription",
            (ctypes.c_wchar_p,),
            APP_DESCRIPTION,
        )

    def save(self, shortcut_path: Path) -> None:
        self._persist_call(
            6,
            "IPersistFile.Save",
            (ctypes.c_wchar_p, ctypes.c_int),
            str(shortcut_path),
            1,
        )

    def close(self) -> None:
        for interface in (self.persist_file, self.shell_link):
            if interface and interface.value:
                release = _com_method(interface, 2)
                release(interface)
                interface.value = None

    def __enter__(self) -> "_NativeShellLink":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def _matching_shortcuts() -> list[dict[str, str]]:
    desktop = _desktop_path()
    matches: list[dict[str, str]] = []
    with _com_initialized():
        for shortcut_path in desktop.glob("*.lnk"):
            try:
                with _NativeShellLink() as shortcut:
                    shortcut.load(shortcut_path)
                    target = shortcut.target_path()
                is_canonical = shortcut_path.name.casefold() == SHORTCUT_FILENAME.casefold()
                is_application = executable_version(target) is not None
                if is_canonical or is_application:
                    matches.append({"path": str(shortcut_path), "target": target})
            except DesktopShortcutError:
                # A malformed or inaccessible unrelated shortcut must not block startup.
                continue
    return matches


def _write_shortcut(shortcut_path: Path, executable: Path, *, load_existing: bool) -> None:
    with _com_initialized(), _NativeShellLink() as shortcut:
        if load_existing:
            shortcut.load(shortcut_path)
        shortcut.configure(executable)
        shortcut.save(shortcut_path)


def get_shortcut_status() -> dict:
    executable = current_executable_path()
    if executable is None:
        return {"available": False, "exists": False, "matched": 0}
    matches = _matching_shortcuts()
    return {"available": True, "exists": bool(matches), "matched": len(matches)}


def create_desktop_shortcut() -> dict:
    executable = current_executable_path()
    if executable is None:
        return {"available": False, "created": False, "exists": False}
    shortcut_path = _desktop_path() / SHORTCUT_FILENAME
    _write_shortcut(shortcut_path, executable, load_existing=shortcut_path.exists())
    return {"available": True, "created": True, "exists": True}


def repair_existing_shortcuts() -> dict:
    """Update matching shortcuts when this version is equal/newer; never create or downgrade."""
    executable = current_executable_path()
    if executable is None:
        return {"available": False, "exists": False, "matched": 0, "updated": 0}
    matches = _matching_shortcuts()
    updated = 0
    for match in matches:
        if not should_replace_target(executable, match["target"]):
            continue
        _write_shortcut(Path(match["path"]), executable, load_existing=True)
        updated += 1
    return {
        "available": True,
        "exists": bool(matches),
        "matched": len(matches),
        "updated": updated,
    }
