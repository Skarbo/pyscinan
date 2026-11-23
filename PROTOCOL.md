# Scinan / Saswell thermostat protocol

What `pyscinan` knows about the protocols, from testing on SAS920FHL (device type 9) thermostats and from
watching how the official Saswell "Thermostat" app talks to them. Nothing here is official documentation.

✅ = confirmed on hardware, ⚠️ = behaves differently than expected, ❓ = seen in the app, not tested.

## Local protocol (TCP port 2000)

Plain ASCII lines, CRLF terminated. **No encryption, no authentication**: anyone on the network who can reach
port 2000 can control the thermostat.

    /<DEVICE_ID>/<CODE>/<SENSOR_TYPE>/<DATA>

- `DEVICE_ID`: 16 characters, the same ID the cloud uses (thermostats start with `1038`).
- `CODE`: `S` + 2 hex digits (`S00`, `S02`, `S12` ...), `SPOLL`, or `type` (hello only).
- `SENSOR_TYPE`: always `1`.

### Connection

- On connect the device sends a hello, `/<id>/type/1/<device type>`, ending with `\n` only, followed right
  away by an `S00` status.
- Heartbeat: send `/<id>/SPOLL/1/1`, the device answers `/<id>/SPOLL/1/OK`. The app sends it every ~8 s.
  `pyscinan` sends it every 8 s and treats 30 s without any data as a dead connection.
- The device **pushes `S00` every 15 s** by itself, and **immediately when a value changes** (also changes made
  on the thermostat or in the phone app).
- Every accepted command is answered with a full `S00` status (~0.1 s). An ignored command gets **no reply**.
- **Several TCP clients can be connected at once.** Replies are sent to all of them. The phone app (cloud)
  keeps working while local clients are connected.
- The physical on/off button cuts power to the Wi-Fi module too. The device just goes silent (no TCP close),
  comes back ~25 s after power-on with a new hello, and keeps its settings.

### Commands

| Code | Data | Meaning | Type 9 |
|---|---|---|---|
| `S00` | `-1` | Request full status | ✅ |
| `S01` | `1`/`0` | Power on/off | ⚠️ ignored (no reply, display unchanged). Use away as "off". |
| `S02` | `21.5` | Target temperature, 0.5 steps, formatted `%04.1f` (`05.0`). **Also cancels away.** | ✅ |
| `S03` | `1`/`0` | Away (frost protection). While away the status shows target `05.0`. Away off restores the previous target. | ✅ |
| `S07` | `1`/`0` | Program (schedule) on/off | ⚠️ ignored; use `S12 1` |
| `S12` | `0`/`1`/`2` | System mode, see below | ✅ |
| `S05`, `S06`, `S08`, `S11`, `S13` | | Model-specific settings and schedule data | ❓ |
| `S1A`, `S1B`, `S1C`, `S2A` | | Installer parameters (calibration, limits). Don't touch. | ❓ |
| `S99` | `<ssid>,<password>!` | Wi-Fi provisioning, **only in AP mode** (ignored in station mode) | ❓ |

`S12` system modes on type 9:

| Value | Name | Effect |
|---|---|---|
| 0 | Comfort (manual) | holds the target set with `S02` |
| 1 | Auto (schedule) | `program=1`, target follows the built-in schedule |
| 2 | Day or night | target unchanged; exact meaning unknown |
| 3+ | | ignored |

### Status (`S00`) payload

    power,cur_temp,target_temp,unit,away,program,fan_mode,run_mode,system_mode[,min,max,sys_mode_type]

Example (type 9): `1,21.5,21.5,0,0,0,0,0,0,05,35`

