"""SteamVR 字幕悬浮窗。

与桌面悬浮窗（overlay_window.py）一样作为独立进程运行：

    python vr_overlay.py --url http://127.0.0.1:PORT

冻结（PyInstaller）后由主程序通过 `--run-vr-overlay` 重新拉起自身进入这里。

实现方式：直接复用桌面悬浮窗的 OverlayWindow（同一套字幕模型、渲染、按钮），
但窗口不上屏（WA_DontShowOnScreen），而是离屏渲染成图片，传成 OpenGL 纹理经
IVROverlay::SetOverlayTexture 贴到 SteamVR 悬浮层上（GL 不可用时退回 SetOverlayRaw）。字号、透明度等设置存在独立的
QSettings("RealtimeSubtitle", "VROverlay")，与桌面悬浮窗互不影响。

交互：SteamVR 把手柄射线在悬浮层上的落点当作鼠标事件发给我们（只有打开 SteamVR
菜单/仪表盘时射线才会出现）。射线指向悬浮层时显示按钮条；扳机按住背景、或按住握持键
（任何位置，不会点到按钮）即「抓住」悬浮层，它会随手柄在 VR 空间中移动/旋转（俯仰、
翻滚接近竖直时轻微吸附），抓着时推拉摇杆调远近；扳机按住边缘拖动调整大小。
菜单打开时 SteamVR 不给任何程序读手柄（实测），这些都只能靠菜单激光转发来的鼠标/滚动
事件：扳机=左键，握持键=中键（Touch 系手柄的默认菜单绑定），摇杆=平滑滚动。
桌面版的「鼠标穿透」在 VR 里没有意义，已去掉，换成整体缩放按钮。

位置模式（主界面设置菜单里选，经 server 下发）：
  * world     固定在世界中（默认）
  * head      跟随头部：位移和旋转（6 轴）完全跟随头显
  * body      身体跟随：跟随水平移动与转身（平时慢慢跟，转身时快速回正），高度固定，不随俯仰/翻滚

连接 SteamVR 用 VRApplication_Background：SteamVR 没运行时不会把它拉起来，也不报错，
而是每隔几秒静默重试；SteamVR 退出后自动断开，等它再次启动。
"""

import os
import sys
import json
import math
import time
import ctypes
import argparse
import logging
import tempfile
import threading

from PySide6.QtCore import Qt, QTimer, QPoint, QPointF, QEvent, QLocale
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QRegion
from PySide6.QtWidgets import QApplication, QWidget

import overlay_window as ow

log = logging.getLogger("vr_overlay")

try:
    import openvr
except Exception:  # pragma: no cover - 仅在缺依赖或运行库加载失败时触发
    openvr = None


OVERLAY_KEY = "febilly.realtime-subtitle.vr-overlay"
OVERLAY_NAME = "Realtime Subtitle VR"

# 画布（逻辑像素）尺寸：对应桌面悬浮窗的窗口大小，决定能放下几行字幕。
VR_MIN_W, VR_MIN_H = 480, 140
VR_MAX_W, VR_MAX_H = 1600, 900
VR_DEFAULT_W, VR_DEFAULT_H = 860, 240
# 射线比鼠标抖得多，边缘缩放热区比桌面版（8px）宽很多。
VR_RESIZE_MARGIN = 22

# 每个画布像素对应的物理尺寸（米）；整体缩放按钮就是调它。
DEFAULT_METERS_PER_PX = 0.0011
MIN_METERS_PER_PX = 0.0004
MAX_METERS_PER_PX = 0.004
SCALE_STEP = 1.15

ATTACH_WORLD = "world"
ATTACH_HEAD = "head"
ATTACH_BODY = "body"
ATTACH_MODES = (ATTACH_WORLD, ATTACH_HEAD, ATTACH_BODY)
DEFAULT_ATTACH = ATTACH_BODY

# 默认位置：视线前方 1.4 米、略低于视线。
DEFAULT_LOCAL_POSITION = (0.0, -0.25, -1.4)
# 身体跟随的偏航：没有「完全不动」的死区，而是两档速度平滑切换。
#   慢跟：一直以很慢的速度朝头的方向转，左右张望时字幕几乎不动，盯着侧面久了它也会慢慢转过来。
#   快速回正：偏差超过 BODY_YAW_RECENTER_DEGREES（转身了）后，叠加头部转动（前馈）并快速收敛，
#     一路回到正前方；回正且头基本停下后，平滑降回慢跟。
BODY_YAW_RECENTER_DEGREES = 20.0
BODY_YAW_SETTLE_DEGREES = 2.0          # 快速回正到这个偏差以内……
BODY_YAW_SETTLE_SPEED_DEGREES = 30.0   # ……且头部转速低于此值（度/秒）时降回慢跟
BODY_YAW_SLOW_RATE = 0.5               # 慢跟的指数收敛速率（1/秒，时间常数 2 秒）
BODY_YAW_FAST_RATE = 6.0               # 快速回正的收敛速率（1/秒，时间常数约 0.17 秒）
BODY_YAW_RISE_SECONDS = 0.12           # 进入快速回正的过渡时间常数
BODY_YAW_FALL_SECONDS = 0.25           # 降回慢跟的过渡时间常数
BODY_YAW_SMOOTHING_SECONDS = 0.03      # 角速度的平滑时间常数（让加减速连续；再大会冲过头）
# 拖动时俯仰/翻滚在竖直附近的吸附角度。
SNAP_DEGREES = 5.0

# 抓住时用摇杆前后推拉调整距离：推到底时距离每秒变化 e^GRAB_PUSH_MAX_RATE 倍（按比例
# 变化，远近手感一致），出了死区按平方曲线加速。
GRAB_PUSH_MAX_RATE = 1.2
# 菜单打开时摇杆只能以滚动事件（ScrollSmooth）拿到，单位未知：按观测到的最大滚动速度
# 归一化成「推到底=1」，并记住这个峰值（存在设置里）。摇杆漂移由 SteamVR 自己的滚动死区
# 挡掉，这里再留一个小死区。
SCROLL_DEADZONE = 0.15
SCROLL_MIN_PEAK = 1e-3
SCROLL_SMOOTHING_SECONDS = 0.08
GRAB_MIN_DISTANCE = 0.2   # 悬浮层中心到手柄的距离范围（米）
GRAB_MAX_DISTANCE = 8.0

# 抓住时吸附上的轴，把对应的边高亮（翻滚→上下边，俯仰→左右边）；
# 松手后保持一会儿，让用户确认松手时的手抖没把它带歪，然后淡出。
SNAP_HIGHLIGHT_COLOR = (59, 130, 246)   # #3b82f6，与主界面主题色一致
SNAP_HIGHLIGHT_WIDTH = 4.0
SNAP_HIGHLIGHT_HOLD = 0.5
SNAP_HIGHLIGHT_FADE = 0.2

# 超采样让 VR 里的文字更清晰；同时限制单帧像素量（SetOverlayRaw 有数据量上限）。
SUPERSAMPLE = 2.0
MAX_UPLOAD_PIXELS = 2_000_000

CONNECT_RETRY_MS = 3000
TICK_ACTIVE_MS = 11      # 显示中：轮询射线事件、更新跟随/拖动姿态
TICK_IDLE_MS = 200       # 隐藏中：只需留意 SteamVR 退出事件
FRAME_PUSH_MS = 66
FRAME_PUSH_FAST_MS = 16  # 抓取中/高亮淡出时：只重画高亮，提高帧率让动画顺滑


_VR_I18N = {
    "zh": {"scale_dec": "缩小悬浮窗", "scale_inc": "放大悬浮窗", "close": "关闭 VR 字幕悬浮窗"},
    "en": {"scale_dec": "Make overlay smaller", "scale_inc": "Make overlay larger",
           "close": "Close VR subtitle overlay"},
    "ja": {"scale_dec": "オーバーレイを小さくする", "scale_inc": "オーバーレイを大きくする",
           "close": "VR 字幕オーバーレイを閉じる"},
    "ko": {"scale_dec": "오버레이 축소", "scale_inc": "오버레이 확대", "close": "VR 자막 오버레이 닫기"},
    "ru": {"scale_dec": "Уменьшить оверлей", "scale_inc": "Увеличить оверлей",
           "close": "Закрыть VR-оверлей субтитров"},
    "es": {"scale_dec": "Reducir superposición", "scale_inc": "Ampliar superposición",
           "close": "Cerrar superposición de subtítulos VR"},
    "pt": {"scale_dec": "Diminuir sobreposição", "scale_inc": "Aumentar sobreposição",
           "close": "Fechar sobreposição de legendas VR"},
}


