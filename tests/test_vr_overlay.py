import asyncio
import ctypes
import functools
import json
import math
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:  # 与 test_web_server_security 一样先导入，避免 patch.dict 还原 sys.modules 后被重复导入
    import numpy  # noqa: F401
    import soundcard  # noqa: F401
except ImportError:
    pass

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPoint, QSettings
from PySide6.QtWidgets import QApplication

import vr_overlay as vo


APP = QApplication.instance() or QApplication([])


def close(a, b, tol=1e-6):
    return all(abs(x - y) < tol for x, y in zip(a, b))


def degrees(*values):
    return [math.degrees(v) for v in values]


# ---------------------------------------------------------------- geometry --
def test_ypr_round_trip_and_inverse():
    m = vo.rotation_ypr(0.7, 0.2, -0.3, (1.0, 2.0, 3.0))
    assert close(vo.decompose_ypr(m), (0.7, 0.2, -0.3))
    identity = vo.mat_mul(vo.mat_inv(m), m)
    assert all(close(identity[i], vo.IDENTITY[i]) for i in range(3))


def test_snap_upright_only_near_vertical():
    snapped = vo.snap_upright(vo.rotation_ypr(0.7, math.radians(2), math.radians(-2.5), (1, 2, 3)))
    assert close(vo.decompose_ypr(snapped), (0.7, 0.0, 0.0))
    assert close([r[3] for r in snapped], (1, 2, 3))

    tilted = vo.snap_upright(vo.rotation_ypr(0.7, math.radians(20), math.radians(1)))
    assert close(degrees(*vo.decompose_ypr(tilted))[1:], (20.0, 0.0), 1e-6)


def test_default_poses_face_user_slightly_below_eye_level():
    hmd = vo.rotation_ypr(math.radians(90), math.radians(30), 0.0, (1.0, 1.6, 2.0))
    assert vo.default_pose(vo.ATTACH_HEAD) == vo.translation(*vo.DEFAULT_LOCAL_POSITION)
    assert vo.default_pose(vo.ATTACH_WORLD) is None

    world = vo.default_pose(vo.ATTACH_WORLD, hmd)
    # Head pitch is ignored: the panel sits level, 1.4 m along the gaze yaw (-X here).
    assert close([r[3] for r in world], (1.0 - 1.4, 1.35, 2.0))
    assert close(vo.decompose_ypr(world), (math.radians(90), 0.0, 0.0))

    body = vo.default_pose(vo.ATTACH_BODY, hmd)
    assert close([r[3] for r in body], (0.0, 1.35, -1.4))


def test_body_frame_locks_height_and_ignores_pitch_roll():
    hmd = vo.rotation_ypr(0.4, 0.5, 0.3, (1.0, 1.7, -2.0))
    frame = vo.mode_frame(vo.ATTACH_BODY, hmd, body_yaw=0.4)
    assert close([r[3] for r in frame], (1.0, 0.0, -2.0))
    assert close(vo.decompose_ypr(frame), (0.4, 0.0, 0.0))


def test_resize_keeps_opposite_edge_fixed_and_clamps():
    size, center = vo.resize_in_plane(
        (False, False, True, False), (860, 240), (0.0, 0.0), (0.11, 0.0), 0.0011)
    assert size == (960, 240)
    assert close(center, (0.055, 0.0))

    size, center = vo.resize_in_plane(
        (True, True, False, False), (860, 240), (0.0, 0.0), (5.0, -5.0), 0.0011)
    assert size == (vo.VR_MIN_W, vo.VR_MIN_H)
    # right and bottom edges stay where they were
    assert math.isclose(center[0] + vo.VR_MIN_W * 0.0011 / 2, 860 * 0.0011 / 2)
    assert math.isclose(center[1] - vo.VR_MIN_H * 0.0011 / 2, -240 * 0.0011 / 2)


def test_pick_pointing_device_prefers_controller_aimed_at_target():
    forward = vo.translation(0.0, 1.0, 0.0)  # looks down -Z
    backward = vo.rotation_ypr(math.pi, 0.0, 0.0, (0.0, 1.0, 0.0))
    assert vo.pick_pointing_device({3: forward, 4: backward}, (0.0, 1.0, -2.0)) == 3
    assert vo.pick_pointing_device({3: forward, 4: backward}, (0.0, 1.0, 2.0)) == 4


