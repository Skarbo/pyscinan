"""Async client for the Scinan / Saswell cloud API.

Ported from hass-scinan-thermostat 1.1.3 (custom_components/scinan_thermostat/api).
Only the Saswell v2.0 API (http://api.saswell.com.cn, plain HTTP) is supported.
The old Scinan v1.0 API (api.scinan.com) no longer answers and was removed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import time
from typing import Any

import aiohttp

from .const import (
    API_DOMAIN_SASWELL,
    API_TIMEOUT_DEFAULT,
    COMPANY_ID_THERMOSTAT,
    Code,
    DeviceMode,
    DeviceType,
)
from .exceptions import (
    ScinanAuthFailed,
    ScinanConnectionError,
    ScinanInvalidTokenError,
    ScinanProtocolError,
    ScinanResponseError,
)
from .models import ScinanDevice, ScinanStatus
from .protocol import format_bool, format_temperature

_LOGGER = logging.getLogger(__name__)


_URI_AUTH = "/v2.0/user/login"
_URI_DEVICE_LIST = "/v2.0/device/list"
_URI_DEVICE_CONTROL = "/v2.0/sensor/control"

_REQUEST_PARAMS = {
    "app_key": "100027",
    "company_id": COMPANY_ID_THERMOSTAT,
    "imei": "357014732382494",
}
_APP_SECRET = base64.b64decode("RTE0OTkwQ0JGNUM3NDBEQjg1MzBBQTZCRTA0REQ5RjM=").decode()

CODE_WRONG_PASSWORD_BLOCKED = "1"
CODE_TOKEN_EXPECTED = "10002"
CODE_TOKEN_EXPIRED = "10003"
CODE_WRONG_PASSWORD = "20007"
CODE_USER_NOT_EXIST = "20014"

_SUPPORTED_TYPES = tuple(int(t) for t in DeviceType)
_RETRY_DELAY = 3


def md5_hex(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()


def sign_params(params: dict[str, str], secret: str = _APP_SECRET) -> str:
    """MD5(secret + k1 + v1 + k2 + v2 ... + secret), keys sorted, uppercase hex."""
    payload = secret + "".join(f"{k}{v}" for k, v in sorted(params.items())) + secret
    return hashlib.md5(payload.encode()).hexdigest().upper()


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class ScinanCloudClient:
    """Cloud API client. One instance per account."""

    def __init__(
        self,
        username: str,
        password: str,
        *,
        token: str | None = None,
        session: aiohttp.ClientSession | None = None,
        api_domain: str = API_DOMAIN_SASWELL,
        timeout: float = API_TIMEOUT_DEFAULT,
    ) -> None:
        self._username = username
        self._password = password
        self._token = token
        self._domain = api_domain
        self._timeout = timeout
        self._session = session
        self._own_session = session is None
        self.devices: dict[str, ScinanDevice] = {}

    @property
    def token(self) -> str | None:
        return self._token

    @property
    def api_domain(self) -> str:
        return self._domain

    async def __aenter__(self) -> ScinanCloudClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        if self._own_session and self._session is not None:
            await self._session.close()
            self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    # -------------------------------------------------------- authentication

    async def authenticate(self, *, force: bool = True) -> str:
        """Log in and return the token."""
        if self._token and not force:
            return self._token
        _LOGGER.debug("Authenticating as %s", self._username)
        data = await self._request(
            _URI_AUTH,
            {"account": self._username, "password": md5_hex(self._password)},
            retry=False,
        )
        token = data.get("access_token") if isinstance(data, dict) else None
        if not token:
            raise ScinanAuthFailed("Token not found in response")
        self._token = token
        return token

    # ------------------------------------------------------------- requests

    def _prepare_params(self, params: dict[str, str]) -> dict[str, str]:
        base = {**params, **_REQUEST_PARAMS, "timestamp": _timestamp(), "token": self._token}
        base = {k: v for k, v in base.items() if v is not None}
        base["sign"] = sign_params(base)
        return base

    async def _request(self, uri: str, params: dict[str, str], *, retry: bool = True) -> Any:
        if self._token is None and uri != _URI_AUTH:
            await self.authenticate()
        try:
            return await self._request_once(uri, params)
        except ScinanInvalidTokenError:
            if not retry:
                raise
            _LOGGER.debug("Token rejected, re-authenticating")
            await asyncio.sleep(_RETRY_DELAY)
            await self.authenticate()
        except ScinanConnectionError as err:
            if not retry:
                raise
            _LOGGER.debug("Request failed (%s), retrying once", err)
            await asyncio.sleep(_RETRY_DELAY)
        return await self._request_once(uri, params)

    async def _request_once(self, uri: str, params: dict[str, str]) -> Any:
        url = f"{self._domain}{uri}"
        try:
            async with asyncio.timeout(self._timeout):
                resp = await self._get_session().post(url, params=self._prepare_params(params))
                if resp.status != 200:
                    raise ScinanResponseError(f"{uri} returned HTTP {resp.status}")
                data = await resp.json(content_type=None)
        except (TimeoutError, aiohttp.ClientError) as err:
            raise ScinanConnectionError(f"Request {uri} failed: {err}") from err
        return self._handle_response(uri, data)

    def _handle_response(self, uri: str, data: Any) -> Any:
        if not isinstance(data, dict):
            raise ScinanResponseError(f"{uri}: unexpected response {data!r}")
        code = message = None
        if str(data.get("result_code")) != "0":
            code = str(data.get("result_code", "unknown"))
            message = data.get("result_message")
        if code is not None:
            err = f"{message or 'API error'} ({code})"
            _LOGGER.debug("%s: %s", uri, err)
            if code in (CODE_WRONG_PASSWORD, CODE_USER_NOT_EXIST):
                raise ScinanAuthFailed(err)
            if code == CODE_WRONG_PASSWORD_BLOCKED:
                raise ScinanAuthFailed(f"Incorrect password 5 times in a row ({code})")
            if code in (CODE_TOKEN_EXPIRED, CODE_TOKEN_EXPECTED):
                raise ScinanInvalidTokenError(err)
            raise ScinanResponseError(err)
        return data.get("result_data")

    # -------------------------------------------------------------- devices

    async def update_devices(self) -> dict[str, ScinanDevice]:
        """Fetch the device list and status of every thermostat."""
        data = await self._request(_URI_DEVICE_LIST, {})
        for item in self._device_items(data):
            device_id = item.get("id")
            if not device_id or item.get("company_id") != COMPANY_ID_THERMOSTAT:
                _LOGGER.debug("Skipping non-thermostat %s", device_id)
                continue
            device = self.devices.setdefault(device_id, ScinanDevice(device_id=device_id))
            device.name = item.get("title")
            device.type = _to_int(item.get("type"))
            device.company_id = item.get("company_id")
            device.online = str(item.get("online")) == "1"
            raw = item.get("s00")
            if raw:
                try:
                    device.update_status(ScinanStatus.parse(raw, cloud=True))
                except ScinanProtocolError as err:
                    _LOGGER.warning("Could not parse status of %s: %s", device_id, err)
        return self.devices

    def _device_items(self, data: Any) -> list[dict]:
        if not isinstance(data, list):
            raise ScinanResponseError("Invalid device list response")
        items: list[dict] = []
        for group in data:
            if _to_int(group.get("type")) in _SUPPORTED_TYPES:
                for dev in group.get("devices") or []:
                    dev.setdefault("type", group.get("type"))
                    items.append(dev)
        return items

    async def device_status(self, device_id: str) -> dict[str, Any]:
        """`device/status`: works even for a device missing from `device/list`.

        Returns e.g. {device_id, ip (public WAN IP), online, online_time, s00, timestamp, user_id}.
        """
        data = await self._request("/v2.0/device/status", {"device_id": device_id})
        return data if isinstance(data, dict) else {}

    async def control(self, device_id: str, code: Code, value: str) -> None:
        """Send a sensor control command."""
        await self._request(
            _URI_DEVICE_CONTROL,
            {
                "device_id": device_id,
                "sensor_id": code.sensor_id,
                "control_data": '{"value": "' + value + '"}',
                "sensor_type": "1",
            },
        )

    async def set_temperature(self, device_id: str, temperature: float) -> None:
        dev = self.devices.get(device_id)
        value = format_temperature(
            temperature, dev.min_temp if dev else 5.0, dev.max_temp if dev else 35.0
        )
        await self.control(device_id, Code.TARGET_TEMPERATURE, value)

    async def set_power(self, device_id: str, on: bool) -> None:
        await self.control(device_id, Code.POWER, format_bool(on))

    async def set_away(self, device_id: str, away: bool) -> None:
        await self.control(device_id, Code.AWAY, format_bool(away))

    async def set_mode(self, device_id: str, mode: DeviceMode | int) -> None:
        await self.control(device_id, Code.MODE, str(int(mode)))


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
