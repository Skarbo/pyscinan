"""Hardware test against a real thermostat. It CHANGES settings, then restores the baseline.

Runs away on/off, power, modes 0-3, program and a high target for 35 s, logging every status.

Usage: uv run python scripts/hw_settings_test.py THERMOSTAT_IP
"""
import asyncio
import sys
import time

from pyscinan import ScinanError, ScinanLocalClient

if len(sys.argv) != 2:
    sys.exit(__doc__)
HOST = sys.argv[1]
T0 = time.monotonic()


def log(*a):
    print(f"[{time.monotonic() - T0:6.1f}s]", *a, flush=True)


async def step(client, label, coro, settle=3.0):
    log(f"=== {label}")
    try:
        st = await coro
        log(f"    ack: {st.raw}")
    except ScinanError as err:
        log(f"    ERROR: {err!r}")
    await asyncio.sleep(settle)


async def main():
    client = ScinanLocalClient(HOST, command_timeout=4)
    client.add_message_listener(lambda m: log("<<", m.code, m.data))
    await client.connect()
    base = client.device.status
    log(f"BASELINE: {base.raw}")
    try:
        await step(client, "away ON", client.set_away(True))
        await step(client, "settemp (same) while away",
                   client.set_temperature(base.target_temperature))
        await step(client, "away OFF", client.set_away(False))

        await step(client, "power OFF", client.set_power(False), settle=6)
        await step(client, "settemp (same) while off",
                   client.set_temperature(base.target_temperature))
        await step(client, "power ON", client.set_power(True))

        for m in (1, 2, 3, 0):
            await step(client, f"mode {m}", client.set_mode(m))

        await step(client, "program ON", client.set_program(True))
        await step(client, "program OFF", client.set_program(False))

        await step(client, "settemp 28 (force heating), watch ~35s",
                   client.set_temperature(28), settle=35)
    finally:
        log("=== RESTORE")
        st = client.device.status
        try:
            if st.away != base.away:
                await client.set_away(base.away)
            if st.power != base.power:
                await client.set_power(base.power)
            if st.program != base.program:
                await client.set_program(base.program)
            if client.device.status.system_mode != base.system_mode:
                await client.set_mode(base.system_mode)
            await client.set_temperature(base.target_temperature)
            final = await client.request_status()
            log(f"FINAL:    {final.raw}")
            log(f"BASELINE: {base.raw}")
        finally:
            await client.close()


asyncio.run(main())
