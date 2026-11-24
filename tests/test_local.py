import asyncio

import pytest

from pyscinan import (
    DeviceMode,
    ScinanConnectionError,
    ScinanLocalClient,
    ScinanTimeoutError,
)

from .fake_thermostat import DEVICE_ID, FakeThermostat


def make_client(fake: FakeThermostat, **kw) -> ScinanLocalClient:
    kw.setdefault("connect_timeout", 1.0)
    kw.setdefault("command_timeout", 0.5)
    return ScinanLocalClient("127.0.0.1", port=fake.port, **kw)


async def wait_until(predicate, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_connect_learns_id_type_and_status(fake):
    async with make_client(fake) as client:
        assert client.connected
        dev = client.device
        assert dev.device_id == DEVICE_ID
        assert dev.type == 9
        assert dev.online
        assert dev.target_temperature == 22.0
        assert f"/{DEVICE_ID}/S00/1/-1" in fake.received


async def test_hello_without_newline(fake):
    fake.hello_newline = False
    async with make_client(fake) as client:
        assert client.device_id == DEVICE_ID


async def test_no_hello_needs_device_id(fake):
    fake.send_hello = False
    with pytest.raises(ScinanTimeoutError):
        await make_client(fake, connect_timeout=0.3).connect()
    async with make_client(fake, device_id=DEVICE_ID) as client:
        assert client.device.target_temperature == 22.0


async def test_connect_refused():
    client = ScinanLocalClient("127.0.0.1", port=1, connect_timeout=0.5)
    with pytest.raises(ScinanConnectionError):
        await client.connect()


async def test_commands(fake):
    async with make_client(fake) as client:
        st = await client.set_temperature(21.3)
        assert st.target_temperature == 21.5
        assert f"/{DEVICE_ID}/S02/1/21.5" in fake.received
        st = await client.set_away(True)
        assert st.away is True
        assert st.target_temperature == 5.0  # frost protection shown while away
        st = await client.set_temperature(21)
        assert st.away is False  # setting a temperature cancels away
        st = await client.set_mode(DeviceMode.AUTO)
        assert st.system_mode == 1
        assert st.program is True
        assert st.target_temperature == 16.0  # schedule target
        assert (await client.set_mode(3)).system_mode == 1  # invalid mode ignored
        assert (await client.set_mode(DeviceMode.COMFORT)).target_temperature == 21


async def test_ignored_commands_fall_back_to_status(fake):
    """Type 9 ignores S01/S07 without replying; the S00 fallback returns unchanged state."""
    async with make_client(fake) as client:
        st = await client.set_power(False)
        assert st.power is True
        assert fake.received[-2:] == [f"/{DEVICE_ID}/S01/1/0", f"/{DEVICE_ID}/S00/1/-1"]
        assert (await client.set_program(True)).program is False


async def test_command_falls_back_to_status_request(fake):
    async with make_client(fake) as client:
        fake.answer_commands = False
        with pytest.raises(ScinanTimeoutError):
            await client.set_temperature(20)
        # fallback S00 was sent after the S02
        assert fake.received[-1] == f"/{DEVICE_ID}/S00/1/-1"


async def test_push_updates_notify_listener(fake):
    async with make_client(fake) as client:
        calls = []
        client.add_listener(lambda: calls.append(client.device.current_temperature))
        fake.state.current = 19.5
        await fake.push_status()
        await wait_until(lambda: calls)
        assert calls[-1] == 19.5


async def test_heartbeat(fake):
    async with make_client(fake, heartbeat_interval=0.1):
        await wait_until(lambda: f"/{DEVICE_ID}/SPOLL/1/1" in fake.received)


async def test_reconnect_after_drop(fake, monkeypatch):
    monkeypatch.setattr("pyscinan.local._RECONNECT_BACKOFF", (0.05,))
    async with make_client(fake) as client:
        states = []
        client.add_listener(lambda: states.append(client.connected))
        await fake.drop_clients()
        await wait_until(lambda: False in states)
        await wait_until(lambda: client.connected and states[-1] is True)
        assert client.device.online
        assert (await client.set_temperature(23)).target_temperature == 23


async def test_idle_timeout_triggers_reconnect(fake, monkeypatch):
    monkeypatch.setattr("pyscinan.local._RECONNECT_BACKOFF", (0.05,))
    fake.answer_commands = True
    async with make_client(fake, idle_timeout=0.3, heartbeat_interval=60) as client:
        connects_before = sum(1 for r in fake.received if r.endswith("/S00/1/-1"))
        await wait_until(
            lambda: sum(1 for r in fake.received if r.endswith("/S00/1/-1")) > connects_before
        )
        await wait_until(lambda: client.connected)


async def test_close_stops_reconnect(fake):
    client = make_client(fake)
    await client.connect()
    await client.close()
    assert not client.connected
    with pytest.raises(ScinanConnectionError):
        await client.set_temperature(20)


async def test_connect_asks_again_when_first_status_is_missing(fake):
    """Seen on hardware: hello, then no status for 5 s. A second request works."""
    fake.ignore_status_requests = 1
    async with make_client(fake) as client:
        assert client.device.target_temperature == 22.0
    assert fake.received.count(f"/{DEVICE_ID}/S00/1/-1") == 2
    assert fake.connections == 1  # same connection, no reconnect


async def test_connect_gives_up_after_two_missing_statuses(fake):
    fake.ignore_status_requests = 2
    client = make_client(fake, reconnect=False)
    with pytest.raises(ScinanTimeoutError):
        await client.connect()
    assert not client.connected


async def test_command_while_disconnected_leaves_no_waiter(fake):
    """A failed send must not leave a status waiter that errors later on close."""
    client = make_client(fake, reconnect=False)
    await client.connect()
    await fake.drop_clients()
    await wait_until(lambda: not client.connected)
    with pytest.raises(ScinanConnectionError):
        await client.set_temperature(21)
    assert client._status_waiters == []
    await client.close()