def test_normalize_attach_mode_defaults_to_world():
    assert vo.normalize_attach_mode("HEAD") == "head"
    assert vo.normalize_attach_mode("bogus") == "body"
    assert vo.normalize_attach_mode(None) == "body"


# ------------------------------------------------------------ yaw follower --
def run(follower, start, end, seconds, dt=0.011):
    steps = max(1, round(seconds / dt))
    for i in range(1, steps + 1):
        follower.update(math.radians(start + (end - start) * i / steps), dt)
    return math.degrees(follower.yaw)


def test_yaw_follower_drifts_slowly_on_a_glance():
    f = vo.YawFollower()
    f.update(0.0, 0.011)
    moved = run(f, 0, 18, 0.3)                # quick glance, still under the threshold
    assert 0.0 < moved < 2.0                  # follows, but only a little
    assert not f.recentering
    held = run(f, 18, 18, 1.0)                # keep looking to the side
    assert moved < held < 18 * 0.5            # keeps drifting slowly toward the gaze
    assert not f.recentering


def test_yaw_follower_tracks_a_turn_without_lagging_then_settles():
    f = vo.YawFollower()
    f.update(0.0, 0.011)
    run(f, 0, 90, 1.0)                       # steady 90 deg/s turn
    assert f.recentering
    assert 90 - math.degrees(f.yaw) < 2.0    # does not trail behind the head
    final = run(f, 90, 90, 1.0)              # head stops
    assert final == pytest.approx(90, abs=vo.BODY_YAW_SETTLE_DEGREES)
    assert not f.recentering
    # back to the slow drift: a small glance barely moves it
    assert abs(run(f, 90, 80, 0.3) - final) < 1.0


def test_yaw_follower_speed_changes_smoothly_without_overshoot():
    def ease(t):
        t = max(0.0, min(1.0, t))
        return t * t * (3 - 2 * t)

    for amplitude, seconds in ((40, 0.6), (90, 0.6), (90, 0.3)):
        f = vo.YawFollower()
        f.update(0.0, 0.011)
        prev, prev_step, worst, peak = 0.0, 0.0, 0.0, 0.0
        for i in range(1, 300):               # a natural turn: speeds up, then slows down
            f.update(math.radians(amplitude * ease(i * 0.011 / seconds)), 0.011)
            yaw = math.degrees(f.yaw)
            step = yaw - prev
            worst = max(worst, abs(step - prev_step))
            peak = max(peak, yaw)
            prev, prev_step = yaw, step
        assert worst < 0.6, amplitude          # no sudden yank when switching speeds
        assert peak - amplitude < 0.5, amplitude   # does not swing past the head
        assert not f.recentering


def test_yaw_follower_wraps_around_pi():
    f = vo.YawFollower()
    f.update(math.radians(170), 0.011)
    run(f, 170, 230, 0.6)
    run(f, 230, 230, 1.5)
    assert math.degrees(f.yaw) % 360 == pytest.approx(230, abs=vo.BODY_YAW_SETTLE_DEGREES)


# ------------------------------------------------------------- the window --
@pytest.fixture
def window(monkeypatch):
    QSettings("RealtimeSubtitle", "VROverlayTest").clear()
    monkeypatch.setattr(vo.VrOverlayWindow, "SETTINGS_NAME", "VROverlayTest")
    monkeypatch.setattr(vo.VrOverlayWindow, "_init_ws", lambda self: None)
    posts = []
    monkeypatch.setattr(vo.VrOverlayWindow, "_post",
                        lambda self, path, payload=None: posts.append((path, payload)) or True)
    win = vo.VrOverlayWindow("http://127.0.0.1:1")
    win.posts = posts
    yield win
    for timer in (win._hover_timer, win._tick_timer, win._frame_timer,
                  win._safety_timer, win._connect_timer):
        timer.stop()
    win.deleteLater()
    QSettings("RealtimeSubtitle", "VROverlayTest").clear()


def test_window_uses_separate_settings_and_drops_click_through(window):
    assert window.settings.applicationName() == "VROverlayTest"
    assert window.btn_passthrough not in window._all_buttons
    assert window.btn_scale_inc in window._all_buttons
    assert window.attach_mode == vo.ATTACH_BODY


