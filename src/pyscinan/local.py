"""Async client for the local TCP protocol (port 2000).

One persistent connection per thermostat. The device pushes S00 status
lines on its own and answers every command with a fresh S00, so this client
is push based: register a listener and read ``client.device``.

    client = ScinanLocalClient("192.168.1.50")
    await client.connect()           # raises ScinanConnectionError on failure
    client.add_listener(lambda: print(client.device.target_temperature))
    await client.set_temperature(21.5)
    ...
    await client.close()

After the first successful connect the client keeps the connection alive
(SPOLL heartbeat) and reconnects with backoff if it drops.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable

from .const import (
    LOCAL_HEARTBEAT_INTERVAL,
    LOCAL_IDLE_TIMEOUT,
    LOCAL_TCP_PORT,
    Code,
    DeviceMode,
)
from .exceptions import (
    ScinanConnectionError,
    ScinanProtocolError,
    ScinanTimeoutError,
)
from .models import ScinanDevice, ScinanStatus
from .protocol import Message, build_line, format_bool, format_temperature, parse_line

_LOGGER = logging.getLogger(__name__)

_LINE_SPLIT_RE = re.compile(rb"[\r\n]+")
_FLUSH_PARTIAL_AFTER = 0.5  # treat an unterminated line as complete after this long
_RECONNECT_BACKOFF = (1, 2, 5, 10, 30, 60)

Listener = Callable[[], None]
MessageListener = Callable[[Message], None]


class ScinanLocalClient:
    """Persistent local connection to one thermostat."""

    def __init__(
        self,
        host: str,
        *,
        device_id: str | None = None,
        port: int = LOCAL_TCP_PORT,
        connect_timeout: float = 5.0,
        command_timeout: float = 5.0,
        heartbeat_interval: float = LOCAL_HEARTBEAT_INTERVAL,
        idle_timeout: float = LOCAL_IDLE_TIMEOUT,
        reconnect: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self._device_id = device_id
        self._connect_timeout = connect_timeout
        self._command_timeout = command_timeout
        self._heartbeat_interval = heartbeat_interval
        self._idle_timeout = idle_timeout
        self._reconnect = reconnect

        self.device: ScinanDevice | None = (
            ScinanDevice(device_id=device_id) if device_id else None
        )
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._write_lock = asyncio.Lock()
        self._connected = False
        self._closing = False
        self._session_tasks: list[asyncio.Task] = []
        self._supervisor: asyncio.Task | None = None
        self._hello: asyncio.Future[str] | None = None
        self._status_waiters: list[asyncio.Future[ScinanStatus]] = []
        self._listeners: list[Listener] = []
        self._message_listeners: list[MessageListener] = []

    # ------------------------------------------------------------------ API

    @property
    def device_id(self) -> str | None:
        return self._device_id

    @property
    def connected(self) -> bool:
        return self._connected

    async def __aenter__(self) -> ScinanLocalClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def connect(self) -> ScinanDevice:
        """Connect, learn the device id/type, fetch status, start keep-alive."""
        if self._supervisor is not None:
            raise RuntimeError("Already connected")
        self._closing = False
        await self._open_session()
        self._supervisor = asyncio.create_task(self._supervise(), name=f"scinan-{self.host}")
        assert self.device is not None
        return self.device

    async def close(self) -> None:
        """Close the connection and stop reconnecting."""
        self._closing = True
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
            self._supervisor = None
        await self._close_session()

    def add_listener(self, listener: Listener) -> Callable[[], None]:
        """Call `listener()` on every status or connection change. Returns remover."""
        self._listeners.append(listener)
        return lambda: _discard(self._listeners, listener)

    def add_message_listener(self, listener: MessageListener) -> Callable[[], None]:
        """Call `listener(message)` for every received line. Returns remover."""
        self._message_listeners.append(listener)
        return lambda: _discard(self._message_listeners, listener)

    async def request_status(self) -> ScinanStatus:
        """Ask for a full status (S00)."""
        status = await self.send_command(Code.STATUS, "-1")
        assert status is not None
        return status

    async def set_temperature(self, temperature: float) -> ScinanStatus:
        """Set target temperature (rounded to 0.5, clamped to device limits)."""
        dev = self.device
        data = format_temperature(
            temperature,
            dev.min_temp if dev else 5.0,
            dev.max_temp if dev else 35.0,
        )
        return await self._command(Code.TARGET_TEMPERATURE, data)

    async def set_power(self, on: bool) -> ScinanStatus:
        return await self._command(Code.POWER, format_bool(on))

    async def set_away(self, away: bool) -> ScinanStatus:
        return await self._command(Code.AWAY, format_bool(away))

    async def set_program(self, enabled: bool) -> ScinanStatus:
        return await self._command(Code.PROGRAM, format_bool(enabled))

    async def set_mode(self, mode: DeviceMode | int) -> ScinanStatus:
        return await self._command(Code.MODE, str(int(mode)))

    async def send_command(
        self, code: Code | str, data: str, *, wait_for_status: bool = True
    ) -> ScinanStatus | None:
        """Send any command. By default wait for the S00 the device answers with.

        If no S00 arrives within the timeout, an explicit S00 request is sent
        as a fallback before giving up.
        """
        if not wait_for_status:
            await self._send(code, data)
            return None
        waiter = self._new_status_waiter()
        await self._send(code, data)
        try:
            return await self._wait(waiter)
        except ScinanTimeoutError:
            if str(code) == Code.STATUS:
                raise
        _LOGGER.debug("%s: no status after %s, requesting S00", self.host, code)
        waiter = self._new_status_waiter()
        await self._send(Code.STATUS, "-1")
        return await self._wait(waiter)

    async def send_poll(self) -> None:
        await self._send(Code.POLL, "1")

    # ------------------------------------------------------------ internals

    async def _command(self, code: Code, data: str) -> ScinanStatus:
        status = await self.send_command(code, data)
        assert status is not None
        return status

    async def _send(self, code: Code | str, data: str) -> None:
        if not self._connected or self._writer is None:
            raise ScinanConnectionError(f"Not connected to {self.host}")
        if not self._device_id:
            raise ScinanConnectionError(f"Device id for {self.host} not known yet")
        line = build_line(self._device_id, code, data)
        _LOGGER.debug("%s >> %s", self.host, line.decode().strip())
        async with self._write_lock:
            try:
                self._writer.write(line)
                await self._writer.drain()
            except (OSError, ConnectionError) as err:
                raise ScinanConnectionError(f"Write to {self.host} failed: {err}") from err

    def _new_status_waiter(self) -> asyncio.Future[ScinanStatus]:
        fut: asyncio.Future[ScinanStatus] = asyncio.get_running_loop().create_future()
        self._status_waiters.append(fut)
        return fut

    async def _wait(self, fut: asyncio.Future[ScinanStatus]) -> ScinanStatus:
        try:
            async with asyncio.timeout(self._command_timeout):
                return await fut
        except TimeoutError as err:
            raise ScinanTimeoutError(f"No status from {self.host}") from err
        finally:
            if fut in self._status_waiters:
                self._status_waiters.remove(fut)

    async def _open_session(self) -> None:
        """Open TCP, wait for hello (if id unknown), fetch first status."""
        try:
            async with asyncio.timeout(self._connect_timeout):
                reader, writer = await asyncio.open_connection(self.host, self.port)
        except (TimeoutError, OSError) as err:
            reason = str(err) or type(err).__name__  # TimeoutError has an empty str()
            raise ScinanConnectionError(
                f"Cannot connect to {self.host}:{self.port}: {reason}"
            ) from err
        _LOGGER.debug("%s: TCP connected", self.host)
        self._reader, self._writer = reader, writer
        self._connected = True
        self._hello = asyncio.get_running_loop().create_future()
        self._session_tasks = [asyncio.create_task(self._read_loop(reader))]
        try:
            if not self._device_id:
                try:
                    async with asyncio.timeout(self._connect_timeout):
                        await self._hello
                except TimeoutError as err:
                    raise ScinanTimeoutError(
                        f"{self.host} sent no hello; pass device_id explicitly"
                    ) from err
            await self.request_status()
        except BaseException:
            await self._close_session()
            raise
        self._session_tasks.append(asyncio.create_task(self._heartbeat_loop()))
        if self.device:
            self.device.online = True
        self._notify()

    async def _close_session(self) -> None:
        was_connected = self._connected
        self._connected = False
        tasks, self._session_tasks = self._session_tasks, []
        current = asyncio.current_task()
        for task in tasks:
            if task is not current:
                task.cancel()
        for task in tasks:
            if task is not current:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self._writer is not None:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()
        self._reader = self._writer = None
        for fut in self._status_waiters:
            if not fut.done():
                fut.set_exception(ScinanConnectionError(f"Connection to {self.host} closed"))
        self._status_waiters.clear()
        if self.device:
            self.device.online = False
        if was_connected:
            _LOGGER.debug("%s: disconnected", self.host)
            self._notify()

    async def _supervise(self) -> None:
        """Wait for the session to die, then reconnect with backoff."""
        attempt = 0
        while not self._closing:
            if self._connected and self._session_tasks:
                # Wait for the reader to end (EOF, idle timeout or error).
                with contextlib.suppress(Exception):
                    await self._session_tasks[0]
                await self._close_session()
                attempt = 0
                if not self._reconnect:
                    return
                _LOGGER.info("%s: connection lost, reconnecting", self.host)
            if attempt:
                await asyncio.sleep(self._backoff(attempt))
            attempt += 1
            try:
                await self._open_session()
                _LOGGER.info("%s: reconnected", self.host)
            except ScinanConnectionError as err:
                _LOGGER.debug(
                    "%s: reconnect failed (%s), retry in %ss",
                    self.host, err, self._backoff(attempt),
                )

    @staticmethod
    def _backoff(attempt: int) -> float:
        """Delay before reconnect attempt number `attempt + 1` (attempt >= 1)."""
        return _RECONNECT_BACKOFF[min(attempt - 1, len(_RECONNECT_BACKOFF) - 1)]

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                await self.send_poll()
            except ScinanConnectionError:
                return

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        buf = b""
        loop = asyncio.get_running_loop()
        last_data = loop.time()
        while True:
            timeout = _FLUSH_PARTIAL_AFTER if buf else self._idle_timeout
            try:
                async with asyncio.timeout(timeout):
                    chunk = await reader.read(4096)
            except TimeoutError:
                if buf:
                    # Unterminated line (some firmwares omit CRLF on the hello).
                    self._handle_raw(buf)
                    buf = b""
                    continue
                if loop.time() - last_data >= self._idle_timeout:
                    _LOGGER.debug("%s: idle for %ss", self.host, self._idle_timeout)
                    return
                continue
            if not chunk:
                if buf:
                    self._handle_raw(buf)
                _LOGGER.debug("%s: EOF", self.host)
                return
            last_data = loop.time()
            buf += chunk
            *lines, buf = _LINE_SPLIT_RE.split(buf)
            for raw in lines:
                if raw:
                    self._handle_raw(raw)

    def _handle_raw(self, raw: bytes) -> None:
        line = raw.decode(errors="replace").strip()
        if not line:
            return
        _LOGGER.debug("%s << %s", self.host, line)
        try:
            msg = parse_line(line)
        except ScinanProtocolError:
            _LOGGER.debug("%s: ignoring line %r", self.host, line)
            return
        self._handle_message(msg)

    def _handle_message(self, msg: Message) -> None:
        if self._device_id is None:
            self._device_id = msg.device_id
        elif msg.device_id != self._device_id:
            _LOGGER.warning(
                "%s: message for %s, expected %s", self.host, msg.device_id, self._device_id
            )
            return
        if self.device is None:
            self.device = ScinanDevice(device_id=msg.device_id)

        for listener in list(self._message_listeners):
            try:
                listener(msg)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Message listener failed")

        if msg.is_hello:
            with contextlib.suppress(ValueError):
                self.device.type = int(msg.data)
            if self._hello and not self._hello.done():
                self._hello.set_result(msg.data)
            return

        if msg.code == Code.STATUS:
            try:
                status = ScinanStatus.parse(msg.data)
            except ScinanProtocolError as err:
                _LOGGER.debug("%s: bad status: %s", self.host, err)
                return
            self.device.update_status(status)
            self.device.online = True
            waiters, self._status_waiters = self._status_waiters, []
            for fut in waiters:
                if not fut.done():
                    fut.set_result(status)
            self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Listener failed")


def _discard(items: list, item: object) -> None:
    """Remove item if present (listener removers may be called more than once)."""
    with contextlib.suppress(ValueError):
        items.remove(item)
