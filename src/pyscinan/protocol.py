"""The plain-text line protocol spoken on TCP port 2000.

Every line is ``/<DEVICE_ID>/<CODE>/<SENSOR_TYPE>/<DATA>`` terminated by CRLF.
On connect the device sends a hello: ``/<DEVICE_ID>/type/1/<device type>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .const import DEVICE_TEMP_MAX, DEVICE_TEMP_MIN, LOCAL_SENSOR_TYPE, Code
from .exceptions import ScinanProtocolError

DEVICE_ID_RE = re.compile(r"^[0-9A-Za-z]{16}$")
HELLO_CODE = "type"


@dataclass(frozen=True, slots=True)
class Message:
    """One protocol line."""

    device_id: str
    code: str
    sensor_type: str
    data: str

    @property
    def is_hello(self) -> bool:
        return self.code == HELLO_CODE

    def __str__(self) -> str:
        return f"/{self.device_id}/{self.code}/{self.sensor_type}/{self.data}"


def parse_line(line: str) -> Message:
    """Parse one received line."""
    line = line.strip()
    parts = line.split("/", 4)
    if len(parts) != 5 or parts[0] != "" or not DEVICE_ID_RE.match(parts[1]):
        raise ScinanProtocolError(f"Unrecognised line: {line!r}")
    return Message(device_id=parts[1], code=parts[2], sensor_type=parts[3], data=parts[4])


def build_line(device_id: str, code: str | Code, data: str) -> bytes:
    """Build one line to send."""
    if not DEVICE_ID_RE.match(device_id):
        raise ValueError(f"Invalid device id: {device_id!r}")
    code = str(code)
    if not code.upper().startswith("S"):
        code = f"S{code}"
    return f"/{device_id}/{code}/{LOCAL_SENSOR_TYPE}/{data}\r\n".encode()


def format_temperature(value: float, minimum: float = DEVICE_TEMP_MIN,
                       maximum: float = DEVICE_TEMP_MAX) -> str:
    """Clamp to limits and round to 0.5 (what the app sends, e.g. '21.5', '05.0')."""
    value = min(max(float(value), minimum), maximum)
    value = round(value * 2) / 2
    return f"{value:04.1f}"


def format_bool(value: bool) -> str:
    return "1" if value else "0"
