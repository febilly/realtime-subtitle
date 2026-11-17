# Linux 环境配置指南

本项目在 Linux 上默认使用 [pywebview](https://github.com/r0x0r/pywebview)（GTK + WebKit2）作为主窗口、[PySide6](https://doc.qt.io/qtforpython-6/) 作为桌面字幕悬浮窗，并通过 PipeWire / PulseAudio 采集系统环回与单应用音频。

> 以下步骤已在 Ubuntu 24.04 与 Manjaro / Arch（KDE Plasma Wayland + XWayland）实测；其他发行版包名可能略有差异。

---

## 1. 安装系统级依赖

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y \
    libgirepository-2.0-dev \
    libglib2.0-dev \
    libcairo2-dev \
    libgtk-3-dev \
    gir1.2-webkit2-4.1 \
    gir1.2-javascriptcoregtk-4.1 \
    libwebkit2gtk-4.1-0 \
    libopus0 \
    pulseaudio-utils \
    python3-dev \
    pkg-config \
    meson \
    cmake
```

### Arch / Manjaro

```bash
sudo pacman -S --needed \
    gobject-introspection \
    gtk3 \
    webkit2gtk-4.1 \
    opus \
    libpulse \
    pipewire-pulse \
    base-devel \
    pkgconf \
    meson
```

- `opus` / `libopus0`：供 Gemini / 音频传输层调用系统 `libopus.so.0`。
- `pulseaudio-utils` / `libpulse`（提供 `pactl` 与 `parec`）：供“按应用/窗口捕获音频”功能枚举和监听 PipeWire / PulseAudio 音频流；桌面整机环回依赖 PipeWire-Pulse 或 PulseAudio `.monitor` 源。

---

## 2. 安装 Python 依赖

`requirements.txt` 已内置 Linux 所需的 `PyGObject` 条件依赖与 Python 3.13+ 兼容版本标记，建议在虚拟环境中安装：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# 或使用 uv：
# uv venv && uv pip install -r requirements.txt
```

**常见问题：**

- **`ModuleNotFoundError: No module named 'gi'`**
  
  原因：`.venv` 是隔离环境，系统装的 `python3-gi` 不会自动暴露给它。
  
  解决：必须在虚拟环境里执行 `pip install PyGObject`。

- **`ERROR: Dependency 'girepository-2.0' is required but not found`**
  
  原因：缺少编译 PyGObject 所需的头文件和 `.pc` 文件。
  
  解决：安装 `libgirepository-2.0-dev`（Ubuntu/Debian）或 `gobject-introspection`（Arch/Manjaro）。

- **`ValueError: Namespace WebKit2 not available`**
  
  原因：GTK 的 WebKit2 GIR 绑定缺失。pywebview 需要通过 WebKit 渲染网页。
  
  解决：安装 `gir1.2-webkit2-4.1` 和 `libwebkit2gtk-4.1-0`（Ubuntu/Debian），或 `webkit2gtk-4.1`（Arch/Manjaro）。

---

## 3. 安装中文字体和 Emoji 字体

WebKit 默认不会渲染中文和 Emoji，需要安装字体：

```bash
# 完整 Noto 字体（推荐，覆盖中日韩和 emoji）
sudo apt install -y fonts-noto-cjk fonts-noto-color-emoji

# 或精简方案（仅中文 + emoji）
# sudo apt install -y fonts-wqy-zenhei fonts-noto-color-emoji

# 刷新字体缓存
fc-cache -fv
```

**常见问题：**

- **中文显示为方框或乱码**
  
  解决：安装 `fonts-noto-cjk` 或 `fonts-wqy-zenhei`。

- **Emoji 显示为方框**
  
  解决：安装 `fonts-noto-color-emoji`。

---

## 4. 图形环境与 Wayland / XWayland 说明

桌面 GUI 模式需要图形会话；在 **Wayland** 桌面（如 KDE Plasma / GNOME）下建议启用 **XWayland（存在 `$DISPLAY`）**：

- 当 `$DISPLAY` 可用且未显式覆盖 `GDK_BACKEND` / `QT_QPA_PLATFORM` 时，程序会自动走 X11/XWayland（`GDK_BACKEND=x11`、`QT_QPA_PLATFORM=xcb`），用于支持主窗口置顶与高分屏缩放、悬浮窗定位、`XShape` 鼠标穿透及全屏视频上层覆盖。
- 若需强制使用原生 Wayland，可手动设置环境变量 `GDK_BACKEND=wayland` 或 `QT_QPA_PLATFORM=wayland`（会受限于 Wayland 协议的置顶与定位约束）。

### WSL + RDP 场景

本项目实测在 **WSL2 + xrdp** 环境下可正常运行，音频通过 `RDP Sink` 捕获。确保：

1. RDP 会话已连接。
2. `DISPLAY` 环境变量已正确设置（通常为 `:10.0` 或类似值）。

### 无内置浏览器 / 远程访问场景

如果没有图形界面或不想打开 `pywebview` 窗口，可以使用 `--no-webview` 仅启动 HTTP / WebSocket 服务，再用系统浏览器访问提示的地址：

```bash
python server.py --no-webview
```

---

## 5. 快速检查清单

在启动前，确认以下命令均返回正常结果：

```bash
# 1. PyGObject 与 WebKit2 可用
python -c "import gi; gi.require_version('WebKit2', '4.1'); print('WebKit2 OK')"

# 2. Opus 共享库与单应用音频命令可用
python -c "from realtime_subtitle.audio import opus_native; opus_native._load_lib(); print('Opus OK')"
which pactl parec

# 3. 中文字体与 Emoji 字体已安装
fc-list :lang=zh | head -1
fc-list | grep -i emoji | head -1

# 4. 处于图形会话中（X11 / XWayland）
echo "$DISPLAY"
```

全部通过后，即可运行：

```bash
python server.py
# 若使用 uv：
uv run server.py
```

---

## 6. 参考链接

- [pywebview Linux 依赖文档](https://pywebview.flowrl.com/guide/installation.html#linux)
- [PyGObject 官方安装指南](https://pygobject.readthedocs.io/en/latest/getting_started.html)
- [Noto Fonts](https://fonts.google.com/noto)