def test_offscreen_frame_draws_every_visible_button(window):
    window._laser_inside = True
    window._update_button_visibility()
    image = window._grab_frame()
    assert image.width() == round(window.width() * image.devicePixelRatio())
    # every button the laser can hit is actually rendered (none hidden by graphics effects)
    for button in window._all_buttons:
        assert button.graphicsEffect() is None
        center = button.mapTo(window, button.rect().center())
        assert window._button_at(center) is button


def test_laser_events_use_canvas_coordinates_without_flipping(window):
    fake = FakeBackend()
    fake.steamvr_running = True
    window.vr = fake
    window._on_message(json.dumps({"type": "vr_overlay_visibility", "visible": True}))
    window._on_laser_move(QPoint(10, 10))     # show the button bar
    center = window.btn_font_inc.mapTo(window, window.btn_font_inc.rect().center())
    fake.events.append(("move", float(center.x()), float(center.y()), 3))
    window._tick()
    assert window._hover_button is window.btn_font_inc


def test_laser_click_on_button_triggers_it(window):
    window._on_laser_move(QPoint(10, 10))
    pos = window.btn_font_inc.mapTo(window, window.btn_font_inc.rect().center())
    before = window.font_size
    window._on_laser_down(pos, device=1)
    window._on_laser_up(pos)
    assert window.font_size == before + 2


def test_gl_upload_layout_is_centred_so_either_v_direction_selects_it():
    for w, h in ((1722, 280), (1722, 281), (3200, 1800), (960, 1799)):
        x0, y0, rw, rh, (u0, v0, u1, v1) = vo.gl_upload_layout(w, h)
        assert 0 <= x0 and x0 + rw <= vo.GL_TEXTURE_W
        assert 0 <= y0 and y0 + rh <= vo.GL_TEXTURE_H
        assert y0 == vo.GL_TEXTURE_H - (y0 + rh)            # exactly centred vertically
        assert v0 == pytest.approx(1.0 - v1)                # bounds symmetric about 0.5
        assert (u1 - u0) * vo.GL_TEXTURE_W == pytest.approx(w)
        assert (v1 - v0) * vo.GL_TEXTURE_H - h in (pytest.approx(0), pytest.approx(1))


def test_gl_uploader_keeps_one_texture_size_and_frames_the_content(window):
    from PySide6.QtGui import QColor, QImage
    uploader = vo.GlTextureUploader()
    if not uploader._ensure():
        pytest.skip("no desktop OpenGL context on this platform")

    submitted, bounds = [], []
    overlay = MagicMock()
    overlay.setOverlayTexture.side_effect = lambda handle, tex: submitted.append(
        (handle, tex.handle, tex.eType))
    overlay.setOverlayTextureBounds.side_effect = lambda handle, b: bounds.append(
        (b.uMin, b.vMin, b.uMax, b.vMax))

    small = QImage(4, 2, QImage.Format_RGBA8888_Premultiplied)
    small.fill(QColor(0, 0, 0, 0))
    small.setPixelColor(0, 0, QColor(255, 0, 0, 255))   # top-left
    small.setDevicePixelRatio(2.0)                      # as rendered (supersampled)
    assert uploader.submit(overlay, 7, small)
    taller = QImage(4, 6, QImage.Format_RGBA8888_Premultiplied)
    taller.fill(QColor(0, 0, 0, 0))
    assert uploader.submit(overlay, 7, taller)           # canvas grew: same textures, new bounds
    (h1, tex1, kind), (_, tex2, _) = submitted
    assert h1 == 7 and kind == vo.openvr.TextureType_OpenGL
    assert tex1 != tex2  # never overwrite the texture SteamVR may still be reading
    assert bounds == [pytest.approx(vo.gl_upload_layout(4, 2)[4]),
                      pytest.approx(vo.gl_upload_layout(4, 6)[4])]

    gl = uploader._gl
    W, H = vo.GL_TEXTURE_W, vo.GL_TEXTURE_H
    pixels = (ctypes.c_ubyte * (W * H * 4))()
    gl.glBindTexture(vo._GL_TEXTURE_2D, tex1)
    gl.glGetTexImage(vo._GL_TEXTURE_2D, 0, vo._GL_RGBA, vo._GL_UNSIGNED_BYTE, pixels)
    x0, y0, rw, rh, _ = vo.gl_upload_layout(4, 2)

    def px(x, y):
        i = (y * W + x) * 4
        return list(pixels[i:i + 4])

    # GL row 0 is the bottom: the image's top-left pixel sits at the region's second row from
    # its top, one pixel in from the left; the 1px border around it is transparent.
    top_row = y0 + rh - 2
    assert px(x0 + 1, top_row) == [255, 0, 0, 255]
    assert px(x0, top_row) == [0, 0, 0, 0]
    assert px(x0 + 1, top_row + 1) == [0, 0, 0, 0]
    assert px(x0 + 1, top_row - 1) == [0, 0, 0, 0]


