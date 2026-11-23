"""Constants shared by the local and cloud clients."""

from enum import IntEnum, StrEnum

# Local (LAN / AP mode) protocol
LOCAL_TCP_PORT = 2000
LOCAL_AP_HOST = "10.10.100.254"
LOCAL_HEARTBEAT_INTERVAL = 8.0  # app sends SPOLL every ~8 s
LOCAL_IDLE_TIMEOUT = 30.0  # reconnect if nothing received for this long
LOCAL_SENSOR_TYPE = "1"

# Cloud API
API_DOMAIN_SASWELL = "http://api.saswell.com.cn"
API_TIMEOUT_DEFAULT = 30

COMPANY_ID_THERMOSTAT = "1038"  # 1038 = thermostat, 1015 = gateway

DEVICE_TEMP_MIN = 5.0
DEVICE_TEMP_MAX = 35.0


class DeviceType(IntEnum):
    """Known thermostat device types (the `type` value in hello / device list)."""

    WHL = 6  # water floor heating, e.g. SAS922WHL-7W-S-WIFI
    FHL = 9  # electric floor heating, e.g. SAS920FHL-7W-WIFI


class Code(StrEnum):
    """Sensor / command codes (same numbering locally and in the cloud)."""

    STATUS = "S00"
    POWER = "S01"
    TARGET_TEMPERATURE = "S02"
    AWAY = "S03"
    PROGRAM = "S07"
    MODE = "S12"
    WIFI = "S99"
    POLL = "SPOLL"

    @property
    def sensor_id(self) -> str:
        """Cloud API sensor id ('S02' -> '02')."""
        return self.value[1:]


class DeviceMode(IntEnum):
    """System mode (S12), status field `system_mode`.

    Observed on type 9 (2026-11-23):
      0 COMFORT       manual, holds the set target
      1 AUTO          runs the built-in schedule; device sets program=1 and the
                      target jumps to the schedule value (e.g. 16.0)
      2 DAY_OR_NIGHT  program=0, target unchanged
    Other values (3) are ignored by the device.
    """

    COMFORT = 0
    AUTO = 1
    DAY_OR_NIGHT = 2
