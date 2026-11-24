# pyscinan

Async Python library for Saswell (Scinan platform) Wi-Fi thermostats.

- **Local**: talks directly to the thermostat over your network (TCP port 2000). No cloud, instant push
  updates, heartbeat and automatic reconnect.
- **Cloud**: the Saswell v2.0 API used by the Saswell "Thermostat" app.

Both clients fill the same `ScinanDevice` model. Used by the
[Scinan Saswell Thermostat](https://github.com/skarbo/hass-scinan-thermostat) Home Assistant integration.

Tested with the Saswell SAS920FHL-7W-WIFI (electric floor heating). Other thermostats using a Saswell
Thermostat app (for example SAS922WHL-7W-S-WIFI, T13FHL-7(EN) WIFI and Prosmart PST50W) are known to work
with the cloud API.

## Install

```bash
pip install pyscinan
```

Requires Python 3.12 or newer.

## Local

The thermostat must already be on your Wi-Fi (set it up with the Thermostat app). Give it a fixed IP address;
it can't be discovered automatically.

```python
import asyncio
from pyscinan import DeviceMode, ScinanLocalClient

async def main():
    async with ScinanLocalClient("192.168.1.50") as client:
        dev = client.device
        print(dev.device_id, dev.current_temperature, dev.target_temperature, dev.mode)

        client.add_listener(lambda: print("update:", dev.current_temperature, dev.target_temperature))

        await client.set_temperature(21.5)
        await client.set_mode(DeviceMode.COMFORT)
        await client.set_away(True)   # "off" on floor heating thermostats
        await asyncio.sleep(60)       # pushes arrive every 15 s and on every change

asyncio.run(main())
```

Every command returns the status the thermostat answers with. A command the thermostat ignores (for example
power on the SAS920FHL) returns the unchanged status, so compare it with what you asked for.

## Cloud

```python
from pyscinan import ScinanCloudClient

async with ScinanCloudClient("email@example.com", "password") as cloud:
    devices = await cloud.update_devices()
    for dev in devices.values():
        print(dev.device_id, dev.name, dev.online, dev.target_temperature)
    await cloud.set_temperature(dev.device_id, 21.5)
```

Pass `session=` to reuse an `aiohttp.ClientSession`. Note that the Saswell cloud only supports plain HTTP.

## Command line

```bash
pyscinan status 192.168.1.50
pyscinan listen 192.168.1.50 --seconds 60
pyscinan settemp 192.168.1.50 21.5
pyscinan away 192.168.1.50 on
pyscinan mode 192.168.1.50 1          # 0 Comfort, 1 Auto (schedule), 2 Day or night
pyscinan cloud-list email@example.com # password from $SCINAN_PASSWORD or a prompt
pyscinan --help
```

## Protocol

See [PROTOCOL.md](PROTOCOL.md) for everything known about the local protocol and the cloud API.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
```

The tests use a fake thermostat (TCP) and a fake cloud server, so no hardware or account is needed.
`scripts/hw_settings_test.py` runs against a real thermostat and changes its settings (then restores them).

## Disclaimer

Not affiliated with Saswell or Scinan. Use at your own risk.