| Idx | Field | Notes |
|---|---|---|
| 0 | power | 1/0 |
| 1 | current temperature | °C |
| 2 | target temperature | `05.0` while away |
| 3 | unit | 0 = °C |
| 4 | away | 1/0 |
| 5 | program | schedule active |
| 6 | fan_mode | |
| 7 | run_mode | **not** a heating flag on type 9 |
| 8 | system_mode | same value as `S12` |
| 9, 10 | min / max target | `05`, `35` |
| 11 | system_mode_type | not sent by type 9 |

**No field reports whether the thermostat is heating** (the display shows a heating icon, the status doesn't).
`ScinanDevice.is_heating` estimates it as `on and not away and current < target`.

### Discovery

Not supported. The SDK in the app has a UDP mode (port 4000, broadcast and group `224.5.0.7`), but the
thermostats tested didn't answer it, nor the Hi-Flying `48899` probe, even from the same subnet. Use the IP
address (give the thermostat a DHCP reservation).

### Wi-Fi provisioning (AP mode)

How the app sets up Wi-Fi (not implemented in `pyscinan`):

1. Put the thermostat in AP mode (button sequence on the thermostat) and join its Wi-Fi.
2. TCP `10.10.100.254:2000`, send `/SCINANIOT/DEVICE_REQUEST`, read the device ID from the reply.
3. Send `/<id>/S99/1/<ssid>,<password>!` and expect `/S99/1/OK`. SSIDs or passwords containing `,` or `!`
   probably break this format.
4. The app then binds the device to the account in the cloud (`device/add`), which only works for a few
   minutes after provisioning.

In station mode only port 2000 is open, and `S99` and `DEVICE_REQUEST` are ignored, so Wi-Fi can only be
changed in AP mode. The device ID doesn't change with the Wi-Fi.

## Cloud API (Saswell v2.0)

- Base URL `http://api.saswell.com.cn/v2.0/`. **Plain HTTP only** (HTTPS doesn't answer): the MD5 password hash
  and the token are sent unencrypted.
- The old Scinan v1.0 API (`api.scinan.com`) no longer works.
- Every request is a POST with query parameters `app_key`, `company_id` (`1038` for thermostats), `imei`,
  `timestamp`, `token` (after login) and `sign` = uppercase MD5 of `secret + k1v1k2v2... (keys sorted) + secret`.
  The app key, IMEI and secret are the app's own (see `cloud.py`).
- Replies: `{"result_code": "0", "result_message": "...", "result_data": ...}`.

| Endpoint | Parameters | Notes |
|---|---|---|
| `user/login` | `account`, `password` (MD5 hex) | → `access_token` |
| `device/list` | | groups `{type, devices: [...]}`; each device has `id`, `title`, `company_id`, `online`, `s00` |
| `device/status` | `device_id` | `{online, s00, ...}` |
| `sensor/control` | `device_id`, `sensor_id` (code without `S`, e.g. `02`), `sensor_type=1`, `control_data={"value": "21.5"}` | |

The cloud `s00` has one extra leading field, a timestamp in ms, so every index shifts by one.

Cloud commands behave exactly like local ones: `sensor/control` answers OK in ~0.3 s and the new status shows
in `device/list` ~2 s later. An ignored command (like `S01` on type 9) still gets OK, so compare the new
status with what you asked for.

Error codes: `20007` wrong password, `20014` user doesn't exist, `1` blocked after 5 wrong passwords,
`10002`/`10003` token missing/expired, `20001` device doesn't exist (for this account), `20002` device already
added, `20003` device authentication error.

### Live push (MQTT, not implemented)

The app gets live updates from an MQTT broker at `tcp://push.saswell.com.cn:1886` (plain TCP):

- MQTT 3.1 only (`MQIsdp`), username = account email, password = the **plain** password, a short alphanumeric
  client ID. No subscribe is needed.
- The server publishes (QoS 1) every status of the account's devices. The status is in the **topic**, the
  body is empty: `/<device id>/S00/1/1,24.5,25.0,0,0,0,0,0,0,05,35`.
- The broker seems to block clients that connect too often or with wrong credentials. Reconnect slowly.
