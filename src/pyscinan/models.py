"""Device model shared by the local and cloud clients."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .const import DEVICE_TEMP_MAX, DEVICE_TEMP_MIN, DeviceMode
from .exceptions import ScinanProtocolError


def _int(values: list[str], idx: int) -> int | None:
    try:
        return int(float(values[idx]))
    except (IndexError, ValueError):
        return None


def _float(values: list[str], idx: int) -> float | None:
    try:
        return float(values[idx])
    except (IndexError, ValueError):
        return None


def _bool(values: list[str], idx: int) -> bool | None:
    val = _int(values, idx)
    return None if val is None else val == 1


@dataclass(slots=True)
class ScinanStatus:
    """A parsed S00 status string.

    Field order (local / direct mode):
        power, current_temp, target_temp, unit, away, program,
        fan_mode, run_mode, system_mode[, min_temp, max_temp, system_mode_type]

    The cloud status string has one extra leading field (timestamp in ms).
    """

    raw: str
    power: bool | None = None
    current_temperature: float | None = None
    target_temperature: float | None = None
    unit: int | None = None  # 0 = Celsius
    away: bool | None = None
    program: bool | None = None
    fan_mode: int | None = None
    run_mode: int | None = None
    system_mode: int | None = None
    min_temp: float | None = None
    max_temp: float | None = None
    system_mode_type: int | None = None
    extra: list[str] = field(default_factory=list)
    timestamp: datetime | None = None  # only from cloud

    @classmethod
    def parse(cls, data: str, *, cloud: bool = False) -> ScinanStatus:
        """Parse the comma separated status payload."""
        values = [v.strip() for v in data.strip().split(",")]
        timestamp = None
        if cloud:
            if not values:
                raise ScinanProtocolError(f"Empty cloud status: {data!r}")
            try:
                timestamp = datetime.fromtimestamp(int(values[0]) / 1000, tz=UTC)
            except ValueError:
                timestamp = None
            values = values[1:]
        if len(values) < 3:
            raise ScinanProtocolError(f"Status too short: {data!r}")
        status = cls(
            raw=data,
            power=_bool(values, 0),
            current_temperature=_float(values, 1),
            target_temperature=_float(values, 2),
            unit=_int(values, 3),
            away=_bool(values, 4),
            program=_bool(values, 5),
            fan_mode=_int(values, 6),
            run_mode=_int(values, 7),
            system_mode=_int(values, 8),
            min_temp=_float(values, 9),
            max_temp=_float(values, 10),
            system_mode_type=_int(values, 11),
            extra=values[12:],
            timestamp=timestamp,
        )
        if status.current_temperature is None or status.target_temperature is None:
            raise ScinanProtocolError(f"Status without temperatures: {data!r}")
        return status


@dataclass(slots=True)
class ScinanDevice:
    """A thermostat, as seen by either client."""

    device_id: str
    name: str | None = None
    type: int | None = None
    company_id: str | None = None
    online: bool = False
    status: ScinanStatus | None = None
    last_updated: datetime | None = None

    def update_status(self, status: ScinanStatus) -> None:
        """Store a new status."""
        self.status = status
        self.last_updated = status.timestamp or datetime.now(UTC)

    # Convenience accessors -------------------------------------------------

    @property
    def is_on(self) -> bool | None:
        return self.status.power if self.status else None

    @property
    def away(self) -> bool | None:
        return self.status.away if self.status else None

    @property
    def current_temperature(self) -> float | None:
        return self.status.current_temperature if self.status else None

    @property
    def target_temperature(self) -> float | None:
        return self.status.target_temperature if self.status else None

    @property
    def min_temp(self) -> float:
        return (self.status.min_temp if self.status else None) or DEVICE_TEMP_MIN

    @property
    def max_temp(self) -> float:
        return (self.status.max_temp if self.status else None) or DEVICE_TEMP_MAX

    @property
    def mode(self) -> DeviceMode | None:
        """System mode as enum, None if unknown."""
        if not self.status or self.status.system_mode is None:
            return None
        try:
            return DeviceMode(self.status.system_mode)
        except ValueError:
            return None

    @property
    def is_heating(self) -> bool:
        """Best guess: the protocol has no confirmed 'relay on' field yet."""
        return bool(
            self.online
            and self.is_on
            and not self.away
            and self.current_temperature is not None
            and self.target_temperature is not None
            and self.current_temperature < self.target_temperature
        )
