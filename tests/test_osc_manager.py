import os
import sys
import tempfile
import time
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock config module with all attributes needed by both projects
mock_config = type(sys)("config")
mock_config.IPC_ENABLED = True
mock_config.IPC_HOST = "127.0.0.1"
mock_config.IPC_PORT_RANGE = range(21000, 21010)
mock_config.IPC_DISCOVERY_FILE = os.path.join(tempfile.gettempdir(), "test_rt_discovery.json")
mock_config.IPC_DISCOVERY_TIMEOUT = 30.0
mock_config.IPC_CONNECT_TIMEOUT = 2.0
mock_config.IPC_RECONNECT_MAX_DELAY = 30.0
mock_config.IPC_POLL_INTERVAL = 3.0
mock_config.OSC_COMPAT_MODE = False
mock_config.OSC_TEXT_MAX_LENGTH = 144
mock_config.OSC_SEND_TARGET_PORT = 9000
mock_config.OSC_CLIENT_IP = "127.0.0.1"
mock_config.OSC_SERVER_IP = "127.0.0.1"
mock_config.OSC_COMPAT_LISTEN_PORT = 9001
sys.modules["config"] = mock_config

# Mock vrchat_oscquery package and submodules before importing osc_manager
mock_vrchat_common = type(sys)("vrchat_oscquery.common")
mock_vrchat_common.dict_to_dispatcher = lambda d: d
mock_vrchat_common.vrc_client = MagicMock(return_value=MagicMock())
mock_vrchat_common.APP_HOST = "127.0.0.1"
sys.modules["vrchat_oscquery.common"] = mock_vrchat_common

mock_vrchat_threaded = type(sys)("vrchat_oscquery.threaded")
mock_vrchat_threaded.vrc_osc = MagicMock()
sys.modules["vrchat_oscquery.threaded"] = mock_vrchat_threaded

sys.modules["vrchat_oscquery"] = type(sys)("vrchat_oscquery")

from osc_manager import OSCManager, QueuedMessage


def _fresh_osc_manager():
    OSCManager._instance = None
    osc = OSCManager()
    osc.clear_history()
    osc._last_send_time = 0.0
    osc._pending_message = None
    osc._pending_timer = None
    return osc


def test_add_external_message_sends_message():
    osc = _fresh_osc_manager()
    mock_client = MagicMock()
    osc._client = mock_client
    osc._udp_send_target = ("127.0.0.1", 9000)
    osc._compat_mode_enabled = lambda: False

    osc.add_external_message("Hello from yakutan", ongoing=False)

    assert mock_client.send_message.call_count >= 2
    addresses = [call[0][0] for call in mock_client.send_message.call_args_list]
    assert "/chatbox/typing" in addresses
    assert "/chatbox/input" in addresses


def test_add_external_message_with_ongoing():
    osc = _fresh_osc_manager()
    mock_client = MagicMock()
    osc._client = mock_client
    osc._udp_send_target = ("127.0.0.1", 9000)
    osc._compat_mode_enabled = lambda: False

    osc.add_external_message("Typing...", ongoing=True)

    input_calls = [call for call in mock_client.send_message.call_args_list if call[0][0] == "/chatbox/input"]
    assert len(input_calls) == 1
    args = input_calls[0][0][1]
    assert args[1] is True
    assert args[2] is False


def _last_chatbox_input_text(mock_client):
    for call in reversed(mock_client.send_message.call_args_list):
        if call[0][0] == "/chatbox/input":
            return call[0][1][0]
    return None


def test_send_preview_messages_renders_multiline_without_recording():
    osc = _fresh_osc_manager()
    mock_client = MagicMock()
    osc._client = mock_client

    osc.add_message_and_send("已确认句。", ongoing=False, speaker="1")
    assert len(osc._message_history) == 1

    osc._last_send_time = 0.0  # dodge the send cooldown so the preview goes out now
    osc.send_preview_messages_with_history(["在途甲。", "在途乙"], ongoing=True, speaker="1")

    text = _last_chatbox_input_text(mock_client)
    assert text is not None
    # Confirmed history line + the two in-progress preview lines, each on its own line.
    assert "已确认句。" in text
    assert "在途甲。" in text
    assert "在途乙" in text
    assert "\n" in text
    # The preview must NOT be recorded into the confirmed history.
    assert len(osc._message_history) == 1
    assert all("在途" not in msg.text for msg in osc._message_history)


