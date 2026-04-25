"""
IPC Server module - bridges yakutan clients and the local OSC manager.

Receives YakutanMessages from connected yakutan peers and forwards them
via osc_manager.add_external_message().  Broadcasts ForeignSpeech events
to all connected clients so that yakutan can display foreign-language
subtitles detected by the realtime-subtitle pipeline.
"""
import asyncio
import logging
import os
from typing import Optional, List

from shared.vrchat_bridge import (
    MessageType,
    YakutanMessage,
    ForeignSpeech,
    Heartbeat,
    OscState,
    serialize_message,
    deserialize_message,
    start_server_with_discovery,
    get_discovery_path,
)
from osc_manager import osc_manager

logger = logging.getLogger(__name__)

DEFAULT_PORT_RANGE = range(17353, 17364)


class IPCServer:
    """
    Asyncio-based TCP IPC server for yakutan ↔ realtime-subtitle bridging.

    - Listens on a dynamic port in the configured range.
    - Writes a discovery JSON file on startup and removes it on stop.
    - Maintains a list of active StreamWriter clients.
    """

    def __init__(self):
        self._server: Optional[asyncio.Server] = None
        self._port: Optional[int] = None
        self._host: Optional[str] = None
        self._clients: List[asyncio.StreamWriter] = []
        self._discovery_file: Optional[str] = None
        self._running = False
        self._lock = asyncio.Lock()
        self._soniox_session = None

    def set_soniox_session(self, session):
        self._soniox_session = session

    async def start(
        self,
        host: Optional[str] = None,
        port_range: Optional[range] = None,
    ) -> None:
        """Start the IPC server and publish the discovery file."""
        import config
        if self._running:
            logger.info("[IPC] Server already running on port %s", self._port)
            return

        self._host = host or config.IPC_HOST
        self._discovery_file = config.IPC_DISCOVERY_FILE
        actual_port_range = port_range if port_range is not None else config.IPC_PORT_RANGE

        self._server, self._port = await start_server_with_discovery(
            host=self._host,
            port_range=actual_port_range,
            discovery_file=self._discovery_file,
            client_handler=self._handle_client,
        )
        self._running = True
        logger.info(
            "IPC server started on port %d for yakutan delegation (discovery=%s)",
            self._port,
            self._discovery_file,
        )

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle a single client connection."""
        addr = writer.get_extra_info("peername")
        logger.info("[IPC] Client connected: %s", addr)

        async with self._lock:
            self._clients.append(writer)

        if self._soniox_session is not None:
            enabled = self._soniox_session.get_osc_translation_enabled()
            await self.send_osc_state_to_client(writer, enabled)

        try:
            while self._running:
                line = await reader.readline()
                if not line:
                    logger.info("[IPC] Client disconnected: %s", addr)
                    break

                message = deserialize_message(line.decode("utf-8"))
                if message is None:
                    logger.warning("[IPC] Invalid message from %s: %r", addr, line)
                    continue

                await self._dispatch_message(message, writer)
        except asyncio.CancelledError:
            logger.info("[IPC] Client handler cancelled for %s", addr)
            raise
        except Exception as exc:
            logger.error("[IPC] Error handling client %s: %s", addr, exc)
        finally:
            async with self._lock:
                if writer in self._clients:
                    self._clients.remove(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("[IPC] Client connection closed: %s", addr)

    async def _dispatch_message(
        self, message: dict, writer: asyncio.StreamWriter
    ) -> None:
        """Route an incoming message to the appropriate handler."""
        msg_type = message.get("type")
        addr = writer.get_extra_info("peername")

        # 打印接收到的消息到命令行
        # print(f"[IPC] Received message from {addr}: {message}")

        if msg_type == MessageType.YAKUTAN_MESSAGE.value:
            text = message.get("text", "")
            ongoing = bool(message.get("ongoing", False))
            logger.debug(
                "[IPC] Received YAKUTAN_MESSAGE from %s: text=%r ongoing=%s",
                addr,
                text,
                ongoing,
            )
            if self._soniox_session is not None:
                self._soniox_session.update_ipc_message(text, ongoing)
            
            if ongoing:
                osc_manager.send_preview_message_with_history(text, ongoing=True, speaker="0")
            else:
                osc_manager.add_message_and_send(text, ongoing=False, speaker="0")

        elif msg_type == MessageType.HEARTBEAT.value:
            logger.debug("[IPC] Received HEARTBEAT from %s", addr)
            try:
                heartbeat = Heartbeat()
                writer.write(serialize_message(heartbeat).encode("utf-8"))
                await writer.drain()
            except Exception as exc:
                logger.warning("[IPC] Failed to send heartbeat reply: %s", exc)

        else:
            logger.warning(
                "[IPC] Unknown message type from %s: %s", addr, msg_type
            )

    async def send_osc_state_to_client(self, writer, enabled):
        try:
            msg = OscState(enabled=enabled)
            writer.write(serialize_message(msg).encode("utf-8"))
            await writer.drain()
        except Exception as exc:
            logger.warning("[IPC] Failed to send OSC state to client: %s", exc)

    async def broadcast_osc_state(self, enabled):
        if not self._clients:
            return
        payload = serialize_message(OscState(enabled=enabled)).encode("utf-8")
        disconnected = []
        for writer in list(self._clients):
            try:
                writer.write(payload)
                await writer.drain()
            except Exception as exc:
                logger.warning("[IPC] Failed to broadcast OSC state: %s", exc)
                disconnected.append(writer)
        if disconnected:
            async with self._lock:
                for writer in disconnected:
                    if writer in self._clients:
                        self._clients.remove(writer)
                for writer in disconnected:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

    async def broadcast_foreign_speech(
        self, source_text: str, detected_language: Optional[str] = None
    ) -> None:
        """Broadcast a ForeignSpeech message to all connected clients."""
        if not self._clients:
            return

        message = ForeignSpeech(
            source_text=source_text,
            detected_language=detected_language,
        )
        payload = serialize_message(message).encode("utf-8")

        disconnected: List[asyncio.StreamWriter] = []
        for writer in list(self._clients):
            try:
                writer.write(payload)
                await writer.drain()
            except Exception as exc:
                logger.warning("[IPC] Failed to broadcast to client: %s", exc)
                disconnected.append(writer)

        if disconnected:
            async with self._lock:
                for writer in disconnected:
                    if writer in self._clients:
                        self._clients.remove(writer)
                for writer in disconnected:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

    async def stop(self) -> None:
        """Stop the server and clean up the discovery file."""
        if not self._running:
            return

        self._running = False
        logger.info("[IPC] Stopping server...")

        async with self._lock:
            clients = list(self._clients)
            self._clients.clear()

        for writer in clients:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        discovery = self._discovery_file
        if discovery and os.path.exists(discovery):
            try:
                with open(discovery, "r") as f:
                    data = f.read()
                    import json

                    info = json.loads(data)
                    if info.get("pid") == os.getpid():
                        os.remove(discovery)
                        logger.info("[IPC] Discovery file removed: %s", discovery)
            except Exception as exc:
                logger.warning("[IPC] Failed to remove discovery file: %s", exc)

        self._port = None
        self._host = None
        self._discovery_file = None
        logger.info("[IPC] Server stopped")