def vtr(key: str) -> str:
    lang = QLocale.system().name().split('_')[0]
    if lang not in _VR_I18N:
        lang = "en"
    return _VR_I18N[lang].get(key, _VR_I18N["en"].get(key, key))


def normalize_attach_mode(value) -> str:
    value = str(value or "").strip().lower()
    return value if value in ATTACH_MODES else DEFAULT_ATTACH


# ===========================================================================
# 纯几何计算（不依赖 openvr，便于单测）
# 姿态用 3x4 嵌套列表表示（OpenVR HmdMatrix34_t 的行主序）：右手系，+Y 上、-Z 前。
# ===========================================================================
IDENTITY = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]


def mat_mul(a, b):
    """两个 3x4 刚体变换相乘（视作 4x4，末行 0 0 0 1）。"""
    out = []
    for i in range(3):
        row = []
        for j in range(4):
            v = sum(a[i][k] * b[k][j] for k in range(3))
            if j == 3:
                v += a[i][3]
            row.append(v)
        out.append(row)
    return out


def mat_inv(m):
    """刚体变换求逆：R^T, -R^T t。"""
    rt = [[m[j][i] for j in range(3)] for i in range(3)]
    t = [m[i][3] for i in range(3)]
    return [[rt[i][0], rt[i][1], rt[i][2], -sum(rt[i][k] * t[k] for k in range(3))]
            for i in range(3)]


def translation(x, y, z):
    return [[1.0, 0.0, 0.0, x], [0.0, 1.0, 0.0, y], [0.0, 0.0, 1.0, z]]


def rotation_ypr(yaw, pitch, roll, position=(0.0, 0.0, 0.0)):
    """R = Ry(yaw)·Rx(pitch)·Rz(roll)。yaw=pitch=roll=0 时朝向 -Z、正立。"""
    ca, sa = math.cos(yaw), math.sin(yaw)
    cb, sb = math.cos(pitch), math.sin(pitch)
    cc, sc = math.cos(roll), math.sin(roll)
    return [
        [ca * cc + sa * sb * sc, -ca * sc + sa * sb * cc, sa * cb, position[0]],
        [cb * sc, cb * cc, -sb, position[1]],
        [-sa * cc + ca * sb * sc, sa * sc + ca * sb * cc, ca * cb, position[2]],
    ]


def decompose_ypr(m):
    """rotation_ypr 的逆运算，返回 (yaw, pitch, roll)。"""
    pitch = math.asin(max(-1.0, min(1.0, -m[1][2])))
    if abs(m[1][2]) > 0.9999:
        # 万向锁（正对天/地）：把翻滚并入偏航
        return math.atan2(-m[2][0], m[0][0]), pitch, 0.0
    return math.atan2(m[0][2], m[2][2]), pitch, math.atan2(m[1][0], m[1][1])


def snap_upright_axes(m, degrees=SNAP_DEGREES):
    """俯仰、翻滚在竖直附近（±degrees）时吸附为 0，偏航与位置不变。

    返回 (新姿态, 俯仰是否吸附上, 翻滚是否吸附上)。
    """
    yaw, pitch, roll = decompose_ypr(m)
    limit = math.radians(degrees)
    snap_pitch, snap_roll = abs(pitch) < limit, abs(roll) < limit
    if not (snap_pitch or snap_roll):
        return m, False, False
    if snap_pitch:
        pitch = 0.0
    if snap_roll:
        roll = 0.0
    return rotation_ypr(yaw, pitch, roll, (m[0][3], m[1][3], m[2][3])), snap_pitch, snap_roll


def snap_upright(m, degrees=SNAP_DEGREES):
    return snap_upright_axes(m, degrees)[0]


def scroll_push_rate(value, peak):
    """滚动速度 → 距离的对数变化率：按峰值归一化，小死区，平方曲线。"""
    n = value / max(peak, SCROLL_MIN_PEAK)
    excess = (abs(n) - SCROLL_DEADZONE) / (1.0 - SCROLL_DEADZONE)
    if excess <= 0:
        return 0.0
    return math.copysign(GRAB_PUSH_MAX_RATE * min(1.0, excess) ** 2, value)


def _point(m):
    return (m[0][3], m[1][3], m[2][3])


def _rotate(m, v):
    return tuple(sum(m[i][k] * v[k] for k in range(3)) for i in range(3))


def _rotate_inv(m, v):
    return tuple(sum(m[k][i] * v[k] for k in range(3)) for i in range(3))


def laser_direction_in_controller(controller, point):
    """手柄坐标系里「手柄原点→point」的单位方向：记下激光相对手柄的方向，之后跟着手柄转。"""
    to = [point[i] - controller[i][3] for i in range(3)]
    n = math.sqrt(sum(v * v for v in to))
    if n < 1e-6:
        return None
    return _rotate_inv(controller, [v / n for v in to])


def ray_hit_in_plane(plane, origin, direction):
    """射线与平面（plane 姿态的 z=0 平面）的交点，返回平面坐标 (x, y)；平行或在背后时返回 None。"""
    inv = mat_inv(plane)
    o = _rotate(inv, origin)
    o = (o[0] + inv[0][3], o[1] + inv[1][3], o[2] + inv[2][3])
    d = _rotate(inv, direction)
    if abs(d[2]) < 1e-6:
        return None
    t = -o[2] / d[2]
    if t <= 0:
        return None
    return (o[0] + t * d[0], o[1] + t * d[1])


def push_relative(relative, rate, dt):
    """沿「手柄→悬浮层中心」连线推远/拉近抓取中的悬浮层（relative 为手柄系下的姿态）。"""
    t = [relative[i][3] for i in range(3)]
    dist = math.sqrt(sum(v * v for v in t))
    if rate == 0 or dist < 1e-6:
        return relative
    new_dist = dist * math.exp(rate * dt)
    # 只在推拉方向上限位：本来就在范围外时不会被一下拽回来
    if rate > 0:
        new_dist = min(new_dist, max(dist, GRAB_MAX_DISTANCE))
    else:
        new_dist = max(new_dist, min(dist, GRAB_MIN_DISTANCE))
    k = new_dist / dist
    return [[row[0], row[1], row[2], row[3] * k] for row in relative]


def hmd_yaw(hmd):
    """头显在水平面上的朝向角（忽略俯仰/翻滚），与 rotation_ypr 的 yaw 对应。"""
    fx, fz = -hmd[0][2], -hmd[2][2]
    if math.hypot(fx, fz) < 1e-3:
        # 几乎正对天/地：改用头顶方向推断水平朝向
        fx, fz = hmd[0][1], hmd[2][1]
    return math.atan2(-fx, -fz) if math.hypot(fx, fz) >= 1e-3 else 0.0


def yaw_frame(hmd):
    """只保留头显的位置与水平朝向（偏航）的坐标系。"""
    return rotation_ypr(hmd_yaw(hmd), 0.0, 0.0, (hmd[0][3], hmd[1][3], hmd[2][3]))


def body_frame(hmd, yaw):
    """身体跟随的参考系：水平位置随头显，高度锁在地面（pose 里存绝对高度），朝向为 yaw。"""
    return rotation_ypr(yaw, 0.0, 0.0, (hmd[0][3], 0.0, hmd[2][3]))


def _wrap_angle(a):
    return math.atan2(math.sin(a), math.cos(a))


