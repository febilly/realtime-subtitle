import asyncio
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock config module before importing ipc_server
mock_config = type(sys)("config")
mock_config.IPC_HOST = "127.0.0.1"
mock_config.IPC_DISCOVERY_FILE = os.path.join(tempfile.gettempdir(), "test_rt_discovery.json")
mock_config.IPC_PORT_RANGE = range(21000, 21010)
mock_config.OSC_COMPAT_MODE = False
mock_config.OSC_TEXT_MAX_LENGTH = 144
mock_config.OSC_SEND_TARGET_PORT = 9000
mock_config.OSC_CLIENT_IP = "127.0.0.1"
mock_config.OSC_SERVER_IP = "127.0.0.1"
mock_config.OSC_COMPAT_LISTEN_PORT = 9001
sys.modules["config"] = mock_config

# Mock vrchat_oscquery before importing osc_manager
mock_vrchat_common = type(sys)("vrchat_oscquery.common")
mock_vrchat_common.dict_to_dispatcher = lambda d: d
mock_vrchat_common.vrc_client = MagicMock(return_value=MagicMock())
mock_vrchat_common.APP_HOST = "127.0.0.1"
sys.modules["vrchat_oscquery.common"] = mock_vrchat_common

mock_vrchat_threaded = type(sys)("vrchat_oscquery.threaded")
mock_vrchat_threaded.vrc_osc = MagicMock()
sys.modules["vrchat_oscquery.threaded"] = mock_vrchat_threaded
sys.modules["vrchat_oscquery"] = type(sys)("vrchat_oscquery")

from ipc_server import IPCServer
from osc_manager import osc_manager as rt_osc
from shared.vrchat_bridge import (
    serialize_message,
    deserialize_message,
    YakutanMessage,
    ForeignSpeech,
)


@pytest.fixture(autouse=True)
def cleanup_discovery_file():
    yield
    path = mock_config.IPC_DISCOVERY_FILE
    if os.path.exists(path):
        os.remove(path)


class TestIPCServerStartup:
    @pytest.mark.asyncio
    async def test_server_starts_and_writes_discovery_file(self):
        server = IPCServer()
        try:
            await server.start()
            assert server._running is True
            assert server._port is not None
            assert server._port in mock_config.IPC_PORT_RANGE
            assert os.path.exists(mock_config.IPC_DISCOVERY_FILE)
            with open(mock_config.IPC_DISCOVERY_FILE, "r") as f:
                data = json.load(f)
            assert data["host"] == mock_config.IPC_HOST
            assert data["port"] == server._port
            assert "pid" in data
            assert "timestamp" in data
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_server_idempotent_start(self):
        server = IPCServer()
        try:
            await server.start()
            first_port = server._port
            await server.start()
            assert server._port == first_port
        finally:
            await server.stop()


class TestIPCServerDispatch:
    @pytest.mark.asyncio
    async def test_yakutan_message_dispatch(self):
        received = []
        original_add = rt_osc.add_external_message
        rt_osc.add_external_message = lambda text, ongoing: received.append((text, ongoing))

        server = IPCServer()
        try:
            await server.start()
            host = mock_config.IPC_HOST
            port = server._port
            reader, writer = await asyncio.open_connection(host, port)
            msg = YakutanMessage(text="hello", ongoing=False)
            writer.write(serialize_message(msg).encode("utf-8"))
            await writer.drain()
            await asyncio.sleep(0.1)
            writer.close()
            await writer.wait_closed()

            assert len(received) == 1
            assert received[0] == ("hello", False)
        finally:
            rt_osc.add_external_message = original_add
            await server.stop()

    @pytest.mark.asyncio
    async def test_heartbeat_echo(self):
        server = IPCServer()
        try:
            await server.start()
            host = mock_config.IPC_HOST
            port = server._port
            reader, writer = await asyncio.open_connection(host, port)
            msg = {"type": "HEARTBEAT"}
            writer.write((json.dumps(msg) + "\n").encode("utf-8"))
            await writer.drain()
            response = await asyncio.wait_for(reader.readline(), timeout=1.0)
            data = deserialize_message(response.decode("utf-8"))
            assert data is not None
            assert data["type"] == "HEARTBEAT"
            writer.close()
            await writer.wait_closed()
        finally:
            await server.stop()


class TestIPCServerBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_foreign_speech(self):
        server = IPCServer()
        try:
            await server.start()
            host = mock_config.IPC_HOST
            port = server._port

            # Connect two clients
            r1, w1 = await asyncio.open_connection(host, port)
            r2, w2 = await asyncio.open_connection(host, port)

            await server.broadcast_foreign_speech("bonjour", "fr")
            await asyncio.sleep(0.1)

            async def read_msg(reader):
                line = await asyncio.wait_for(reader.readline(), timeout=1.0)
                return deserialize_message(line.decode("utf-8"))

            data1 = await read_msg(r1)
            data2 = await read_msg(r2)

            assert data1["type"] == "FOREIGN_SPEECH"
            assert data1["source_text"] == "bonjour"
            assert data1["detected_language"] == "fr"
            assert data2["type"] == "FOREIGN_SPEECH"
            assert data2["source_text"] == "bonjour"

            w1.close()
            await w1.wait_closed()
            w2.close()
            await w2.wait_closed()
        finally:
            await server.stop()

    @pytest.mark.asyncio
    async def test_broadcast_with_no_clients(self):
        server = IPCServer()
        try:
            await server.start()
            await server.broadcast_foreign_speech("hello")
        finally:
            await server.stop()


class TestIPCServerStop:
    @pytest.mark.asyncio
    async def test_stop_removes_discovery_file(self):
        server = IPCServer()
        await server.start()
        assert os.path.exists(mock_config.IPC_DISCOVERY_FILE)
        await server.stop()
        assert not os.path.exists(mock_config.IPC_DISCOVERY_FILE)
        assert server._running is False

    @pytest.mark.asyncio
    async def test_stop_closes_client_connections(self):
        server = IPCServer()
        await server.start()
        host = mock_config.IPC_HOST
        port = server._port
        reader, writer = await asyncio.open_connection(host, port)
        await server.stop()
        await asyncio.sleep(0.1)
        assert server._running is False
        writer.close()
        await writer.wait_closed()
