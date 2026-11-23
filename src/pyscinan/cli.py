"""Command line tool: ``pyscinan <command> ...`` or ``python -m pyscinan``.

Local:
  pyscinan listen HOST [--seconds N]
  pyscinan status HOST
  pyscinan settemp HOST 21.5
  pyscinan power HOST on|off
  pyscinan away HOST on|off
  pyscinan program HOST on|off
  pyscinan mode HOST 0|1|2
  pyscinan raw HOST S05 -1,1 [--no-wait]
Cloud:
  pyscinan cloud-list USERNAME            (password from $SCINAN_PASSWORD or prompt)
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import sys
from dataclasses import asdict

from .cloud import ScinanCloudClient
from .exceptions import ScinanError
from .local import ScinanLocalClient
from .models import ScinanDevice


def _onoff(value: str) -> bool:
    if value.lower() in ("1", "on", "true", "yes"):
        return True
    if value.lower() in ("0", "off", "false", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected on/off, got {value!r}")


def _print_device(dev: ScinanDevice | None) -> None:
    if dev is None:
        print("(no device)")
        return
    st = dev.status
    print(f"device_id={dev.device_id} type={dev.type} name={dev.name} online={dev.online}")
    if st:
        fields = {k: v for k, v in asdict(st).items() if k not in ("raw", "extra")}
        print("  " + " ".join(f"{k}={v}" for k, v in fields.items() if v is not None))
        print(f"  raw={st.raw}" + (f" extra={st.extra}" if st.extra else ""))


async def _run(args: argparse.Namespace) -> int:

    if args.cmd == "cloud-list":
        password = os.environ.get("SCINAN_PASSWORD") or getpass.getpass("Password: ")
        async with ScinanCloudClient(args.host, password) as cloud:
            devices = await cloud.update_devices()
            for dev in devices.values():
                _print_device(dev)
        return 0

    client = ScinanLocalClient(args.host, device_id=args.id, reconnect=args.cmd == "listen")
    client.add_message_listener(lambda m: print("<<", m))
    await client.connect()
    try:
        if args.cmd == "listen":
            await asyncio.sleep(args.seconds or 10**9)
        elif args.cmd == "status":
            pass  # connect() already fetched status
        elif args.cmd == "settemp":
            await client.set_temperature(float(args.args[0]))
        elif args.cmd == "power":
            await client.set_power(_onoff(args.args[0]))
        elif args.cmd == "away":
            await client.set_away(_onoff(args.args[0]))
        elif args.cmd == "program":
            await client.set_program(_onoff(args.args[0]))
        elif args.cmd == "mode":
            await client.set_mode(int(args.args[0]))
        elif args.cmd == "raw":
            payload = args.args[1] if len(args.args) > 1 else "-1"
            await client.send_command(args.args[0], payload, wait_for_status=not args.no_wait)
            if args.no_wait:
                await asyncio.sleep(3)
        _print_device(client.device)
    finally:
        await client.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pyscinan", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["listen", "status", "settemp", "power", "away",
                                    "program", "mode", "raw", "cloud-list"])
    ap.add_argument("host", help="thermostat IP (or cloud username)")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--id", help="16-char device id (normally learned from the hello)")
    ap.add_argument("--seconds", type=float, help="listen duration")
    ap.add_argument("--no-wait", action="store_true", help="raw: don't wait for S00 reply")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130
    except ScinanError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