class YawFollower:
    """身体跟随的偏航：慢跟 + 快速回正，两档之间用 urgency（0..1）平滑过渡。

    期望角速度 = urgency × 头的转速（前馈，转身时不会一直落后）+ 剩余偏差 × 收敛速率
    （慢跟速率→快速速率，随 urgency 插值）；实际角速度再低通追向期望值。urgency 在快速
    回正时趋向 1、回正后趋向 0，因此档位切换与加减速都是连续的。
    """

    def __init__(self, recenter_degrees=BODY_YAW_RECENTER_DEGREES,
                 settle_degrees=BODY_YAW_SETTLE_DEGREES,
                 settle_speed_degrees=BODY_YAW_SETTLE_SPEED_DEGREES,
                 slow_rate=BODY_YAW_SLOW_RATE, fast_rate=BODY_YAW_FAST_RATE,
                 rise_seconds=BODY_YAW_RISE_SECONDS, fall_seconds=BODY_YAW_FALL_SECONDS,
                 smoothing_seconds=BODY_YAW_SMOOTHING_SECONDS):
        self.recenter = math.radians(recenter_degrees)
        self.settle = math.radians(settle_degrees)
        self.settle_speed = math.radians(settle_speed_degrees)
        self.slow_rate = slow_rate
        self.fast_rate = fast_rate
        self.rise = rise_seconds
        self.fall = fall_seconds
        self.smoothing = smoothing_seconds
        self.reset()

    def reset(self, yaw=None):
        self.yaw = yaw
        self._last_target = yaw
        self.recentering = False
        self.urgency = 0.0
        self.velocity = 0.0

    def update(self, target, dt):
        if self.yaw is None:
            self.reset(target)
            return self.yaw
        dt = max(0.0, min(dt, 0.1))
        head_delta = _wrap_angle(target - self._last_target)
        self._last_target = target
        if not self.recentering and abs(_wrap_angle(target - self.yaw)) > self.recenter:
            self.recentering = True

        if dt > 0:
            goal, tau = (1.0, self.rise) if self.recentering else (0.0, self.fall)
            self.urgency += (goal - self.urgency) * (1.0 - math.exp(-dt / tau))
        w = self.urgency
        if dt > 0:
            # 期望角速度 = 前馈（头的转速）+ 剩余偏差的收敛；实际角速度再低通追向它，
            # 让加速/减速连续，切换档位时不会「猛地一拽」。
            rate = self.slow_rate + (self.fast_rate - self.slow_rate) * w
            desired = w * head_delta / dt + rate * _wrap_angle(target - self.yaw)
            self.velocity += (desired - self.velocity) * (1.0 - math.exp(-dt / self.smoothing))
            self.yaw += self.velocity * dt

        if self.recentering:
            head_speed = abs(head_delta) / dt if dt > 0 else 0.0
            if (abs(_wrap_angle(target - self.yaw)) < self.settle
                    and head_speed < self.settle_speed):
                self.recentering = False
        self.yaw = _wrap_angle(self.yaw)
        return self.yaw


def mode_frame(mode, hmd, body_yaw=None):
    """位置模式对应的参考系（在站立坐标系中）；hmd 为头显姿态。"""
    if mode == ATTACH_HEAD:
        return hmd
    if mode == ATTACH_BODY:
        return body_frame(hmd, hmd_yaw(hmd) if body_yaw is None else body_yaw)
    return IDENTITY


def default_pose(mode, hmd=None):
    """模式参考系下的默认姿态：视线前方略低处，正对用户。需要头显姿态而拿不到时返回 None。"""
    x, y, z = DEFAULT_LOCAL_POSITION
    if mode == ATTACH_HEAD:
        return translation(x, y, z)
    if hmd is None:
        return None
    if mode == ATTACH_BODY:
        return translation(x, hmd[1][3] + y, z)
    return mat_mul(yaw_frame(hmd), translation(x, y, z))


def canvas_to_local(x, y, size, meters_per_px):
    """画布坐标（左上原点、y 向下）→ 悬浮层自身平面坐标（米，中心原点、y 向上）。"""
    w, h = size
    return ((x - w / 2) * meters_per_px, (h / 2 - y) * meters_per_px)


def resize_in_plane(edges, start_size, start_center, delta, meters_per_px):
    """按拖动的边缘与平面位移计算新画布尺寸和中心，对边保持不动。"""
    left_e, top_e, right_e, bottom_e = edges
    w0, h0 = start_size
    half_w, half_h = w0 * meters_per_px / 2, h0 * meters_per_px / 2
    left, right = start_center[0] - half_w, start_center[0] + half_w
    top, bottom = start_center[1] + half_h, start_center[1] - half_h
    dx, dy = delta
    min_w, max_w = VR_MIN_W * meters_per_px, VR_MAX_W * meters_per_px
    min_h, max_h = VR_MIN_H * meters_per_px, VR_MAX_H * meters_per_px
    if left_e:
        left = min(max(left + dx, right - max_w), right - min_w)
    if right_e:
        right = min(max(right + dx, left + min_w), left + max_w)
    if top_e:
        top = min(max(top + dy, bottom + min_h), bottom + max_h)
    if bottom_e:
        bottom = min(max(bottom + dy, top - max_h), top - min_h)
    size = (round((right - left) / meters_per_px), round((top - bottom) / meters_per_px))
    return size, ((left + right) / 2, (top + bottom) / 2)


def pick_pointing_device(candidates, target):
    """从 {index: pose} 中挑出 -Z 方向最对准 target 点的设备（射线来源的兜底判断）。"""
    best, best_dot = None, -2.0
    for index, pose in candidates.items():
        to = [target[i] - pose[i][3] for i in range(3)]
        n = math.sqrt(sum(v * v for v in to))
        if n < 1e-6:
            continue
        forward = [-pose[i][2] for i in range(3)]
        dot = sum(forward[i] * to[i] for i in range(3)) / n
        if dot > best_dot:
            best, best_dot = index, dot
    return best


def supersample_scale(width, height):
    budget = math.sqrt(MAX_UPLOAD_PIXELS / max(1, width * height))
    return max(1.0, min(SUPERSAMPLE, budget))


def _pose_to_json(m):
    return json.dumps([[round(v, 6) for v in row] for row in m]) if m else ""


def _pose_from_json(value):
    try:
        m = json.loads(value) if value else None
        if (isinstance(m, list) and len(m) == 3
                and all(isinstance(r, list) and len(r) == 4 for r in m)):
            return [[float(v) for v in row] for row in m]
    except (TypeError, ValueError):
        pass
    return None


# ===========================================================================
# 帧上传：OpenGL 纹理（SetOverlayTexture）
# ===========================================================================
# SetOverlayRaw 是给不常更新的图片用的：每次调用 SteamVR 都要重建悬浮层纹理，字幕
# 一更新悬浮层就会闪一下。频繁更新应交 GPU 纹理（Valve 的 Qt 悬浮层示例也是 GL 纹理）。
# 只用 GL 1.1 函数，直接从 opengl32.dll 调，不依赖（打包时被排除的）QtOpenGL 模块。
_GL_TEXTURE_2D = 0x0DE1
_GL_TEXTURE_MAG_FILTER = 0x2800
_GL_TEXTURE_MIN_FILTER = 0x2801
_GL_TEXTURE_WRAP_S = 0x2802
_GL_TEXTURE_WRAP_T = 0x2803
_GL_LINEAR = 0x2601
_GL_CLAMP_TO_EDGE = 0x812F
_GL_UNPACK_ALIGNMENT = 0x0CF5
_GL_RGBA = 0x1908
_GL_RGBA8 = 0x8058
_GL_UNSIGNED_BYTE = 0x1401


# SteamVR 第一次收到 GL 纹理时按当时的尺寸建一块共享纹理，之后不再随纹理尺寸重建（实测
# vrclient 日志只有一次 "Created shared texture"）：画布变大后新画面会被裁掉。所以 GL 纹理
# 固定按最大画布尺寸分配，每帧只把画面传到正中间一块，再用 texture bounds 只显示这一块
# （bounds 参与悬浮层宽高比计算）。区域上下对称，v 轴不论按哪个方向算都选中同一块。
GL_TEXTURE_W = int(VR_MAX_W * SUPERSAMPLE) + 4
GL_TEXTURE_H = int(VR_MAX_H * SUPERSAMPLE) + 4


def gl_upload_layout(w, h, tex_w=GL_TEXTURE_W, tex_h=GL_TEXTURE_H):
    """w×h 的画面在固定纹理里的摆放：返回 (x0, y0, 区域宽, 区域高, (uMin, vMin, uMax, vMax))。

    区域 = 画面 + 四周 1 像素透明边（双线性采样边缘时不带进旧内容）；高度差为奇数时底部
    多补一行透明像素，让区域在纹理里严格上下居中。bounds 只框住画面（含那一行补齐）。
    """
    extra = (tex_h - h) % 2
    rw, rh = w + 2, h + 2 + extra
    x0, y0 = (tex_w - rw) // 2, (tex_h - rh) // 2
    v_min = (y0 + 1) / tex_h
    return x0, y0, rw, rh, ((x0 + 1) / tex_w, v_min, (x0 + 1 + w) / tex_w, 1.0 - v_min)


def gl_mouse_map(w, h, canvas_size):
    """GL 路径下光标坐标 → 画布坐标的换算参数 (原点 x, 原点 y, x 缩放, y 缩放)。

    SteamVR 算射线落点时不知道 GL 纹理的尺寸，而是把 mouse scale 当作纹理尺寸、再乘上
    bounds（实测）。所以 GL 路径把 mouse scale 设成整张纹理的尺寸：悬浮层形状才对，光标
    坐标也就成了纹理像素坐标（左上原点），这里换算回画布坐标。
    """
    x0, y0, _, _, _ = gl_upload_layout(w, h)
    cw, ch = canvas_size
    return (x0 + 1, y0 + 1, w / max(1, cw), h / max(1, ch))


