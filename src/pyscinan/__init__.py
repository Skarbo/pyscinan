"""Async client for Scinan / Saswell Wi-Fi thermostats."""

from .cloud import ScinanCloudClient
from .const import Code, DeviceMode, DeviceType
from .exceptions import (
    ScinanAuthFailed,
    ScinanConnectionError,
    ScinanError,
    ScinanInvalidTokenError,
    ScinanProtocolError,
    ScinanResponseError,
    ScinanTimeoutError,
)
from .local import ScinanLocalClient
from .models import ScinanDevice, ScinanStatus

__all__ = [
    "Code",
    "DeviceMode",
    "DeviceType",
    "ScinanAuthFailed",
    "ScinanCloudClient",
    "ScinanConnectionError",
    "ScinanDevice",
    "ScinanError",
    "ScinanInvalidTokenError",
    "ScinanLocalClient",
    "ScinanProtocolError",
    "ScinanResponseError",
    "ScinanStatus",
    "ScinanTimeoutError",
]
