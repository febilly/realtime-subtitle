# Linux 环境配置指南

本项目的 UI 基于 [pywebview](https://github.com/r0x0r/pywebview)，在 Linux 上运行时需要额外的系统级 GUI 和字体依赖。以下是在 Ubuntu/Debian 系发行版上的完整配置步骤，以及我们踩过的坑。

> 本文档基于 Ubuntu 24.04 (Noble Numbat) 实测整理，其他发行版包名可能略有差异。

---

## 1. 安装 Python 依赖

```bash
pip install -r requirements.txt
# 或
uv pip install -r requirements.txt
```

---

## 2. 安装系统级 GUI 依赖

### 2.1 GTK 和基础构建工具

pywebview 默认优先使用 GTK 后端，需要以下系统包：

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
    python3-dev \
    pkg-config \
    meson \
    cmake
```

### 2.2 在虚拟环境中安装 PyGObject

系统包安装完成后，需要在项目的 Python 虚拟环境里编译安装 `PyGObject`：

```bash
source .venv/bin/activate
pip install PyGObject
# 或
uv pip install PyGObject
```

**常见问题：**

- **`ModuleNotFoundError: No module named 'gi'`**
  
  原因：`.venv` 是隔离环境，系统装的 `python3-gi` 不会自动暴露给它。
  
  解决：必须在虚拟环境里执行 `pip install PyGObject`。

- **`ERROR: Dependency 'girepository-2.0' is required but not found`**
  
  原因：缺少编译 PyGObject 所需的头文件和 `.pc` 文件。
  
  解决：安装 `libgirepository-2.0-dev`（见 2.1）。

- **`ValueError: Namespace WebKit2 not available`**
  
  原因：GTK 的 WebKit2 GIR 绑定缺失。pywebview 需要通过 WebKit 渲染网页。
  
  解决：安装 `gir1.2-webkit2-4.1` 和 `libwebkit2gtk-4.1-0`（见 2.1）。

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

## 4. 图形环境要求

pywebview 必须运行在具有图形会话的环境中。如果你是通过 **纯 SSH** 连接远程服务器，没有 `$DISPLAY`，程序会在创建窗口时失败。

### WSL + RDP 场景

本项目实测在 **WSL2 + xrdp** 环境下可正常运行，音频通过 `RDP Sink` 捕获。

确保：

1. RDP 会话已连接。
2. `DISPLAY` 环境变量已正确设置（通常为 `:10.0` 或类似值）。

### 纯 CLI / 无头服务器

如果你没有图形环境，且只需要后台服务，目前项目没有内置 `--headless` 模式。一个替代方案是：

- 单独运行 `web_server.py` 提供 HTTP 接口（需确认是否支持独立运行）。
- 或者通过 X11 转发 / VNC / RDP 提供图形会话。

---

## 5. 快速检查清单

在运行 `python server.py` 之前，确认以下命令均返回正常结果：

```bash
# 1. Python 虚拟环境已激活
which python

# 2. PyGObject 已正确安装
python -c "import gi; print(gi.__version__)"

# 3. WebKit2 命名空间可用
python -c "import gi; gi.require_version('WebKit2', '4.1'); print('WebKit2 OK')"

# 4. 中文字体已安装
fc-list :lang=zh | head -1

# 5. Emoji 字体已安装
fc-list | grep -i emoji | head -1

# 6. 处于图形会话中（非纯 SSH）
echo $DISPLAY
```

全部通过后，即可运行：

```bash
python server.py
```

---

## 6. 参考链接

- [pywebview Linux 依赖文档](https://pywebview.flowrl.com/guide/installation.html#linux)
- [PyGObject 官方安装指南](https://pygobject.readthedocs.io/en/latest/getting_started.html)
- [Noto Fonts](https://fonts.google.com/noto)