def test_gl_mouse_coordinates_map_back_to_the_canvas():
    canvas = (861, 140)
    image_size = (1722, 280)                         # 2x supersampled frame
    mouse_map = vo.gl_mouse_map(*image_size, canvas)
    x0, y0, _, _, _ = vo.gl_upload_layout(*image_size)
    for cx, cy in ((0, 0), (100, 50), (861, 140)):
        # SteamVR reports texture-pixel coordinates (mouse scale = texture size)
        tx, ty = x0 + 1 + cx * 2, y0 + 1 + cy * 2
        assert vo.mouse_to_canvas(tx, ty, mouse_map) == pytest.approx((cx, cy))
    assert vo.mouse_to_canvas(12.0, 34.0, None) == (12.0, 34.0)


def scale_calls(overlay):
    return [(c.args[1].v[0], c.args[1].v[1]) for c in overlay.setOverlayMouseScale.call_args_list]


def test_gl_frames_use_a_texture_sized_mouse_scale(monkeypatch):
    from PySide6.QtGui import QImage
    backend = vo.VrBackend()
    backend.overlay, backend.handle = MagicMock(), 3
    backend.set_size((861, 140), 0.001)
    assert scale_calls(backend.overlay)[-1] == (861, 140)

    monkeypatch.setattr(backend._uploader, "submit", lambda *a: True)
    image = QImage(1722, 280, QImage.Format_RGBA8888_Premultiplied)
    assert backend.set_frame(image)
    assert scale_calls(backend.overlay)[-1] == (vo.GL_TEXTURE_W, vo.GL_TEXTURE_H)
    assert backend._mouse_map == vo.gl_mouse_map(1722, 280, (861, 140))
    backend.set_size((861, 200), 0.001)               # resizing keeps the texture-sized scale
    assert scale_calls(backend.overlay)[-1] == (vo.GL_TEXTURE_W, vo.GL_TEXTURE_H)

    monkeypatch.setattr(backend._uploader, "submit", lambda *a: False)
    backend._uploader.used = True
    assert backend.set_frame(image)                   # fell back to SetOverlayRaw
    assert backend._mouse_map is None
    assert scale_calls(backend.overlay)[-1] == (861, 200)


def test_set_frame_falls_back_to_raw_without_gl(monkeypatch):
    from PySide6.QtGui import QImage
    backend = vo.VrBackend()
    backend.overlay, backend.handle = MagicMock(), 3
    monkeypatch.setattr(backend._uploader, "submit", lambda *a: False)
    image = QImage(2, 2, QImage.Format_RGBA8888_Premultiplied)
    image.fill(0)
    backend._uploader.used = True                  # GL path had set texture bounds
    assert backend.set_frame(image)
    backend.overlay.setOverlayRaw.assert_called_once()
    (_, full), _ = backend.overlay.setOverlayTextureBounds.call_args
    assert (full.uMin, full.vMin, full.uMax, full.vMax) == (0.0, 0.0, 1.0, 1.0)


def test_visibility_frame_waits_silently_without_steamvr(window, monkeypatch):
    monkeypatch.setattr(window.vr, "connect", lambda: False)
    window._on_message(json.dumps({"type": "vr_overlay_visibility", "visible": True}))
    assert window._connect_timer.isActive()
    assert window.posts == []  # nothing to report: still "not connected"
    window._on_message(json.dumps({"type": "vr_overlay_visibility", "visible": False}))
    assert not window._connect_timer.isActive()


