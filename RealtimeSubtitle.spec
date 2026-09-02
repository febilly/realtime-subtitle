# -*- mode: python ; coding: utf-8 -*-
import os
import re

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

datas = [('static', 'static'), ('ACKNOWLEDGMENTS.md', '.')]
binaries = []
# hiddenimports = ['websockets.sync.client', 'aiohttp', 'soundcard', 'numpy', 'dotenv', 'locale', 'pythonosc', 'streamlink', 'webview']
hiddenimports = ['websockets.sync.client', 'aiohttp', 'soundcard', 'numpy', 'dotenv', 'locale', 'pythonosc', 'webview',
                 'provider_setup', 'soniox_session', 'gemini_session', 'soniox_client', 'gemini_client',
                 'soniox_key_setup', 'gemini_key_setup',
                 'overlay_window']
tmp_ret = collect_all('soundcard')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('aiohttp')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('websockets')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('pythonosc')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('ten_vad')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
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

# 不需要的模块；排除后其对应的 Qt6*.dll / 插件不会被收集。
# 重点：QtWebEngine* 必须排除，否则 webview.platforms.qt 会把整套
# WebEngine（上百 MB）拖进来——主窗口在 Windows 上用的是 EdgeChromium，
# 不走 Qt 后端。
excludes = [
    # 其它 GUI 绑定 / 无关大库
    'PyQt5', 'PyQt6', 'PySide2', 'tkinter', 'matplotlib',
    # pywebview 用不到的后端（Windows 走 edgechromium/winforms）
    'webview.platforms.qt', 'webview.platforms.gtk', 'webview.platforms.cocoa',
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
]


a = Analysis(
    ['server.py'],
    pathex=[],
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
)


def _keep_qt(entry):
    dest = str(entry[0]).replace('\\', '/').lower()
    if any(s in dest for s in _drop_substr):
        return False
    if dest.rsplit('/', 1)[-1] in _drop_basename:
        return False
    return True


a.binaries = [e for e in a.binaries if _keep_qt(e)]
a.datas = [e for e in a.datas if _keep_qt(e)]

pyz = PYZ(a.pure)


# 未签名 + 无版本资源的 exe 会被杀软的信誉/启发式引擎当成高危样本。签名是根治
# 手段，但至少先把 VERSIONINFO 补上——版本号直接从 config.py 读，避免两处手工同步。
def _client_version():
    source = os.path.join(SPECPATH, 'config.py')
    with open(source, encoding='utf-8') as fh:
        match = re.search(r'^CLIENT_VERSION\s*=\s*["\']([^"\']+)["\']', fh.read(), re.M)
    if not match:
        raise SystemExit(f'CLIENT_VERSION not found in {source}')
    return match.group(1)


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
    icon='NONE',
    version=version_info,
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
