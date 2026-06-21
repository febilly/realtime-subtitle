# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('static', 'static')]
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
hiddenimports += ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'shiboken6']

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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='RealtimeSubtitle',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='NONE',
)
