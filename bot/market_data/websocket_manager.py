"""WebSocket connection manager with reconnect, heartbeat, and health tracking.

Public market-data sockets only — never sends private API credentials.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from bot.market_data.models import ConnectionState

logger = logging.getLogger(__name__)

MessageHandler = Callable[[str | bytes], Awaitable[None]]


class WebSocketTransport(Protocol):
    """Minimal async WebSocket protocol for production or mocks."""

    async def send(self, data: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...

    @property
    def closed(self) -> bool: ...


class WebSocketManager:
    """Manages a single public WebSocket feed with exponential backoff."""

    def __init__(
        self,
        *,
        name: str,
        url: str,
        reconnect_base_ms: float = 500.0,
        reconnect_max_ms: float = 30_000.0,
        heartbeat_interval_ms: float = 15_000.0,
        connection_timeout_ms: float = 10_000.0,
        connect_factory: Callable[[str], Awaitable[WebSocketTransport]] | None = None,
        heartbeat_payload: str | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self._reconnect_base_ms = reconnect_base_ms
        self._reconnect_max_ms = reconnect_max_ms
        self._heartbeat_interval_ms = heartbeat_interval_ms
        self._connection_timeout_ms = connection_timeout_ms
        self._connect_factory = connect_factory
        self._heartbeat_payload = heartbeat_payload

        self._ws: WebSocketTransport | None = None
        self._state = ConnectionState.DISCONNECTED
        self._running = False
        self._reconnect_count = 0
        self._last_message_at: datetime | None = None
        self._message_count = 0
        self._window_start = time.monotonic()
        self._seen_hashes: set[str] = set()
        self._seen_order: list[str] = []
        self._max_seen = 512
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._on_message: MessageHandler | None = None
        self._on_connected: Callable[[], Awaitable[None]] | None = None
        self._subscriptions: list[str] = []

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state == ConnectionState.CONNECTED and self._ws is not None and not self._ws.closed

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    @property
    def last_message_at(self) -> datetime | None:
        return self._last_message_at

    @property
    def last_message_age_ms(self) -> float | None:
        if self._last_message_at is None:
            return None
        return max(0.0, (datetime.now(UTC) - self._last_message_at).total_seconds() * 1000.0)

    @property
    def message_rate_per_sec(self) -> float:
        elapsed = max(time.monotonic() - self._window_start, 1e-6)
        return self._message_count / elapsed

    def set_subscriptions(self, messages: list[str]) -> None:
        self._subscriptions = list(messages)

    async def start(
        self,
        on_message: MessageHandler,
        *,
        on_connected: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._on_message = on_message
        self._on_connected = on_connected
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name=f"ws-{self.name}")

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._close_socket()
        self._state = ConnectionState.DISCONNECTED
        logger.info("WEBSOCKET_DISCONNECTED exchange=%s reason=shutdown", self.name)

    async def send(self, data: str) -> None:
        if self._ws is None or self._ws.closed:
            raise RuntimeError(f"{self.name} websocket is not connected")
        await self._ws.send(data)

    def note_message(self, raw: str | bytes) -> bool:
        """Track message health / duplicates. Returns False if duplicate.

        Only exact-dedupe small control/ack frames. Large depth payloads are never
        dropped here — partial hashes collide when only mid-book levels change,
        and LocalOrderBook already handles sequence / snapshot identity.
        """
        size = len(raw)
        if size <= 256:
            digest = hash(raw if isinstance(raw, (bytes, str)) else bytes(raw))
            if digest in self._seen_hashes:
                logger.debug("DUPLICATE_MESSAGE exchange=%s", self.name)
                return False
            self._seen_hashes.add(digest)
            self._seen_order.append(digest)
            if len(self._seen_order) > self._max_seen:
                old = self._seen_order.pop(0)
                self._seen_hashes.discard(old)
        now = datetime.now(UTC)
        if self._last_message_at is None or (now - self._last_message_at).total_seconds() > 5:
            self._message_count = 0
            self._window_start = time.monotonic()
        self._message_count += 1
        self._last_message_at = now
        return True

    def is_stale(self, max_age_ms: float) -> bool:
        age = self.last_message_age_ms
        if age is None:
            return True
        if not self.connected:
            return True
        return age > max_age_ms

    async def _run_loop(self) -> None:
        delay_ms = self._reconnect_base_ms
        while self._running:
            try:
                await self._connect_once()
                delay_ms = self._reconnect_base_ms
                await self._read_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.info(
                    "WEBSOCKET_DISCONNECTED exchange=%s reason=%s",
                    self.name,
                    type(exc).__name__,
                )
                self._state = ConnectionState.RECONNECTING
                self._reconnect_count += 1
                logger.info(
                    "WEBSOCKET_RECONNECT exchange=%s attempt=%s delay_ms=%s",
                    self.name,
                    self._reconnect_count,
                    delay_ms,
                )
                await self._close_socket()
                if not self._running:
                    break
                await asyncio.sleep(delay_ms / 1000.0)
                delay_ms = min(delay_ms * 2, self._reconnect_max_ms)

    async def _connect_once(self) -> None:
        self._state = ConnectionState.CONNECTING
        factory = self._connect_factory or _default_connect
        self._ws = await asyncio.wait_for(
            factory(self.url),
            timeout=self._connection_timeout_ms / 1000.0,
        )
        self._state = ConnectionState.CONNECTED
        logger.info("WEBSOCKET_CONNECTED exchange=%s url=%s", self.name, self.url)
        for msg in self._subscriptions:
            await self._ws.send(msg)
        if self._on_connected:
            await self._on_connected()
        if self._heartbeat_payload is not None and self._heartbeat_interval_ms > 0:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _read_loop(self) -> None:
        assert self._ws is not None
        assert self._on_message is not None
        while self._running and self._ws is not None and not self._ws.closed:
            raw = await asyncio.wait_for(
                self._ws.recv(),
                timeout=max(self._connection_timeout_ms, self._heartbeat_interval_ms * 2)
                / 1000.0,
            )
            if not self.note_message(raw):
                continue
            await self._on_message(raw)

    async def _heartbeat_loop(self) -> None:
        try:
            while self._running and self.connected and self._heartbeat_payload:
                await asyncio.sleep(self._heartbeat_interval_ms / 1000.0)
                if self._ws and not self._ws.closed:
                    await self._ws.send(self._heartbeat_payload)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.info("WEBSOCKET_HEARTBEAT_FAILED exchange=%s error=%s", self.name, exc)

    async def _close_socket(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None


async def _default_connect(url: str) -> WebSocketTransport:
    import websockets

    ws = await websockets.connect(url, max_size=8 * 1024 * 1024)

    class _Wrapper:
        def __init__(self, socket: Any) -> None:
            self._socket = socket

        async def send(self, data: str) -> None:
            await self._socket.send(data)

        async def recv(self) -> str | bytes:
            return await self._socket.recv()

        async def close(self) -> None:
            await self._socket.close()

        @property
        def closed(self) -> bool:
            return bool(getattr(self._socket, "closed", False))

    return _Wrapper(ws)
