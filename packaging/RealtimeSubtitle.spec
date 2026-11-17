# -*- mode: python ; coding: utf-8 -*-
import ctypes.util
import os
import re
import subprocess
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

IS_WINDOWS = sys.platform == 'win32'
IS_LINUX = sys.platform.startswith('linux')

if IS_WINDOWS:
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

# This spec lives in packaging/; every source path below is relative to the
# repository root one level up.
ROOT = os.path.dirname(SPECPATH)
_LOCAL_INFERENCE = 'realtime_subtitle/local_inference'


def _src(path):
    return os.path.join(ROOT, path)


datas = [(_src('static'), 'static'), (_src('resources'), 'resources'), (_src('licenses'), 'licenses'),
    (_src('ACKNOWLEDGMENTS.md'), '.'), (_src('THIRD_PARTY_NOTICES.md'), '.'), (_src('LICENSE'), '.'),
    # Vendored llama.cpp ctypes bindings (qwen_asr_gguf/inference/llama.py), kept as files.
    (_src(f'{_LOCAL_INFERENCE}/vendor/qwen_asr_gguf'), f'{_LOCAL_INFERENCE}/vendor/qwen_asr_gguf'),
    # FireRed VAD streaming ONNX (~2.3 MB): silence sleep, stream rollover and
    # local ASR segmentation.
    (_src(f'{_LOCAL_INFERENCE}/models/firered_vad'), f'{_LOCAL_INFERENCE}/models/firered_vad'),
]
binaries = []
# hiddenimports = ['websockets.sync.client', 'aiohttp', 'soundcard', 'numpy', 'dotenv', 'locale', 'pythonosc', 'streamlink', 'webview']
# App modules are listed by package so lazily imported ones (sessions picked at
# runtime, overlays started with --run-overlay, local inference) are bundled.
hiddenimports = ['websockets.sync.client', 'aiohttp', 'soundcard', 'numpy', 'dotenv', 'locale', 'pythonosc', 'webview',
                 'onnxruntime', 'kaldi_native_fbank',
                 'kaldi_native_fbank.lib._kaldi_native_fbank',
                 'gguf', 'gguf.constants', 'openvr']
