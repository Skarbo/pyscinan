import pytest

from .fake_thermostat import FakeThermostat


@pytest.fixture
async def fake():
    dev = FakeThermostat()
    await dev.start()
    yield dev
    await dev.stop()
