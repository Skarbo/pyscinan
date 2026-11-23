import json
from datetime import UTC, datetime

import pytest

from pyscinan import cloud as cloud_mod
from pyscinan.cloud import ScinanCloudClient
from pyscinan.const import Code, DeviceMode
from pyscinan.exceptions import (
    ScinanAuthFailed,
    ScinanConnectionError,
    ScinanInvalidTokenError,
    ScinanResponseError,
)

from .fake_cloud import PASSWORD, TOKEN, USERNAME, FakeCloud, error, ok

LOGIN = "/v2.0/user/login"
LIST = "/v2.0/device/list"
CONTROL = "/v2.0/sensor/control"


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(cloud_mod, "_RETRY_DELAY", 0)


@pytest.fixture
async def server():
    srv = FakeCloud()
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
async def client(server):
    async with ScinanCloudClient(USERNAME, PASSWORD, api_domain=server.url, timeout=1) as c:
        yield c


# ------------------------------------------------------------------ login


async def test_login_sends_md5_password_and_signed_params(server, client):
    assert await client.authenticate() == TOKEN
    assert client.token == TOKEN
    [params] = server.calls(LOGIN)
    assert params["account"] == USERNAME
    assert params["password"] == cloud_mod.md5_hex(PASSWORD)
    assert params["app_key"] == "100027"
    assert params["company_id"] == "1038"
    assert "token" not in params
    assert server.bad_signatures == 0


async def test_authenticate_without_force_reuses_token(server, client):
    await client.authenticate()
    await client.authenticate(force=False)
    assert len(server.calls(LOGIN)) == 1


@pytest.mark.parametrize(
    ("username", "password"),
    [(USERNAME, "wrong"), ("nobody@example.com", PASSWORD)],
    ids=["wrong-password-20007", "unknown-user-20014"],
)
async def test_login_bad_credentials(server, username, password):
    async with ScinanCloudClient(username, password, api_domain=server.url) as c:
        with pytest.raises(ScinanAuthFailed):
            await c.authenticate()
    assert len(server.calls(LOGIN)) == 1  # auth errors are never retried


async def test_login_blocked_after_five_wrong_passwords(server, client):
    server.queue(LOGIN, error("1", "blocked"))
    with pytest.raises(ScinanAuthFailed, match="5 times"):
        await client.authenticate()


async def test_login_without_token_in_response(server, client):
    server.queue(LOGIN, ok({}))
    with pytest.raises(ScinanAuthFailed, match="Token not found"):
        await client.authenticate()


# ------------------------------------------------------------ device list


async def test_update_devices_logs_in_lazily_and_parses_list(server, client):
    devices = await client.update_devices()

    assert len(server.calls(LOGIN)) == 1
    assert server.calls(LIST)[0]["token"] == TOKEN
    assert set(devices) == {"1038000000000B02", "1038000000000C03"}  # gateway + type 3 skipped

    dev = devices["1038000000000B02"]
    assert dev.name == "Thermostat_000B02"
    assert dev.type == 9
    assert dev.online
    assert dev.current_temperature == 21.5
    assert dev.target_temperature == 22.0
    assert dev.status.timestamp == datetime(2025, 11, 24, 16, 0, tzinfo=UTC)

    away = devices["1038000000000C03"]
    assert not away.online
    assert away.away
    assert away.target_temperature == 5.0


async def test_update_devices_keeps_device_objects(server, client):
    first = (await client.update_devices())["1038000000000B02"]
    second = (await client.update_devices())["1038000000000B02"]
    assert first is second


async def test_update_devices_bad_status_is_skipped(server, client):
    server.device_list = [{"type": 9, "devices": [
        {"id": "1038000000000B02", "company_id": "1038", "online": 1, "s00": "garbage"}]}]
    devices = await client.update_devices()
    dev = devices["1038000000000B02"]
    assert dev.online
    assert dev.status is None


async def test_update_devices_invalid_response(server, client):
    server.queue(LIST, ok({"not": "a list"}))
    with pytest.raises(ScinanResponseError):
        await client.update_devices()


# ------------------------------------------------------- token and retries


async def test_expired_token_relogs_and_retries(server):
    async with ScinanCloudClient(USERNAME, PASSWORD, token="old", api_domain=server.url) as c:
        await c.update_devices()
        assert c.token == TOKEN
    assert [p["token"] for p in server.calls(LIST)] == ["old", TOKEN]
    assert len(server.calls(LOGIN)) == 1


async def test_token_still_rejected_after_relogin(server, client):
    server.queue(LIST, error("10003"), error("10003"))
    with pytest.raises(ScinanInvalidTokenError):
        await client.update_devices()
    assert len(server.calls(LIST)) == 2  # one retry only


async def test_connection_timeout_retried_once(server, client):
    server.queue(LIST, "hang")
    devices = await client.update_devices()
    assert devices
    assert len(server.calls(LIST)) == 2


async def test_connection_timeout_twice_raises(server, client):
    server.queue(LIST, "hang", "hang")
    with pytest.raises(ScinanConnectionError):
        await client.update_devices()


async def test_unreachable_server_raises_connection_error():
    async with ScinanCloudClient(USERNAME, PASSWORD, api_domain="http://127.0.0.1:9",
                                 timeout=1) as c:
        with pytest.raises(ScinanConnectionError):
            await c.authenticate()


async def test_http_error_status(server, client):
    server.queue(LIST, 500)
    with pytest.raises(ScinanResponseError, match="HTTP 500"):
        await client.update_devices()


async def test_unknown_error_code(server, client):
    server.queue(LIST, error("20001", "device not exists"))
    with pytest.raises(ScinanResponseError, match="20001"):
        await client.update_devices()


# ---------------------------------------------------------------- control


async def test_control_params(server, client):
    await client.control("1038000000000B02", Code.AWAY, "1")
    [params] = server.calls(CONTROL)
    assert params["device_id"] == "1038000000000B02"
    assert params["sensor_id"] == "03"
    assert params["sensor_type"] == "1"
    assert json.loads(params["control_data"]) == {"value": "1"}
    assert server.bad_signatures == 0


@pytest.mark.parametrize(
    ("call", "sensor_id", "value"),
    [
        (lambda c: c.set_temperature("X", 21.3), "02", "21.5"),
        (lambda c: c.set_temperature("X", 2), "02", "05.0"),  # clamped to min
        (lambda c: c.set_temperature("X", 50), "02", "35.0"),  # clamped to max
        (lambda c: c.set_away("X", True), "03", "1"),
        (lambda c: c.set_away("X", False), "03", "0"),
        (lambda c: c.set_power("X", False), "01", "0"),
        (lambda c: c.set_mode("X", DeviceMode.AUTO), "12", "1"),
        (lambda c: c.set_mode("X", 2), "12", "2"),
    ],
)
async def test_setters(server, client, call, sensor_id, value):
    await call(client)
    [params] = server.calls(CONTROL)
    assert params["sensor_id"] == sensor_id
    assert json.loads(params["control_data"]) == {"value": value}


async def test_set_temperature_uses_device_limits(server, client):
    await client.update_devices()
    client.devices["1038000000000B02"].status.max_temp = 30.0
    await client.set_temperature("1038000000000B02", 33)
    assert json.loads(server.calls(CONTROL)[0]["control_data"]) == {"value": "30.0"}


# ---------------------------------------------------------- device status


async def test_device_status(server, client):
    data = await client.device_status("1038000000000A01")
    assert data["device_id"] == "1038000000000A01"
    assert data["online"] == "1"
