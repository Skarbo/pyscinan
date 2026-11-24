# Changelog

## 0.1.2 (2026-11-24)

- Local client: a command sent while disconnected no longer leaves a status waiter behind. Before, closing the
  client afterwards logged "Future exception was never retrieved".

## 0.1.1 (2026-11-24)

- Local client: when connecting, ask for the status a second time if the first request gets no answer.
  Seen on real hardware: the thermostat sent its hello, but no status for 5 seconds. Before, `connect()`
  failed with `ScinanTimeoutError`.

## 0.1.0 (2026-11-23)

First release.

- Local client (`ScinanLocalClient`): TCP port 2000, hello/device ID detection, status push listeners,
  heartbeat, idle timeout and automatic reconnect, commands for temperature, away, power, program and mode.
- Cloud client (`ScinanCloudClient`): Saswell v2.0 API login, device list, device status and control,
  with token refresh and one retry on connection errors.
- Shared `ScinanDevice` / `ScinanStatus` models.
- `pyscinan` command line tool.