class FakeBackend:
    """Stands in for VrBackend: SteamVR availability is a flag the test flips."""

    def __init__(self):
        self.steamvr_running = False
        self.connected = False
        self.visible = False
        self.events = []
        self.disconnects = 0
        self.poses = {0: vo.translation(0.0, 1.6, 0.0)}

    def connect(self):
        self.connected = self.steamvr_running
        return self.connected

    def disconnect(self, acknowledge_quit=False):
        self.connected = False
        self.visible = False
        self.disconnects += 1

    def alive(self):
        return self.connected and self.steamvr_running

    def set_visible(self, visible):
        self.visible = visible

    def set_size(self, *args):
        pass

    def set_transform_absolute(self, m):
        pass

    def set_transform_head_relative(self, m):
        pass

    def set_frame(self, image):
        return True

    def device_poses(self):
        return dict(self.poses)

    def controller_indices(self):
        return set(self.poses) - {0}

    def poll(self):
        events, self.events = self.events, []
        return events


def test_steamvr_quit_goes_back_to_waiting_then_reconnects(window):
    fake = FakeBackend()
    window.vr = fake
    window._on_message(json.dumps({"type": "vr_overlay_visibility", "visible": True}))
    assert not fake.connected and window._connect_timer.isActive()

    fake.steamvr_running = True          # user starts SteamVR
    window._try_connect()
    assert fake.connected and fake.visible
    assert window.posts[-1] == ("/vr-overlay", {"action": "report", "connected": True})
    assert window.pose is not None       # world mode got placed in front of the headset

    fake.steamvr_running = False         # user quits SteamVR
    fake.events.append(("quit", 0.0, 0.0, None))
    window._tick()
    assert not fake.connected
    assert window._connect_timer.isActive()   # keeps waiting, no error
    assert window.posts[-1] == ("/vr-overlay", {"action": "report", "connected": False})

    window._try_connect()                # still not running: stays quiet
    assert not fake.connected and window._connect_timer.isActive()

    fake.steamvr_running = True          # next SteamVR launch
    window._try_connect()
    assert fake.connected and fake.visible
    assert window.posts[-1] == ("/vr-overlay", {"action": "report", "connected": True})


def test_heartbeat_detects_steamvr_crash_without_quit_event(window):
    fake = FakeBackend()
    fake.steamvr_running = True
    window.vr = fake
    window._on_message(json.dumps({"type": "vr_overlay_visibility", "visible": True}))
    assert fake.connected and window._connect_timer.isActive()   # heartbeat running

    fake.steamvr_running = False         # crash: no quit event
    window._try_connect()                # heartbeat tick
    assert fake.disconnects == 1 and not fake.connected
    assert window._connect_timer.isActive()


def test_config_frame_switches_mode_and_persists(window):
    window._on_message(json.dumps({"type": "vr_overlay_config", "attach_mode": "head"}))
    assert window.attach_mode == vo.ATTACH_HEAD
    assert window.pose == vo.translation(*vo.DEFAULT_LOCAL_POSITION)
    assert window.settings.value("attach_mode") == "head"


def test_desktop_overlay_visibility_frame_is_ignored(window):
    window._on_message(json.dumps({"type": "overlay_visibility", "visible": False}))
    assert window.isVisible()


# ---------------------------------------------------------- server endpoint --
def async_test(coro):
    @functools.wraps(coro)
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro(*args, **kwargs))
        finally:
            loop.close()
    return wrapper


class FakeVrManager:
    def __init__(self):
        self.is_visible = False
        self.connected = False
        self.attach_mode = "body"
        self.opened = 0
        self.alive = False

    def is_open(self):
        return self.alive

    def open(self, hidden=True):
        self.opened += 1
        self.alive = True
        return True


@pytest.fixture
def web_server_module():
    with patch.dict(sys.modules, {
        "aiohttp": MagicMock(), "aiohttp.web": MagicMock(),
        "config": MagicMock(), "llm_client": MagicMock(),
    }):
        sys.modules.pop("web_server", None)
        import web_server as module
        module.LOCK_MANUAL_CONTROLS = False
        module.web.json_response.side_effect = lambda data, status=200: (data, status)
        yield module
        sys.modules.pop("web_server", None)


