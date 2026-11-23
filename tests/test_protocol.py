import pytest

from pyscinan import DeviceMode, ScinanProtocolError, ScinanStatus
from pyscinan.cloud import sign_params
from pyscinan.models import ScinanDevice
from pyscinan.protocol import build_line, format_temperature, parse_line

REAL_STATUS = "1,22.0,22.0,0,0,0,0,0,0,05,35"  # captured from type 9, 2026-11-23


def test_parse_hello():
    msg = parse_line("/1038000000000A01/type/1/9\r\n")
    assert msg.device_id == "1038000000000A01"
    assert msg.is_hello
    assert msg.data == "9"


def test_parse_status_line():
    msg = parse_line(f"/1038000000000A01/S00/1/{REAL_STATUS}")
    assert msg.code == "S00"
    assert msg.data == REAL_STATUS


@pytest.mark.parametrize("line", ["", "hello", "/short/S00/1/x", "1038000000000A01/S00/1/x"])
def test_parse_bad_lines(line):
    with pytest.raises(ScinanProtocolError):
        parse_line(line)


def test_build_line():
    assert build_line("1038000000000A01", "S02", "21.5") == b"/1038000000000A01/S02/1/21.5\r\n"
    assert build_line("1038000000000A01", "02", "21.5") == b"/1038000000000A01/S02/1/21.5\r\n"
    with pytest.raises(ValueError):
        build_line("bad", "S02", "1")


def test_parse_local_status():
    st = ScinanStatus.parse(REAL_STATUS)
    assert st.power is True
    assert st.current_temperature == 22.0
    assert st.target_temperature == 22.0
    assert st.unit == 0
    assert st.away is False
    assert st.system_mode == 0
    assert st.min_temp == 5
    assert st.max_temp == 35
    assert st.system_mode_type is None
    assert st.timestamp is None


def test_parse_cloud_status_has_leading_timestamp():
    st = ScinanStatus.parse("1564834881996,1,26.5,16.0,0,1,1,0,0,1,05,35", cloud=True)
    assert st.timestamp is not None and st.timestamp.year == 2019
    assert st.power is True
    assert st.current_temperature == 26.5
    assert st.target_temperature == 16.0
    assert st.away is True
    assert st.program is True
    assert st.system_mode == 1


def test_parse_short_status():
    with pytest.raises(ScinanProtocolError):
        ScinanStatus.parse("1,22")


def test_device_properties():
    dev = ScinanDevice(device_id="1038000000000A01", online=True)
    assert dev.mode is None and dev.min_temp == 5.0
    dev.update_status(ScinanStatus.parse("1,20.0,22.0,0,0,0,0,0,2,05,30"))
    assert dev.mode is DeviceMode.DAY_OR_NIGHT
    assert dev.max_temp == 30
    assert dev.is_heating
    dev.update_status(ScinanStatus.parse("1,20.0,22.0,0,1,0,0,0,7,05,30"))
    assert dev.mode is None  # unknown value
    assert not dev.is_heating  # away


@pytest.mark.parametrize(("value", "expected"), [
    (21.5, "21.5"), (21.3, "21.5"), (21.2, "21.0"), (5, "05.0"), (2, "05.0"), (40, "35.0"),
])
def test_format_temperature(value, expected):
    assert format_temperature(value) == expected


def test_sign_params_matches_old_implementation():
    # Re-implementation of hass-scinan-thermostat utils.create_md5_signature
    import hashlib
    secret = "SECRET"
    params = {"b": "2", "a": "1", "token": "x"}
    payload = secret + "a1b2tokenx" + secret
    assert sign_params(params, secret) == hashlib.md5(payload.encode()).hexdigest().upper()