hiddenimports += collect_submodules(
    'realtime_subtitle',
    # The vendored llama.cpp bindings are shipped as data files above.
    filter=lambda name: not name.startswith(f'{_LOCAL_INFERENCE.replace("/", ".")}.vendor'),
)
tmp_ret = collect_all('soundcard')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('aiohttp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('websockets')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# Opus 编码只依赖独立的 libopus（Windows 用内置 libopus/win64/opus.dll，
# Linux 打包系统 libopus.so.0），由 realtime_subtitle/audio/opus_native.py
# 在运行时按 frozen 路径加载；不再打包 PyAV 整套 FFmpeg DLL。
def _find_linux_opus():
    candidates = [
        '/lib/x86_64-linux-gnu/libopus.so.0',
        '/usr/lib/x86_64-linux-gnu/libopus.so.0',
        '/lib/aarch64-linux-gnu/libopus.so.0',
        '/usr/lib/aarch64-linux-gnu/libopus.so.0',
        '/usr/lib64/libopus.so.0',
        '/usr/lib/libopus.so.0',
    ]
    found = ctypes.util.find_library('opus')
    if found and os.path.isabs(found):
        candidates.insert(0, found)
    try:
        out = subprocess.check_output(['ldconfig', '-p'], text=True, stderr=subprocess.DEVNULL)
        for line in out.splitlines():
            if 'libopus.so.0' in line and '=>' in line:
                candidates.insert(0, line.split('=>', 1)[1].strip())
    except Exception:
        pass
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.realpath(path)
    raise SystemExit('libopus.so.0 not found; install libopus0 (Ubuntu/Debian) or opus (Arch)')


if IS_WINDOWS:
    binaries += [(_src('libopus/win64/opus.dll'), '.')]
elif IS_LINUX:
    binaries += [(_find_linux_opus(), 'libopus.so.0')]

# SteamVR 字幕悬浮窗：pyopenvr 用 importlib.resources 在 openvr 包目录里找
# 对应平台的 libopenvr_api 动态库，所以要放进 openvr/ 子目录。只带当前平台 64 位库，
# 其余平台的库以及 OpenGL 示例代码都用不到。
# 构建环境缺 openvr 时直接报错：否则产物会悄悄丢掉 VR 字幕（主界面不显示 VR 按钮）。
import importlib.util as _ilu
_openvr_spec = _ilu.find_spec('openvr')
if _openvr_spec is None:
    raise SystemExit('openvr is not installed; run "pip install -r requirements.txt" '
                     'so the build includes the SteamVR subtitle overlay')
_openvr_lib_name = 'libopenvr_api_64.dll' if IS_WINDOWS else 'libopenvr_api_64.so'
_openvr_lib = os.path.join(os.path.dirname(_openvr_spec.origin), _openvr_lib_name)
if not os.path.isfile(_openvr_lib):
    raise SystemExit(f'OpenVR runtime not found: {_openvr_lib}')
binaries += [(_openvr_lib, 'openvr')]
excludes_openvr = ['openvr.glframework', 'openvr.gl_renderer',
                   'openvr.color_cube_actor', 'openvr.tracked_devices_actor']
tmp_ret = collect_all('pythonosc')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# sherpa_onnx（语义边界 A/B sidecar，requirements-semantic-boundary-scout.txt）
# 默认关闭、只在开发环境用，故意不打包：否则产物大小取决于构建机装没装它。
# tmp_ret = collect_all('streamlink')
# datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# 原生字幕悬浮窗（PySide6）：只用 QtCore/QtGui/QtWidgets。
# 不能用 collect_all('PySide6') —— 它会把所有 Qt 模块/DLL/插件全部打进来，
# 而 exclude_datas 只过滤数据文件，挡不住庞大的 Qt6*.dll。
# 这里只声明实际用到的子模块，交给 PyInstaller 自带的 PySide6 hook 按需
# 收集依赖（平台/样式/图像格式插件等），其余可选模块统一在 excludes 中排除。
hiddenimports += ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtSvg', 'shiboken6']
if IS_LINUX:
    hiddenimports += [
        'gi', 'gi.repository.Gtk', 'gi.repository.Gdk', 'gi.repository.WebKit2',
        'gi.repository.GLib', 'gi.repository.Gio', 'gi.repository.GObject',
    ]

# 不需要的模块；排除后其对应的 Qt6*.dll / 插件不会被收集。
# 重点：QtWebEngine* 必须排除，否则 webview.platforms.qt 会把整套
# WebEngine（上百 MB）拖进来——主窗口在 Windows 上用 EdgeChromium，在 Linux 上用 GTK。
_webview_excludes = (
    ['webview.platforms.qt', 'webview.platforms.gtk', 'webview.platforms.cocoa']
    if IS_WINDOWS
    else [
        'webview.platforms.qt', 'webview.platforms.winforms', 'webview.platforms.edgechromium',
        'webview.platforms.cef', 'webview.platforms.mshtml', 'webview.platforms.android',
        'webview.platforms.cocoa',
    ]
)
excludes = [
    # 其它 GUI 绑定 / 无关大库
    'PyQt5', 'PyQt6', 'PySide2', 'tkinter', 'matplotlib',
    *_webview_excludes,
    # PySide6 中体积庞大且未使用的可选模块
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
    'PySide6.QtWebChannel', 'PySide6.QtWebSockets', 'PySide6.QtWebView',
    'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuick3D',
    'PySide6.QtQuickWidgets', 'PySide6.QtQuickControls2',
    'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtGraphs',
    'PySide6.Qt3DCore', 'PySide6.Qt3DRender', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic',
    'PySide6.Qt3DAnimation', 'PySide6.Qt3DExtras',
    'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PySide6.QtSpatialAudio',
    'PySide6.QtDesigner', 'PySide6.QtUiTools', 'PySide6.QtHelp', 'PySide6.QtTest',
    'PySide6.QtSql', 'PySide6.QtNetworkAuth', 'PySide6.QtBluetooth', 'PySide6.QtNfc',
    'PySide6.QtPositioning', 'PySide6.QtLocation', 'PySide6.QtSerialPort', 'PySide6.QtSerialBus',
    'PySide6.QtSensors', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets',
    'PySide6.QtPrintSupport', 'PySide6.QtConcurrent', 'PySide6.QtRemoteObjects',
    'PySide6.QtScxml', 'PySide6.QtStateMachine', 'PySide6.QtTextToSpeech',
    'PySide6.QtSvgWidgets', 'PySide6.QtXml', 'PySide6.QtDBus',
    # 构建期工具链，运行时用不到，此前被打进产物白占 240 个模块。
    # PyInstaller 自己是被 soundcard/__pyinstaller/conftest.py 拖进来的
    # （那文件 import PyInstaller.utils.conftest），PyInstaller 又依赖 setuptools。
    # 注意 'distutils' 不能排除：PyInstaller 会把它别名到 setuptools 内置的那份，
    # 排除后模块图会报 "Target module distutils already imported as ExcludedModule"。
    'setuptools', 'pkg_resources', 'PyInstaller',
] + excludes_openvr


a = Analysis(
    [_src('server.py')],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
# PySide6 的 QtGui hook 会自动收集一批 Qt 插件，其中两个会拖入庞大依赖：
#   * platforminputcontexts/qtvirtualkeyboardplugin → Qt6Quick/Qt6Qml*/Qt6VirtualKeyboard
#   * imageformats/qpdf                             → Qt6Pdf
# 字幕悬浮窗用不到虚拟键盘和 PDF 缩略图，excludes 挡不住插件，这里按路径剔除
# 这些插件及随之失去用途的 DLL。
_drop_substr = (
    'plugins/platforminputcontexts',  # 虚拟键盘输入法插件
    'qtvirtualkeyboard',
    'imageformats/qpdf',              # PDF imageformat 插件
    # collect_all() 会把包里的 __pyinstaller/ 一并收走，那是给 PyInstaller 用的
    # 打包钩子和测试辅助，运行时毫无用处。soundcard 那份的 conftest.py 还
    # `import PyInstaller.utils.conftest`，正是整条构建工具链被拖进产物的源头。
    '__pyinstaller/',
)
_drop_basename = (
    'qt6quick.dll', 'qt6qml.dll', 'qt6qmlmodels.dll', 'qt6qmlworkerscript.dll',
    'qt6qmlmeta.dll', 'qt6virtualkeyboard.dll', 'qt6pdf.dll',
    # Qt 的软件 OpenGL 回退渲染器（~20MB）。字幕悬浮窗是纯 QWidget + QPainter
    # 光栅渲染，且已移除 QtQuick/QML，用不到它。
    'opengl32sw.dll',
    'libqt6quick.so.6', 'libqt6qml.so.6', 'libqt6qmlmodels.so.6',
    'libqt6qmlworkerscript.so.6', 'libqt6qmlmeta.so.6',
    'libqt6virtualkeyboard.so.6', 'libqt6pdf.so.6',
)
_linux_host_lib_prefixes = (
    'libc.so', 'libm.so', 'libdl.so', 'librt.so', 'libpthread.so',
    'libstdc++.so', 'libgcc_s.so',
    'libglib-2.0.so', 'libgobject-2.0.so', 'libgio-2.0.so',
    'libgmodule-2.0.so', 'libgthread-2.0.so', 'libgirepository-',
    'libgtk-3.so', 'libgdk-3.so', 'libgdk_pixbuf-2.0.so',
    'libpango', 'libcairo', 'libharfbuzz', 'libfreetype', 'libfontconfig',
    'libexpat.so', 'libgl.so', 'libglx.so', 'libgldispatch.so', 'libegl.so',
    'libvulkan.so', 'libdrm.so', 'libgbm.so', 'libwayland-',
    'libx11.so', 'libx11-xcb.so', 'libxext.so',
    'libxcb.so', 'libxcb-glx.so', 'libxcb-dri', 'libxcb-shm.so',
    'libpulse', 'libpipewire', 'libasound', 'libdbus-1.so', 'libsystemd.so', 'libudev.so',
)


def _keep_qt(entry):
    dest = str(entry[0]).replace('\\', '/').lower()
    if any(s in dest for s in _drop_substr):
        return False
    base = dest.rsplit('/', 1)[-1]
    if base in _drop_basename:
        return False
    if IS_LINUX:
        # 让 AppImage 运行时使用宿主系统的基础/GTK/WebKit/Audio 库，避免跨发行版 ABI 冲突；
        # PySide6 自带的 Qt/ICU 库保留。
        if dest.startswith(('gi_typelibs/', 'share/gir-1.0/', 'girepository-1.0/')):
            return False
        if not dest.startswith('pyside6/') and base.startswith(_linux_host_lib_prefixes):
            return False
        return True
    # Qt on Windows uses the OS ICU API (System32\icu.dll). PATH may contain
    # Poppler/Conda's incompatible ICU 78 (icuuc.dll, icudt78.dll, ...);
    # bundling it shadows System32 and makes PySide6.QtCore fail to import,
    # while its data file alone is ~33 MB uncompressed and nothing in the
    # bundle links against it once the Poppler icuuc.dll is dropped.
    if base.startswith('icu') and base.endswith('.dll'):
        return False
    return True


a.binaries = [e for e in a.binaries if _keep_qt(e)]
a.datas = [e for e in a.datas if _keep_qt(e)]

pyz = PYZ(a.pure)


# 未签名 + 无版本资源的 exe 会被杀软的信誉/启发式引擎当成高危样本。签名是根治
# 手段，但至少先把 VERSIONINFO 补上——版本号直接从 realtime_subtitle/core/config.py 读，避免两处手工同步。
def _client_version():
    source = _src('realtime_subtitle/core/config.py')
    with open(source, encoding='utf-8') as fh:
        match = re.search(r'^CLIENT_VERSION\s*=\s*["\']([^"\']+)["\']', fh.read(), re.M)
    if not match:
        raise SystemExit(f'CLIENT_VERSION not found in {source}')
    return match.group(1)


if IS_WINDOWS:
    _version = _client_version()
    # FixedFileInfo 只接受 4 个整数；"4.7.1" -> (4, 7, 1, 0)，预发布后缀（如 "-rc1"）
    # 只保留数字部分，字符串表里仍然写完整版本号。
    _numeric = [int(part) for part in re.findall(r'\d+', _version)][:4]
    _numeric += [0] * (4 - len(_numeric))
    _version_tuple = tuple(_numeric)

    version_info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=_version_tuple,
            prodvers=_version_tuple,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,      # VOS_NT_WINDOWS32
            fileType=0x1,    # VFT_APP
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                StringTable(
                    '040904B0',  # US English, Unicode
                    [
                        StringStruct('CompanyName', 'febilly'),
                        StringStruct('FileDescription', 'Realtime Subtitle'),
                        StringStruct('FileVersion', _version),
                        StringStruct('InternalName', 'RealtimeSubtitle'),
                        StringStruct(
                            'LegalCopyright',
                            'Copyright (C) febilly. Licensed under AGPL-3.0.',
                        ),
                        StringStruct('OriginalFilename', 'RealtimeSubtitle.exe'),
                        StringStruct('ProductName', 'Realtime Subtitle'),
                        StringStruct('ProductVersion', _version),
                        StringStruct(
                            'Comments',
                            'https://github.com/febilly/realtime-subtitle',
                        ),
                    ],
                )
            ]),
            VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
        ],
    )