def make_server(module):
    server = module.WebServer(MagicMock(), MagicMock())
    server.vr_overlay_manager = FakeVrManager()
    server.broadcast_to_clients = AsyncMock()
    return server


def request(payload):
    req = AsyncMock()
    req.remote = "127.0.0.1"
    req.json.return_value = payload
    return req


@async_test
async def test_vr_overlay_open_spawns_process_and_reports_waiting(web_server_module):
    server = make_server(web_server_module)
    data, status = await server.vr_overlay_post_handler(request({"action": "open"}))
    assert status == 200
    assert data["open"] is True and data["connected"] is False
    assert server.vr_overlay_manager.opened == 1
    server.broadcast_to_clients.assert_awaited_with(
        {"type": "vr_overlay_visibility", "visible": True, "connected": False})

    await server.vr_overlay_post_handler(request({"action": "report", "connected": True}))
    server.broadcast_to_clients.assert_awaited_with(
        {"type": "vr_overlay_visibility", "visible": True, "connected": True})


@async_test
async def test_vr_overlay_close_from_overlay_is_flagged(web_server_module):
    server = make_server(web_server_module)
    await server.vr_overlay_post_handler(request({"action": "open"}))
    await server.vr_overlay_post_handler(request({"action": "close", "source": "vr_overlay"}))
    frame = server.broadcast_to_clients.await_args.args[0]
    assert frame["visible"] is False and frame["closed_from_overlay"] is True


@async_test
async def test_vr_overlay_config_validates_and_broadcasts(web_server_module):
    server = make_server(web_server_module)
    data, status = await server.vr_overlay_post_handler(
        request({"action": "config", "attach_mode": "body"}))
    assert status == 200 and server.vr_overlay_manager.attach_mode == "body"
    server.broadcast_to_clients.assert_awaited_with(
        {"type": "vr_overlay_config", "attach_mode": "body"})

    _, status = await server.vr_overlay_post_handler(
        request({"action": "config", "attach_mode": "sideways"}))
    assert status == 400


@async_test
async def test_watchdog_respawns_vr_process_killed_while_switch_is_on(web_server_module):
    server = make_server(web_server_module)
    manager = server.vr_overlay_manager
    assert await server._check_vr_overlay_process() is False   # switch off: nothing to do

    await server.vr_overlay_post_handler(request({"action": "open"}))
    manager.connected = True
    manager.alive = False                # SteamVR exit took the process down
    assert await server._check_vr_overlay_process() is True
    assert manager.opened == 2 and manager.connected is False
    server.broadcast_to_clients.assert_awaited_with(
        {"type": "vr_overlay_visibility", "visible": True, "connected": False})

    manager.alive = False                # dies again right away: throttled
    assert await server._check_vr_overlay_process() is False
    assert manager.opened == 2


@async_test
async def test_vr_overlay_unavailable_without_manager(web_server_module):
    server = make_server(web_server_module)
    server.vr_overlay_manager = None
    data, _ = await server.vr_overlay_get_handler(request({}))
    assert data["available"] is False


# ------------------------------------------------- grip grab / push / snap --
def test_push_relative_moves_along_the_controller_ray_and_clamps():
    rel = vo.mat_mul(vo.translation(0.0, 0.0, -1.0), vo.rotation_ypr(0.3, 0.0, 0.0))
    farther = vo.push_relative(rel, math.log(2), 1.0)
    assert [farther[i][3] for i in range(3)] == pytest.approx([0.0, 0.0, -2.0])
    assert [row[:3] for row in farther] == [row[:3] for row in rel]   # orientation kept
    nearest = vo.push_relative(rel, -50.0, 1.0)
    assert -nearest[2][3] == pytest.approx(vo.GRAB_MIN_DISTANCE)
    farthest = vo.push_relative(rel, 50.0, 1.0)
    assert -farthest[2][3] == pytest.approx(vo.GRAB_MAX_DISTANCE)


def test_snap_upright_axes_reports_which_axis_snapped():
    m = vo.rotation_ypr(0.5, math.radians(4.0), math.radians(12.0), (1, 2, 3))
    snapped, pitch, roll = vo.snap_upright_axes(m)
    assert (pitch, roll) == (True, False)
    assert degrees(*vo.decompose_ypr(snapped)) == pytest.approx(
        [math.degrees(0.5), 0.0, 12.0], abs=1e-6)
    _, pitch, roll = vo.snap_upright_axes(vo.rotation_ypr(0.0, 0.3, 0.3))
    assert (pitch, roll) == (False, False)


