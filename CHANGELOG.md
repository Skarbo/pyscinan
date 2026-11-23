# Changelog

## 0.1.0 (unreleased)

First release.

- Local client (`ScinanLocalClient`): TCP port 2000, hello/device ID detection, status push listeners,
  heartbeat, idle timeout and automatic reconnect, commands for temperature, away, power, program and mode.
- Cloud client (`ScinanCloudClient`): Saswell v2.0 API login, device list, device status and control,
  with token refresh and one retry on connection errors.
- Shared `ScinanDevice` / `ScinanStatus` models.
- `pyscinan` command line tool.