def test_send_preview_messages_ignores_empty():
    osc = _fresh_osc_manager()
    mock_client = MagicMock()
    osc._client = mock_client

    osc.send_preview_messages_with_history(["", "   ", None], ongoing=True, speaker="1")

    assert mock_client.send_message.call_count == 0
    assert len(osc._message_history) == 0


def test_add_external_message_has_no_speaker_prefix():
    osc = _fresh_osc_manager()
    mock_client = MagicMock()
    osc._client = mock_client
    osc._udp_send_target = ("127.0.0.1", 9000)
    osc._compat_mode_enabled = lambda: False

    osc.add_external_message("Hello", ongoing=False)

    input_calls = [call for call in mock_client.send_message.call_args_list if call[0][0] == "/chatbox/input"]
    assert len(input_calls) == 1
    text = input_calls[0][0][1][0]
    assert text == "Hello"
    assert "S" not in text or "：" not in text


def test_add_external_message_empty_ignored():
    osc = _fresh_osc_manager()
    mock_client = MagicMock()
    osc._client = mock_client
    osc._udp_send_target = ("127.0.0.1", 9000)
    osc._compat_mode_enabled = lambda: False

    osc.add_external_message("", ongoing=False)
    osc.add_external_message("   ", ongoing=False)

    assert mock_client.send_message.call_count == 0


def test_add_message_and_send_with_speaker_label():
    # Soniox path: a diarized speaker index renders an S{speaker}： prefix.
    osc = _fresh_osc_manager()
    mock_client = MagicMock()
    osc._client = mock_client
    osc._udp_send_target = ("127.0.0.1", 9000)
    osc._compat_mode_enabled = lambda: False

    osc.add_message_and_send("My message", ongoing=False, speaker="1")

    input_calls = [call for call in mock_client.send_message.call_args_list if call[0][0] == "/chatbox/input"]
    assert len(input_calls) == 1
    text = input_calls[0][0][1][0]
    assert text == "S1：My message"


def test_add_message_and_send_without_speaker_is_plain():
    # Gemini path: no speaker provided renders the raw text (no prefix).
    osc = _fresh_osc_manager()
    mock_client = MagicMock()
    osc._client = mock_client
    osc._udp_send_target = ("127.0.0.1", 9000)
    osc._compat_mode_enabled = lambda: False

    osc.add_message_and_send("My message", ongoing=False)

    input_calls = [call for call in mock_client.send_message.call_args_list if call[0][0] == "/chatbox/input"]
    assert len(input_calls) == 1
    text = input_calls[0][0][1][0]
    assert text == "My message"


def test_speaker_labels_disabled_hides_prefix():
    # When labels are disabled (Gemini), even an explicit speaker is not prefixed.
    osc = _fresh_osc_manager()
    mock_client = MagicMock()
    osc._client = mock_client
    osc._udp_send_target = ("127.0.0.1", 9000)
    osc._compat_mode_enabled = lambda: False
    osc.set_speaker_labels_enabled(False)

    osc.add_message_and_send("My message", ongoing=False, speaker="1")

    input_calls = [call for call in mock_client.send_message.call_args_list if call[0][0] == "/chatbox/input"]
    assert len(input_calls) == 1
    text = input_calls[0][0][1][0]
    assert text == "My message"


def test_own_message_wrapped_in_brackets():
    osc = _fresh_osc_manager()
    mock_client = MagicMock()
    osc._client = mock_client
    osc._udp_send_target = ("127.0.0.1", 9000)
    osc._compat_mode_enabled = lambda: False

    osc.add_message_and_send("My speech", ongoing=False, own=True)

    input_calls = [call for call in mock_client.send_message.call_args_list if call[0][0] == "/chatbox/input"]
    assert len(input_calls) == 1
    text = input_calls[0][0][1][0]
    assert text == ">My speech<"


def test_truncate_text():
    osc = _fresh_osc_manager()
    long_text = "A" * 200
    result = osc._truncate_text(long_text, max_length=144)
    assert len(result) <= 144


def test_build_combined_history_respects_max_lines():
    osc = _fresh_osc_manager()
    now = time.time()
    for i in range(15):
        osc._message_history.append(type("Msg", (), {"text": f"msg{i}", "timestamp": now, "speaker": "1"})())
    combined = osc._build_combined_history_locked()
    lines = combined.split("\n")
    assert len(lines) <= 9