def test_body_follow_settles_quickly_after_the_head_stops():
    f = vo.YawFollower()
    f.reset(0.0)
    run(f, 0, 60, 0.5)                       # turn 60 degrees in half a second
    t = 0.0
    while f.recentering and t < 2.0:
        f.update(math.radians(60), 0.011)
        t += 0.011
    assert not f.recentering
    assert t < 0.4
    assert abs(math.degrees(f.yaw) - 60) <= vo.BODY_YAW_SETTLE_DEGREES


@pytest.fixture
def grabbable(window):
    """SteamVR menu open; controller 3 drives the laser."""
    fake = FakeBackend()
    fake.steamvr_running = True
    fake.poses[3] = vo.translation(0.2, 1.2, -0.3)   # a controller
    window.vr = fake
    window._on_message(json.dumps({"type": "vr_overlay_visibility", "visible": True}))
    assert fake.connected
    window._tick()                                    # places the panel in front of the headset
    return window, fake


def button_center(window, button):
    window._on_laser_move(QPoint(10, 10))             # laser on the panel shows the button bar
    c = button.mapTo(window, button.rect().center())
    return float(c.x()), float(c.y())


def test_menu_grip_grabs_even_over_a_button_without_clicking_it(grabbable):
    window, fake = grabbable
    x, y = button_center(window, window.btn_font_inc)
    before = window.font_size
    fake.events.append(("move", x, y, 3))
    fake.events.append(("grip_down", x, y, 3))        # grip = middle click in the menu
    window._tick()
    assert window._grab["kind"] == "move" and window._grab["via"] == "grip"
    assert window._hover_button is None and window._pressed_button is None

    fake.events.append(("down", x, y, 3))             # trigger while gripping: ignored
    fake.events.append(("up", x, y, 3))
    window._tick()
    assert window._grab is not None                   # trigger release doesn't drop a grip grab

    fake.events.append(("grip_up", x, y, 3))
    window._tick()
    assert window._grab is None
    assert window.font_size == before