else:
    version_info = None

# 单文件 vs 目录版。默认单文件（CI 走这条），设 RTS_ONEDIR=1 则产出目录版，
# 由调用方压成 zip 分发。
#
# 目录版对杀软友好得多：单文件 exe 每次启动都要把上百个 DLL 释放到
# %TEMP%\_MEIxxxxx 再加载，这正是 dropper 的标准行为，行为启发式引擎对它很敏感。
# 目录版没有这一步，DLL 直接从自己的 _internal 目录加载。
ONEDIR = os.environ.get('RTS_ONEDIR') == '1'

# 产物名。目录版由调用方传入 hosted-v<版本> 这样的最终名字，让 PyInstaller 直接
# 写到位——构建完再去 ren/move 会撞上杀软实时扫描占用文件，报 "Access is denied"。
DIST_NAME = os.environ.get('RTS_DIST_NAME') or 'RealtimeSubtitle'

# EXE 的位置参数是若干 TOC；目录版把二进制和数据留给 COLLECT 收集。
_exe_toc = [pyz, a.scripts] + ([] if ONEDIR else [a.binaries, a.datas]) + [[]]

exe = EXE(
    *_exe_toc,
    exclude_binaries=ONEDIR,
    name=DIST_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 不要开 UPX：加壳是杀软最经典的静态特征之一，会显著抬高误报率。
    # （CI runner 上本来就没装 UPX，此前的 upx=True 一直是空操作。）
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_src('static/app.ico') if IS_WINDOWS else None,
    version=version_info if IS_WINDOWS else None,
)

if ONEDIR:
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=DIST_NAME,
    )