def mouse_to_canvas(x, y, mouse_map):
    if mouse_map is None:
        return x, y  # mouse scale 就是画布尺寸
    ox, oy, sx, sy = mouse_map
    return (x - ox) / sx, (y - oy) / sy


class GlTextureUploader:
    """在离屏 GL 上下文里把 QImage 传成纹理，交给 SteamVR。

    两张固定尺寸的纹理轮流用：SteamVR 可能还在读上一张，不覆盖刚交出去的那张。
    任何一步失败都返回 False，由调用方退回 SetOverlayRaw。
    """

    def __init__(self):
        self._ctx = None
        self._surface = None
        self._gl = None
        self._textures = []
        self._next = 0
        self._bounds = None   # (overlay handle, bounds) 上次设置的值
        self.used = False     # 是否设过 bounds（退回 SetOverlayRaw 时要恢复成整张）
        self.failed = False

    def _ensure(self) -> bool:
        if self._ctx is not None:
            return True
        if self.failed or sys.platform != "win32":
            self.failed = True
            return False
        try:
            from PySide6.QtGui import QOpenGLContext, QOffscreenSurface
            gl = ctypes.WinDLL("opengl32")
            ctx = QOpenGLContext()
            if not ctx.create() or ctx.isOpenGLES():
                raise RuntimeError("no desktop OpenGL context")
            surface = QOffscreenSurface()
            surface.setFormat(ctx.format())
            surface.create()
            if not ctx.makeCurrent(surface):
                raise RuntimeError("makeCurrent failed")
            ids = (ctypes.c_uint * 2)()
            gl.glGenTextures(2, ids)
            for tex in ids:
                gl.glBindTexture(_GL_TEXTURE_2D, tex)
                gl.glTexParameteri(_GL_TEXTURE_2D, _GL_TEXTURE_MIN_FILTER, _GL_LINEAR)
                gl.glTexParameteri(_GL_TEXTURE_2D, _GL_TEXTURE_MAG_FILTER, _GL_LINEAR)
                gl.glTexParameteri(_GL_TEXTURE_2D, _GL_TEXTURE_WRAP_S, _GL_CLAMP_TO_EDGE)
                gl.glTexParameteri(_GL_TEXTURE_2D, _GL_TEXTURE_WRAP_T, _GL_CLAMP_TO_EDGE)
                # 只分配不填数据：bounds 之外的内容不会显示
                gl.glTexImage2D(_GL_TEXTURE_2D, 0, _GL_RGBA8, GL_TEXTURE_W, GL_TEXTURE_H, 0,
                                _GL_RGBA, _GL_UNSIGNED_BYTE, None)
            gl.glBindTexture(_GL_TEXTURE_2D, 0)
        except Exception:
            log.exception("OpenGL texture upload unavailable, using SetOverlayRaw")
            self.failed = True
            return False
        self._gl, self._ctx, self._surface = gl, ctx, surface
        self._textures = list(ids)
        return True

    def submit(self, overlay, handle, image: QImage) -> bool:
        w, h = image.width(), image.height()
        if w + 2 > GL_TEXTURE_W or h + 3 > GL_TEXTURE_H:
            return False
        if not self._ensure():
            return False
        try:
            if not self._ctx.makeCurrent(self._surface):
                return False
            gl = self._gl
            tex = self._textures[self._next]
            x0, y0, rw, rh, bounds = gl_upload_layout(w, h)
            region = QImage(rw, rh, QImage.Format_RGBA8888_Premultiplied)
            region.fill(Qt.transparent)
            source = QImage(image)
            source.setDevicePixelRatio(1.0)  # 按像素原样拷贝，不按超采样倍率缩放
            painter = QPainter(region)
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            painter.drawImage(1, 1, source)
            painter.end()
            # GL 纹理第 0 行在底部（SteamVR 按 GL 约定显示），所以先上下翻转。
            flipped = region.flipped(Qt.Vertical) if hasattr(region, "flipped") \
                else region.mirrored(False, True)
            buf = (ctypes.c_ubyte * flipped.sizeInBytes()).from_buffer_copy(flipped.constBits())
            gl.glBindTexture(_GL_TEXTURE_2D, tex)
            gl.glPixelStorei(_GL_UNPACK_ALIGNMENT, 4)
            # 像素原样上传（仍是预乘 alpha，悬浮层设了 IsPremultiplied）。
            gl.glTexSubImage2D(_GL_TEXTURE_2D, 0, x0, y0, rw, rh,
                               _GL_RGBA, _GL_UNSIGNED_BYTE, buf)
            gl.glBindTexture(_GL_TEXTURE_2D, 0)
            gl.glFinish()
            if self._bounds != (handle, bounds):
                vr_bounds = openvr.VRTextureBounds_t()
                vr_bounds.uMin, vr_bounds.vMin, vr_bounds.uMax, vr_bounds.vMax = bounds
                overlay.setOverlayTextureBounds(handle, vr_bounds)
                self._bounds = (handle, bounds)
                self.used = True
            texture = openvr.Texture_t()
            texture.handle = tex
            texture.eType = openvr.TextureType_OpenGL
            texture.eColorSpace = openvr.ColorSpace_Auto
            overlay.setOverlayTexture(handle, texture)
            self._next = 1 - self._next
            return True
        except Exception:
            # 不在两条路之间来回切（那本身就会闪），之后一直用 SetOverlayRaw。
            log.exception("OpenGL texture upload failed, switching to SetOverlayRaw")
            self.failed = True
            return False