def test_menu_grip_on_an_edge_moves_instead_of_resizing(grabbable):
    window, fake = grabbable
    w, h = window.width(), window.height()
    fake.events.append(("grip_down", float(w // 2), float(h - 3), 3))
    window._tick()
    assert window._grab["kind"] == "move"


def test_laser_leaving_the_panel_drops_a_grip_grab(grabbable):
    window, fake = grabbable
    fake.events.append(("grip_down", 200.0, 100.0, 3))
    window._tick()
    assert window._grab is not None
    fake.events.append(("leave", 0.0, 0.0, 3))
    window._tick()
    assert window._grab is None


def distance(window):
    rel = window._grab["relative"]
    return math.sqrt(sum(rel[i][3] ** 2 for i in range(3)))


@pytest.mark.parametrize("press, release", [("down", "up"), ("grip_down", "grip_up")])
def test_scroll_events_push_the_grabbed_panel(grabbable, monkeypatch, press, release):
    window, fake = grabbable
    clock = [1000.0]
    monkeypatch.setattr(vo.time, "monotonic", lambda: clock[0])
    fake.events.append((press, 200.0, 100.0, 3))
    window._tick()
    assert window._grab["kind"] == "move"
    start = distance(window)
    for _ in range(30):                           # stick pushed forward in the menu
        fake.events.append(("scroll", 0.0, 0.4, 3))
        clock[0] += 0.011
        window._tick()
    assert distance(window) > start * 1.05
    farthest = distance(window)
    for _ in range(30):                           # pulled back
        fake.events.append(("scroll", 0.0, -0.4, 3))
        clock[0] += 0.011
        window._tick()
    assert distance(window) < farthest
    fake.events.append((release, 200.0, 100.0, 3))
    window._tick()
    assert window._grab is None
    assert window.settings.value("scroll_peak") is not None   # calibration remembered


def test_scroll_push_rate_normalises_by_the_peak():
    assert vo.scroll_push_rate(0.0, 10.0) == 0.0
    assert vo.scroll_push_rate(1.0, 10.0) == 0.0             # inside the small dead zone
    assert vo.scroll_push_rate(10.0, 10.0) == pytest.approx(vo.GRAB_PUSH_MAX_RATE)
    assert vo.scroll_push_rate(-10.0, 10.0) == pytest.approx(-vo.GRAB_PUSH_MAX_RATE)


def test_snap_highlight_holds_after_release_then_fades(grabbable, monkeypatch):
    window, fake = grabbable
    clock = [1000.0]
    monkeypatch.setattr(vo.time, "monotonic", lambda: clock[0])
    fake.events.append(("grip_down", 200.0, 100.0, 3))
    window._tick()
    assert window._snap_axes == (True, True)      # panel was placed upright
    assert window._snap_highlight() == (1.0, 1.0)
    window._push_frame()
    assert window._frame_timer.interval() == vo.FRAME_PUSH_FAST_MS

    fake.events.append(("grip_up", 200.0, 100.0, 3))
    window._tick()                                # released
    clock[0] += vo.SNAP_HIGHLIGHT_HOLD - 0.01
    assert window._snap_highlight() == (1.0, 1.0)
    clock[0] += 0.01 + vo.SNAP_HIGHLIGHT_FADE / 2
    assert window._snap_highlight() == pytest.approx((0.5, 0.5))
    clock[0] += vo.SNAP_HIGHLIGHT_FADE
    assert window._snap_highlight() == (0.0, 0.0)
    window._push_frame()
    assert window._frame_timer.interval() == vo.FRAME_PUSH_MS


def test_ray_hit_in_plane_and_laser_direction():
    plane = vo.rotation_ypr(0.4, 0.1, 0.0, (0.5, 1.5, -2.0))
    target = vo.mat_mul(plane, vo.translation(0.3, -0.1, 0.0))
    controller = vo.rotation_ypr(-0.2, 0.3, 0.1, (0.1, 1.1, -0.2))
    direction = vo.laser_direction_in_controller(controller, vo._point(target))
    hit = vo.ray_hit_in_plane(plane, vo._point(controller), vo._rotate(controller, direction))
    assert hit == pytest.approx((0.3, -0.1))
    behind = vo.ray_hit_in_plane(plane, vo._point(controller),
                                 tuple(-v for v in vo._rotate(controller, direction)))
    assert behind is None


def test_edge_resize_follows_the_controller_not_the_cursor(grabbable):
    window, fake = grabbable
    w0, h0 = window.width(), window.height()
    bottom = QPoint(w0 // 2, h0 - 5)
    world = window._world_pose(fake.poses)
    lx, ly = vo.canvas_to_local(bottom.x(), bottom.y(), (w0, h0), window.meters_per_px)
    hit = vo._point(vo.mat_mul(world, vo.translation(lx, ly, 0.0)))
    # put the controller somewhere sensible, aiming at the bottom edge
    fake.poses[3] = vo.translation(hit[0] + 0.1, hit[1] - 0.2, hit[2] + 0.8)
    window._on_laser_move(bottom)
    window._on_laser_down(bottom, device=3)
    assert window._grab["kind"] == "resize"

    # cursor events now report nonsense (stale SteamVR geometry) - they must be ignored
    fake.events.append(("move", 5.0, 5.0, 3))
    # the controller moves down 10 cm: the bottom edge follows, the top edge stays
    fake.poses[3] = vo.translation(hit[0] + 0.1, hit[1] - 0.3, hit[2] + 0.8)
    window._tick()
    window._tick()                                # stable: no rubber band between ticks
    grown = window.height() - h0
    assert grown == pytest.approx(0.1 / window.meters_per_px, abs=3)
    window._tick()
    assert window.height() - h0 == grown


def test_roll_snap_highlights_top_and_bottom_edges_in_theme_blue(window):
    base = window._grab_frame()
    img = window._compose_highlight(base, (0.0, 1.0))
    dpr = img.devicePixelRatio()
    mid_x = round(window.width() / 2 * dpr)
    mid_y = round(window.height() / 2 * dpr)
    top = img.pixelColor(mid_x, 1)
    assert (top.red(), top.green(), top.blue()) == vo.SNAP_HIGHLIGHT_COLOR
    assert img.pixelColor(1, mid_y) == base.pixelColor(1, mid_y)   # sides untouched