# ===========================================================================
# OpenVR 封装：所有 openvr 调用集中在这里
# ===========================================================================
class VrBackend:
    def __init__(self):
        self.system = None
        self.overlay = None
        self.handle = None
        self._uploader = GlTextureUploader()
        self._canvas_size = (VR_DEFAULT_W, VR_DEFAULT_H)
        self._mouse_map = None     # GL 路径下光标坐标的换算参数，见 gl_mouse_map
        self._mouse_scale = None   # 上次设置的 (handle, mouse scale)
        self._event = openvr.VREvent_t() if openvr is not None else None
        self._poses = ((openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
                       if openvr is not None else None)

    @property
    def connected(self) -> bool:
        return self.handle is not None

    def connect(self) -> bool:
        if openvr is None:
            return False
        try:
            # Background：SteamVR 未运行时直接失败（不会把 SteamVR 拉起来），由调用方静默重试。
            self.system = openvr.init(openvr.VRApplication_Background)
            self.overlay = openvr.VROverlay()
            try:
                self.handle = self.overlay.createOverlay(OVERLAY_KEY, OVERLAY_NAME)
            except openvr.error_code.OverlayError:
                # 上次异常退出时 key 可能还被占着
                self.handle = self.overlay.findOverlay(OVERLAY_KEY)
            self.overlay.setOverlayInputMethod(self.handle, openvr.VROverlayInputMethod_Mouse)
            self.overlay.setOverlayFlag(self.handle, openvr.VROverlayFlags_IsPremultiplied, True)
            # 菜单打开时读不到摇杆，但激光指着悬浮层推摇杆会以滚动事件发给我们
            self.overlay.setOverlayFlag(self.handle, openvr.VROverlayFlags_SendVRSmoothScrollEvents, True)
            return True
        except Exception:
            self.disconnect()
            return False

    def disconnect(self, acknowledge_quit=False):
        if self.system is not None and acknowledge_quit:
            try:
                self.system.acknowledgeQuit_Exiting()
            except Exception:
                pass
        if self.system is not None:
            try:
                openvr.shutdown()
            except Exception:
                pass
        self.system = None
        self.overlay = None
        self.handle = None
        self._mouse_map = None
        self._mouse_scale = None

    def set_visible(self, visible: bool):
        if not self.connected:
            return
        try:
            if visible:
                self.overlay.showOverlay(self.handle)
            else:
                self.overlay.hideOverlay(self.handle)
        except Exception:
            pass

    def set_size(self, size, meters_per_px):
        if not self.connected:
            return
        try:
            w, h = size
            self._canvas_size = (w, h)
            self.overlay.setOverlayWidthInMeters(self.handle, w * meters_per_px)
            if self._mouse_map is None:
                self._set_mouse_scale(w, h)
        except Exception:
            pass

    def _set_mouse_scale(self, w, h):
        if self._mouse_scale == (self.handle, (w, h)):
            return
        scale = openvr.HmdVector2_t()
        scale.v[0], scale.v[1] = float(w), float(h)
        self.overlay.setOverlayMouseScale(self.handle, scale)
        self._mouse_scale = (self.handle, (w, h))

    @staticmethod
    def _matrix(m):
        mat = openvr.HmdMatrix34_t()
        for i in range(3):
            for j in range(4):
                mat.m[i][j] = m[i][j]
        return mat

    def set_transform_absolute(self, m):
        if not self.connected:
            return
        try:
            self.overlay.setOverlayTransformAbsolute(
                self.handle, openvr.TrackingUniverseStanding, self._matrix(m))
        except Exception:
            pass

    def set_transform_head_relative(self, m):
        if not self.connected:
            return
        try:
            self.overlay.setOverlayTransformTrackedDeviceRelative(
                self.handle, openvr.k_unTrackedDeviceIndex_Hmd, self._matrix(m))
        except Exception:
            pass

    def set_frame(self, image: QImage) -> bool:
        if not self.connected:
            return False
        if self._uploader.submit(self.overlay, self.handle, image):
            try:
                self._set_mouse_scale(GL_TEXTURE_W, GL_TEXTURE_H)
                self._mouse_map = gl_mouse_map(image.width(), image.height(), self._canvas_size)
            except Exception:
                pass
            return True
        try:
            if self._uploader.used:
                # GL 路径设过 bounds：SetOverlayRaw 传的是整张图，恢复成整张
                full = openvr.VRTextureBounds_t()
                full.uMin, full.vMin, full.uMax, full.vMax = 0.0, 0.0, 1.0, 1.0
                self.overlay.setOverlayTextureBounds(self.handle, full)
                self._uploader.used = False
            if self._mouse_map is not None:
                self._mouse_map = None
                self._set_mouse_scale(*self._canvas_size)
            w, h = image.width(), image.height()
            # RGBA8888 每行 4 字节对齐，bytesPerLine 恰为 w*4，整块即紧凑像素数据。
            buf = (ctypes.c_ubyte * image.sizeInBytes()).from_buffer_copy(image.constBits())
            self.overlay.setOverlayRaw(self.handle, buf, w, h, 4)
            return True
        except Exception:
            return False

    def _seconds_to_photons(self):
        """预测到画面真正显示出来的时间，让跟随头部的悬浮层不拖影。"""
        try:
            ok, since_vsync, _ = self.system.getTimeSinceLastVsync()
            hmd = openvr.k_unTrackedDeviceIndex_Hmd
            freq = self.system.getFloatTrackedDeviceProperty(hmd, openvr.Prop_DisplayFrequency_Float)
            to_photons = self.system.getFloatTrackedDeviceProperty(
                hmd, openvr.Prop_SecondsFromVsyncToPhotons_Float)
            if not ok or freq <= 0:
                return 0.0
            return max(0.0, 1.0 / freq - since_vsync + to_photons)
        except Exception:
            return 0.0

    def device_poses(self):
        """{设备索引: 3x4 姿态}（站立坐标系，仅有效姿态）。"""
        if not self.connected:
            return {}
        try:
            self.system.getDeviceToAbsoluteTrackingPose(
                openvr.TrackingUniverseStanding, self._seconds_to_photons(), self._poses)
        except Exception:
            return {}
        out = {}
        for index, pose in enumerate(self._poses):
            if pose.bPoseIsValid:
                m = pose.mDeviceToAbsoluteTracking.m
                out[index] = [[m[i][j] for j in range(4)] for i in range(3)]
        return out

    def controller_indices(self):
        if not self.connected:
            return set()
        out = set()
        try:
            for index in range(openvr.k_unMaxTrackedDeviceCount):
                if self.system.getTrackedDeviceClass(index) == openvr.TrackedDeviceClass_Controller:
                    out.add(index)
        except Exception:
            pass
        return out

    def alive(self) -> bool:
        """SteamVR 崩溃时不会发 Quit 事件：用一次轻量调用确认连接仍然有效。"""
        if not self.connected:
            return False
        try:
            return self.overlay.findOverlay(OVERLAY_KEY) == self.handle
        except Exception:
            return False

    def poll(self):
        """取出所有待处理事件，转成 (kind, x, y, device)；x/y 为画布坐标（左上原点）。"""
        if not self.connected:
            return []
        out = []
        ev = self._event
        try:
            while self.overlay.pollNextOverlayEvent(self.handle, ev)[0]:
                et = ev.eventType
                mouse = ev.data.mouse
                device = ev.trackedDeviceIndex
                x, y = mouse_to_canvas(mouse.x, mouse.y, self._mouse_map)
                if et == openvr.VREvent_MouseMove:
                    out.append(("move", x, y, device))
                elif et == openvr.VREvent_MouseButtonDown and mouse.button == openvr.VRMouseButton_Left:
                    out.append(("down", x, y, device))
                elif et == openvr.VREvent_MouseButtonUp and mouse.button == openvr.VRMouseButton_Left:
                    out.append(("up", x, y, device))
                # 菜单激光里握持键是鼠标中键（Touch 系手柄的默认菜单绑定）
                elif et == openvr.VREvent_MouseButtonDown and mouse.button == openvr.VRMouseButton_Middle:
                    out.append(("grip_down", x, y, device))
                elif et == openvr.VREvent_MouseButtonUp and mouse.button == openvr.VRMouseButton_Middle:
                    out.append(("grip_up", x, y, device))
                elif et == openvr.VREvent_FocusLeave:
                    out.append(("leave", 0.0, 0.0, device))
                elif et == openvr.VREvent_ScrollSmooth:
                    out.append(("scroll", ev.data.scroll.xdelta, ev.data.scroll.ydelta, device))
                elif (300 <= et < 400 and et not in (openvr.VREvent_FocusEnter,
                                                     openvr.VREvent_OverlayFocusChanged)):
                    log.info("overlay input event %s (mouse button %s)", et, mouse.button)
            while self.system.pollNextEvent(ev):
                if ev.eventType == openvr.VREvent_Quit:
                    out.append(("quit", 0.0, 0.0, None))
        except Exception:
            out.append(("quit", 0.0, 0.0, None))
        return out


# ===========================================================================
# VR 悬浮窗：复用 OverlayWindow 的渲染与按钮，只替换「上屏」与交互
# ===========================================================================
class VrSubtitleTextEdit(ow.SubtitleTextEdit):
    """离屏超采样渲染时，语言标识/注音图块也按超采样倍率生成，避免发糊。"""

    render_scale = 1.0

    def devicePixelRatioF(self):
        return self.render_scale

    def set_render_scale(self, scale: float):
        if scale != self.render_scale:
            self.render_scale = scale
            self._tag_cache.clear()
            self._ruby_cache.clear()


class VrOverlayWindow(ow.OverlayWindow):
    SETTINGS_NAME = "VROverlay"
    WS_CLIENT = "vr_overlay"
    DEFAULT_FONT_SIZE = 24

    def __init__(self, server_url: str):
        # 这些属性在父类构造期间就会被（重载的方法）用到，必须先建好。
        self.vr = VrBackend()
        self._vr_visible = False
        self._reported_connected = False  # 服务端在打开时已默认「未连接」
        self._laser_inside = False
        self._hover_button = None
        self._pressed_button = None
        self._grab = None
        self._scroll_accum = 0.0       # 本帧收到的滚动量（菜单里推摇杆）
        self._scroll_stats = [0, 0.0]  # 本次抓取的滚动事件数、总量（写日志用于校准）
        self._snap_axes = (False, False)   # 抓取中 (俯仰, 翻滚) 是否吸附上
        self._snap_release = None          # 松手时刻，用于高亮的保持与淡出
        self._dirty = True
        self._base_frame = None        # 不含高亮的整帧渲染结果
        self._last_highlight = None
        self._last_frame = None
        self._placed_size = None
        self._yaw_follower = YawFollower()
        self._yaw_follow_time = None
        super().__init__(server_url)

        self._load_placement()
        # 不上屏：仍是「可见」的 QWidget（布局、子控件显隐照常），只是没有桌面窗口。
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.show()

        self._connect_timer = QTimer(self)
        self._connect_timer.setInterval(CONNECT_RETRY_MS)
        self._connect_timer.timeout.connect(self._try_connect)

        self._tick_timer = QTimer(self)
        self._tick_timer.setTimerType(Qt.PreciseTimer)
        self._tick_timer.setInterval(TICK_IDLE_MS)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()

        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(FRAME_PUSH_MS)
        self._frame_timer.timeout.connect(self._push_frame)
        self._frame_timer.start()

        # 兜底：有些状态变化（如重启按钮文案）不经过 _render/update，定期标脏比对一次。
        self._safety_timer = QTimer(self)
        self._safety_timer.setInterval(1000)
        self._safety_timer.timeout.connect(self._mark_dirty)
        self._safety_timer.start()

    # ------------------------------------------------------------ UI 调整 --
    def _create_text_edit(self):
        return VrSubtitleTextEdit(self)

    def _init_ui(self):
        super()._init_ui()
        self.setWindowTitle("Realtime Subtitle VR Overlay")
        layout = self.button_bar.layout()

        # 鼠标穿透在 VR 里没有意义：从按钮条移除。其余按钮上为穿透模式准备的透明度
        # 特效也一并去掉——QGraphicsEffect 的控件在离屏 render() 时画不出来。
        layout.removeWidget(self.btn_passthrough)
        self.btn_passthrough.hide()
        for button in self._other_buttons:
            button.setGraphicsEffect(None)
        self._other_buttons = ()
        self._btn_opacity_effects = {}

        self.btn_scale_dec = self._make_button("", vtr("scale_dec"), self._dec_scale, icon="zoom-out")
        self.btn_scale_inc = self._make_button("", vtr("scale_inc"), self._inc_scale, icon="zoom-in")
        self.btn_close.setToolTip(vtr("close"))

        idx = layout.indexOf(self.btn_font_inc) + 1
        layout.insertWidget(idx, self.btn_scale_dec)
        layout.insertWidget(idx + 1, self.btn_scale_inc)

        self._all_buttons = tuple(
            b for b in self._all_buttons if b is not self.btn_passthrough
        ) + (self.btn_scale_dec, self.btn_scale_inc)
        self.button_bar.adjustSize()

    def _apply_windows_no_activate_style(self):
        pass  # 没有桌面窗口

    def _restore_geometry(self):
        w = int(self.settings.value("canvas_w", VR_DEFAULT_W))
        h = int(self.settings.value("canvas_h", VR_DEFAULT_H))
        self.resize(min(max(w, VR_MIN_W), VR_MAX_W), min(max(h, VR_MIN_H), VR_MAX_H))

    def _load_placement(self):
        s = self.settings
        self.attach_mode = normalize_attach_mode(s.value("attach_mode", DEFAULT_ATTACH))
        try:
            mpp = float(s.value("meters_per_px", DEFAULT_METERS_PER_PX))
        except (TypeError, ValueError):
            mpp = DEFAULT_METERS_PER_PX
        self.meters_per_px = min(max(mpp, MIN_METERS_PER_PX), MAX_METERS_PER_PX)
        try:
            self.scroll_peak = float(s.value("scroll_peak", 0.0))
        except (TypeError, ValueError):
            self.scroll_peak = 0.0
        # pose 是悬浮层中心在当前模式参考系下的姿态；None 表示等拿到头显姿态后再放到正前方。
        self.pose = _pose_from_json(s.value("pose"))
        if self.pose is None:
            self.pose = default_pose(self.attach_mode)

    def _save_geometry(self):
        s = self.settings
        s.setValue("canvas_w", self.width())
        s.setValue("canvas_h", self.height())
        s.setValue("attach_mode", self.attach_mode)
        s.setValue("meters_per_px", self.meters_per_px)
        s.setValue("pose", _pose_to_json(self.pose))

    # ---------------------------------------------------- 标脏 / 推帧 ------
    def _mark_dirty(self):
        self._dirty = True

    def update(self, *args):
        super().update(*args)
        self._dirty = True

    def _render(self):
        super()._render()
        self._dirty = True

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._dirty = True

    def _grab_frame(self) -> QImage:
        scale = supersample_scale(self.width(), self.height())
        self.text.set_render_scale(scale)
        img = QImage(round(self.width() * scale), round(self.height() * scale),
                     QImage.Format_RGBA8888_Premultiplied)
        img.setDevicePixelRatio(scale)
        img.fill(Qt.transparent)
        # 只画控件自身与子控件；不铺窗口底色（背景由 paintEvent 按透明度自绘）。
        self.render(img, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
        return img

    def _snap_highlight(self):
        """(左右边, 上下边) 高亮的不透明度 0..1：抓取中实时；松手后保持再淡出。"""
        snap_pitch, snap_roll = self._snap_axes
        if not (snap_pitch or snap_roll):
            return (0.0, 0.0)
        if self._grab is not None and self._grab["kind"] == "move":
            alpha = 1.0
        elif self._snap_release is None:
            alpha = 0.0
        else:
            t = time.monotonic() - self._snap_release - SNAP_HIGHLIGHT_HOLD
            alpha = 1.0 if t <= 0 else max(0.0, 1.0 - t / SNAP_HIGHLIGHT_FADE)
            alpha = round(alpha * 20) / 20  # 量化，避免同一透明度反复推帧
        if alpha <= 0:
            self._snap_axes = (False, False)
            self._snap_release = None
            return (0.0, 0.0)
        return (alpha if snap_pitch else 0.0, alpha if snap_roll else 0.0)

    def _compose_highlight(self, base: QImage, highlight) -> QImage:
        sides_alpha, ends_alpha = highlight
        if sides_alpha <= 0 and ends_alpha <= 0:
            return base
        img = base.copy()
        w, h = self.width(), self.height()
        inset = SNAP_HIGHLIGHT_WIDTH / 2
        r = ow.BG_RADIUS
        painter = QPainter(img)
        painter.setRenderHint(QPainter.Antialiasing)
        for alpha, lines in (
            # 翻滚吸附：上下两条边（水平了）
            (ends_alpha, ((r, inset, w - r, inset), (r, h - inset, w - r, h - inset))),
            # 俯仰吸附：左右两条边（竖直了）
            (sides_alpha, ((inset, r, inset, h - r), (w - inset, r, w - inset, h - r))),
        ):
            if alpha <= 0:
                continue
            pen = QPen(QColor(*SNAP_HIGHLIGHT_COLOR, round(255 * alpha)), SNAP_HIGHLIGHT_WIDTH)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            for x1, y1, x2, y2 in lines:
                painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        painter.end()
        return img

    def _push_frame(self):
        highlight = self._snap_highlight()  # 隐藏时也推进，淡出结束后才会退回低帧率
        if self._vr_visible and self.vr.connected:
            changed = self._dirty or self._base_frame is None
            if changed:
                self._dirty = False
                self._base_frame = self._grab_frame()
            if changed or highlight != self._last_highlight:
                img = self._compose_highlight(self._base_frame, highlight)
                if self._last_frame is None or img != self._last_frame:
                    if self.vr.set_frame(img):
                        self._last_frame = img
                        self._last_highlight = highlight
                else:
                    self._last_highlight = highlight
        # 抓取中/高亮淡出时提高推帧频率（此时通常只重画高亮，不重新渲染整窗）
        animating = self._grab is not None or self._snap_release is not None
        interval = FRAME_PUSH_FAST_MS if animating else FRAME_PUSH_MS
        if self._frame_timer.interval() != interval:
            self._frame_timer.setInterval(interval)

    # ------------------------------------------------- SteamVR 连接/显隐 --
    def _set_vr_visible(self, visible: bool):
        self._vr_visible = visible
        if visible:
            if self.vr.connected and not self.vr.alive():
                self.vr.disconnect()  # 隐藏期间 SteamVR 悄悄没了
            if self.vr.connected:
                self._show_connected()
            else:
                self._try_connect()
        else:
            self._connect_timer.stop()
            self._end_laser_interaction()
            self.vr.set_visible(False)
        self._update_tick_rate()
        self._report_status()

    def _try_connect(self):
        if not self._vr_visible:
            self._connect_timer.stop()
            return
        if self.vr.connected:
            # 已连接时这个定时器充当心跳：SteamVR 没了就回到等待状态
            if not self.vr.alive():
                self._on_vr_quit()
            return
        if not self.vr.connect():
            # SteamVR 未运行：静默等待，不报错
            if not self._connect_timer.isActive():
                self._connect_timer.start()
            return
        self._show_connected()

    def _show_connected(self):
        self._placed_size = None
        self._yaw_follower.reset()
        self._apply_placement()
        self._last_frame = None
        self._dirty = True
        self._push_frame()
        self.vr.set_visible(True)
        self._update_tick_rate()
        self._report_status()
        self._connect_timer.start()  # 心跳

    def _on_vr_quit(self):
        """SteamVR 被关掉（或崩溃）：断开，开关仍开着就继续静默等它下次启动。"""
        self._end_laser_interaction()
        self.vr.disconnect(acknowledge_quit=True)
        self._update_tick_rate()
        self._report_status()
        if self._vr_visible:
            self._connect_timer.start()
        else:
            self._connect_timer.stop()

    def _update_tick_rate(self):
        active = self._vr_visible and self.vr.connected
        self._tick_timer.setInterval(TICK_ACTIVE_MS if active else TICK_IDLE_MS)

    def _report_status(self):
        connected = bool(self._vr_visible and self.vr.connected)
        if connected == self._reported_connected:
            return
        self._reported_connected = connected
        threading.Thread(
            target=self._post, args=("/vr-overlay", {"action": "report", "connected": connected}),
            daemon=True,
        ).start()

    # ------------------------------------------------------- 姿态 / 放置 ---
    def _hmd_pose(self, poses=None):
        poses = self.vr.device_poses() if poses is None else poses
        return poses.get(0)

    def _frame(self, hmd):
        """当前模式的参考系；身体跟随模式下顺带推进偏航跟随。"""
        if self.attach_mode == ATTACH_BODY:
            now = time.monotonic()
            dt = 0.0 if self._yaw_follow_time is None else now - self._yaw_follow_time
            self._yaw_follow_time = now
            self._yaw_follower.update(hmd_yaw(hmd), dt)
        return mode_frame(self.attach_mode, hmd, self._yaw_follower.yaw)

    def _apply_placement(self, poses=None):
        """把当前尺寸与姿态推给 SteamVR。身体跟随模式需每帧调用（随头显位置/偏航更新）。"""
        if not self.vr.connected:
            return
        size = (self.width(), self.height())
        if self._placed_size != (size, self.meters_per_px):
            self._placed_size = (size, self.meters_per_px)
            self.vr.set_size(size, self.meters_per_px)
        if self.pose is None:
            # 固定在世界但还没有位置：等头显姿态有效后放到正前方
            hmd = self._hmd_pose(poses)
            if hmd is None:
                return
            self.pose = default_pose(self.attach_mode, hmd)
            self._save_geometry()
        if self.attach_mode == ATTACH_HEAD:
            self.vr.set_transform_head_relative(self.pose)
        elif self.attach_mode == ATTACH_BODY:
            hmd = self._hmd_pose(poses)
            if hmd is not None:
                self.vr.set_transform_absolute(mat_mul(self._frame(hmd), self.pose))
        else:
            self.vr.set_transform_absolute(self.pose)

    def _world_pose(self, poses=None):
        """悬浮层当前在站立坐标系中的姿态；拿不到头显姿态时返回 None。"""
        if self.pose is None:
            return None
        if self.attach_mode == ATTACH_WORLD:
            return self.pose
        hmd = self._hmd_pose(poses)
        return None if hmd is None else mat_mul(
            mode_frame(self.attach_mode, hmd, self._yaw_follower.yaw), self.pose)

    def set_attach_mode(self, mode):
        """切换位置模式：悬浮层保持在切换瞬间所在的位置，之后按新模式跟随/固定。"""
        mode = normalize_attach_mode(mode)
        if mode == self.attach_mode:
            return
        self._end_laser_interaction()
        world = self._world_pose() if self.vr.connected else None
        self.attach_mode = mode
        self._yaw_follower.reset()
        hmd = self._hmd_pose() if self.vr.connected else None
        if world is not None and hmd is not None:
            self.pose = mat_mul(mat_inv(self._frame(hmd)), world)
        else:
            self.pose = default_pose(mode, hmd)
        self._save_geometry()
        self._apply_placement()

    def reset_position(self):
        self._end_laser_interaction()
        hmd = self._hmd_pose() if self.vr.connected else None
        self._yaw_follower.reset()
        self.pose = default_pose(self.attach_mode, hmd)
        self._save_geometry()
        self._apply_placement()

    # ------------------------------------------------------- VR 专用按钮 --
    def _step_scale(self, factor: float):
        self.meters_per_px = min(max(self.meters_per_px * factor, MIN_METERS_PER_PX), MAX_METERS_PER_PX)
        self._apply_placement()
        self._save_geometry()

    def _inc_scale(self):
        self._step_scale(SCALE_STEP)

    def _dec_scale(self):
        self._step_scale(1 / SCALE_STEP)

    # ------------------------------------------------------- 射线交互 ------
    def _tick(self):
        for kind, x, y, device in self.vr.poll():
            if kind == "quit":
                self._on_vr_quit()
                return
            if kind == "leave":
                # 射线离开悬浮层后收不到按键松开事件，抓取/缩放就此结束，避免「粘」在手上
                self._end_laser_interaction()
                continue
            if kind == "scroll":
                self._scroll_accum += y   # 菜单里推/拉摇杆（y 为 ydelta）
                self._scroll_stats[0] += 1
                self._scroll_stats[1] += y
                continue
            # 鼠标坐标与画布同向（实测：左上原点、y 向下），不需要翻转
            pos = QPoint(round(x), round(y))
            if kind == "move":
                self._on_laser_move(pos)
            elif kind == "down":
                self._on_laser_down(pos, device)
            elif kind == "up":
                self._on_laser_up(pos)
            elif kind == "grip_down":
                self._on_laser_grip_down(pos, device)
            elif kind == "grip_up":
                self._on_laser_grip_up()
        if not (self._vr_visible and self.vr.connected):
            return
        if self._grab is not None and self._grab["kind"] == "move":
            self._continue_grab()
        elif self._grab is not None and self._grab["kind"] == "resize":
            self._continue_resize()
        elif self.attach_mode == ATTACH_BODY:
            self._apply_placement()
        self._scroll_accum = 0.0

    def _update_button_visibility(self):
        """父类用桌面光标位置决定按钮显隐；VR 里改由射线是否落在悬浮层上决定。"""
        inside = self._laser_inside
        if inside != self._mouse_inside:
            self._mouse_inside = inside
            self.update()
        if inside != self.button_bar.isVisible():
            self.button_bar.setVisible(inside)
            if inside:
                self._reposition_buttons()
                self.button_bar.raise_()
            self._dirty = True

    def _button_at(self, pos):
        if not self.button_bar.isVisible():
            return None
        widget = self.childAt(pos)
        while widget is not None and widget is not self:
            if isinstance(widget, ow.VectorButton):
                return widget if widget.isVisible() else None
            widget = widget.parentWidget()
        return None

    def _set_hover_button(self, button):
        if button is self._hover_button:
            return
        old = self._hover_button
        self._hover_button = button
        if old is not None:
            old.setAttribute(Qt.WA_UnderMouse, False)
            QApplication.sendEvent(old, QEvent(QEvent.Leave))
            old.update()
        if button is not None:
            button.setAttribute(Qt.WA_UnderMouse, True)
            # 交给 ToolTipEventFilter 显示与桌面版一致的悬浮提示
            QApplication.sendEvent(button, QEvent(QEvent.Enter))
            button.update()
        self._dirty = True

    def _edges_at_vr(self, pos):
        m = VR_RESIZE_MARGIN
        return (pos.x() <= m, pos.y() <= m,
                pos.x() >= self.width() - m, pos.y() >= self.height() - m)

    def _on_laser_move(self, pos):
        if not self._laser_inside:
            log.info("laser entered the panel")
            self._laser_inside = True
            self._update_button_visibility()
        if self._grab is not None:
            return  # 抓取/缩放都按手柄姿态逐帧计算，不用光标坐标
        if self._pressed_button is None:
            self._set_hover_button(self._button_at(pos))

    def _on_laser_down(self, pos, device):
        self._on_laser_move(pos)
        if self._grab is not None:
            return  # 已经在抓（例如握持键抓着时又按了扳机）
        button = self._button_at(pos)
        if button is not None:
            if button.isEnabled():
                self._pressed_button = button
                button.setDown(True)
                self._dirty = True
            return
        self._set_hover_button(None)
        edges = self._edges_at_vr(pos)
        if any(edges):
            self._start_resize(pos, edges, device)
        else:
            self._start_grab(pos, device)

    def _on_laser_up(self, pos):
        pressed = self._pressed_button
        if pressed is not None:
            self._pressed_button = None
            pressed.setDown(False)
            if self._button_at(pos) is pressed and pressed.isEnabled():
                pressed.click()
            self._set_hover_button(self._button_at(pos))
            self._dirty = True
        if self._grab is not None and self._grab.get("via") == "trigger":
            self._end_grab()

    def _on_laser_grip_down(self, pos, device):
        """握持键（菜单里是中键）：在悬浮层任何位置按下都是抓取，不点按钮、不缩放。"""
        self._on_laser_move(pos)
        if self._grab is not None:
            return
        if self._pressed_button is not None:
            self._pressed_button.setDown(False)
            self._pressed_button = None
        self._set_hover_button(None)
        log.info("grip grab (menu middle-click) from device %s", device)
        self._start_grab(pos, device, via="grip")

    def _on_laser_grip_up(self):
        if self._grab is not None and self._grab.get("via") == "grip":
            self._end_grab()

    def _end_laser_interaction(self):
        if self._pressed_button is not None:
            self._pressed_button.setDown(False)
            self._pressed_button = None
        if self._grab is not None:
            self._end_grab()
        self._set_hover_button(None)
        if self._laser_inside:
            log.info("laser left the panel")
            self._laser_inside = False
            self._update_button_visibility()

    def _end_grab(self):
        grab, self._grab = self._grab, None
        if grab is None:
            return
        if grab["kind"] == "move" and any(self._snap_axes):
            self._snap_release = time.monotonic()  # 高亮保持一会儿再淡出
        if self._scroll_stats[0]:
            log.info("scroll during grab: %d events, total ydelta %.3f, peak rate %.3f/s",
                     self._scroll_stats[0], self._scroll_stats[1], self.scroll_peak)
            self.settings.setValue("scroll_peak", self.scroll_peak)
        self._scroll_stats = [0, 0.0]
        self._save_geometry()

    # 抓取：悬浮层与手柄刚性相连，随手柄在空间中移动/旋转
    def _laser_hit(self, pos, device, poses):
        """扳机按下处在站立坐标系里的点，以及发出激光的手柄（事件没带来源时挑指着它的那只）。"""
        world = self._world_pose(poses)
        if world is None:
            return None, None
        lx, ly = canvas_to_local(pos.x(), pos.y(), (self.width(), self.height()), self.meters_per_px)
        hit_point = _point(mat_mul(world, translation(lx, ly, 0.0)))
        controllers = self.vr.controller_indices()
        if device not in controllers or device not in poses:
            device = pick_pointing_device(
                {i: p for i, p in poses.items() if i in controllers}, hit_point)
        if device is None or device not in poses:
            return hit_point, None
        return hit_point, device

    def _start_grab(self, pos, device, via="trigger"):
        """扳机/握持键抓取：从射线事件里确定是哪只手柄。"""
        poses = self.vr.device_poses()
        _, device = self._laser_hit(pos, device, poses)
        if device is None:
            return
        self._begin_move_grab(device, poses, via=via)

    def _begin_move_grab(self, device, poses, via):
        world = self._world_pose(poses)
        if world is None or device not in poses:
            return
        self._grab = {
            "kind": "move",
            "via": via,
            "device": device,
            "relative": mat_mul(mat_inv(poses[device]), world),
            "time": time.monotonic(),
        }
        self._snap_axes = (False, False)
        self._snap_release = None

    def _continue_grab(self):
        grab = self._grab
        now = time.monotonic()
        dt, grab["time"] = min(now - grab["time"], 0.1), now
        # 推/拉摇杆：沿手柄→悬浮层的连线调整距离。菜单里摇杆只能从滚动事件拿到，
        # 先平滑再按峰值归一化
        raw = self._scroll_accum / dt if dt > 0 else 0.0
        k = 1.0 - math.exp(-dt / SCROLL_SMOOTHING_SECONDS) if dt > 0 else 0.0
        grab["scroll"] = grab.get("scroll", 0.0) + (raw - grab.get("scroll", 0.0)) * k
        self.scroll_peak = max(self.scroll_peak, abs(grab["scroll"]))
        rate = scroll_push_rate(grab["scroll"], self.scroll_peak)
        if rate:
            grab["relative"] = push_relative(grab["relative"], rate, dt)

        poses = self.vr.device_poses()
        controller = poses.get(grab["device"])
        hmd = self._hmd_pose(poses)
        if controller is None or (self.attach_mode != ATTACH_WORLD and hmd is None):
            return
        world = mat_mul(controller, grab["relative"])
        if self.attach_mode != ATTACH_WORLD:
            # 在模式参考系里吸附：世界/身体跟随相对重力竖直，跟随头部相对视野竖直
            world = mat_mul(mat_inv(self._frame(hmd)), world)
        self.pose, snap_pitch, snap_roll = snap_upright_axes(world)
        self._snap_axes = (snap_pitch, snap_roll)
        self._apply_placement(poses)

    # 缩放：在悬浮层自身平面内移动被拖的边，对边不动。
    # 不用光标坐标：每次改尺寸后 SteamVR 还会按旧的几何算几帧光标，用它会正反馈成「橡皮筋」。
    # 改为记下按下时激光相对手柄的方向，之后逐帧用手柄姿态求射线与（起始）平面的交点。
    def _current_frame(self, hmd):
        if self.attach_mode == ATTACH_WORLD:
            return IDENTITY
        if hmd is None:
            return None
        return mode_frame(self.attach_mode, hmd, self._yaw_follower.yaw)

    def _start_resize(self, pos, edges, device=None):
        if self.pose is None:
            return
        poses = self.vr.device_poses()
        hit_point, device = self._laser_hit(pos, device, poses)
        if device is None:
            return
        direction = laser_direction_in_controller(poses[device], hit_point)
        if direction is None:
            return
        size = (self.width(), self.height())
        self._grab = {
            "kind": "resize",
            "via": "trigger",
            "device": device,
            "direction": direction,
            "edges": edges,
            "start_pose": self.pose,
            "start_size": size,
            "start_local": canvas_to_local(pos.x(), pos.y(), size, self.meters_per_px),
        }

    def _continue_resize(self):
        grab = self._grab
        poses = self.vr.device_poses()
        controller = poses.get(grab["device"])
        frame = self._current_frame(self._hmd_pose(poses))
        if controller is None or frame is None:
            return
        plane = mat_mul(frame, grab["start_pose"])
        hit = ray_hit_in_plane(plane, _point(controller), _rotate(controller, grab["direction"]))
        if hit is None:
            return
        sx, sy = grab["start_local"]
        size, center = resize_in_plane(
            grab["edges"], grab["start_size"], (0.0, 0.0), (hit[0] - sx, hit[1] - sy),
            self.meters_per_px)
        self.pose = mat_mul(grab["start_pose"], translation(center[0], center[1], 0.0))
        if size != (self.width(), self.height()):
            self.resize(*size)
            self._reposition_buttons()
            self._apply_placement(poses)
            # 立刻推新帧：悬浮层宽度（米）与纹理 bounds 同时更新，不会有几帧被拉伸
            self._push_frame()
        else:
            self._apply_placement(poses)

    # ------------------------------------------------------- 消息 / 关闭 ---
    def _on_message(self, raw: str):
        try:
            data = json.loads(raw)
        except Exception:
            return
        mtype = data.get("type")
        if mtype == "overlay_visibility":
            return  # 那是桌面悬浮窗的显隐
        if mtype == "vr_overlay_visibility":
            self._set_vr_visible(bool(data.get("visible")))
            return
        if mtype == "vr_overlay_config":
            self.set_attach_mode(data.get("attach_mode"))
            return
        if mtype == "vr_overlay_reset_position":
            self.reset_position()
            return
        super()._on_message(raw)

    def closeEvent(self, event):
        event.ignore()
        self._save_geometry()
        self._set_vr_visible(False)
        threading.Thread(
            target=self._post, args=("/vr-overlay", {"action": "close", "source": "vr_overlay"}),
            daemon=True,
        ).start()

    def shutdown_vr(self):
        self.vr.set_visible(False)
        self.vr.disconnect()


def _setup_log_file():
    """诊断日志：%LOCALAPPDATA%/RealtimeSubtitle/vr_overlay.log（每次启动覆盖）。"""
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    folder = os.path.join(base, "RealtimeSubtitle")
    try:
        os.makedirs(folder, exist_ok=True)
        handler = logging.FileHandler(os.path.join(folder, "vr_overlay.log"), mode="w",
                                      encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Realtime subtitle SteamVR overlay")
    parser.add_argument("--url", required=True, help="服务器地址，如 http://127.0.0.1:8000")
    args, _ = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    _setup_log_file()
    app = QApplication.instance() or QApplication(sys.argv)
    ow.load_bundled_fonts()
    app.setStyle(ow.InstantToolTipStyle(app.style()))
    app.setQuitOnLastWindowClosed(False)
    win = VrOverlayWindow(args.url)
    app.aboutToQuit.connect(win.shutdown_vr)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
