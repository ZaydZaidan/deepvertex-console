<div align="center">

<img src="gui_t1/ikon/deepvertex-app-icon.png" width="120" alt="DeepVertex"/>

# DeepVertex Marine Technology Console

**BlueROV2 mission console for coral-mapping surveys — ROV mode.**

**BlueOS-compatible. Tested on the BlueROV2 (Ubuntu 24.04 and Windows 10/11).**

A QGroundControl-style topside application (PySide6) for the BlueRobotics BlueROV2:
three live camera streams, full MAVLink telemetry, gamepad teleop with safety
interlocks, compressed **and** onboard-RAW capture with a clear per-dive file layout,
pre-flight sensor health, failsafe management, calibration tools — and a complete
per-session sensor log. Designed to be run by a dive crew, not by a software engineer.

[![CI](https://github.com/ZaydZaidan/deepvertex-console/actions/workflows/ci.yml/badge.svg)](https://github.com/ZaydZaidan/deepvertex-console/actions/workflows/ci.yml)
![Version](https://img.shields.io/badge/version-1.1.7-00b3d6)
![Platform](https://img.shields.io/badge/platform-Ubuntu%2024.04%20%7C%20Windows%2010%2F11-333)
![License](https://img.shields.io/badge/license-PolyForm%20Strict-7048e8)
![BlueOS](https://img.shields.io/badge/BlueOS-compatible-1f6feb)
![Tested on](https://img.shields.io/badge/tested%20on-BlueROV2-00b3d6)
![Python](https://img.shields.io/badge/python-3.12-3776ab)

</div>

![Console — Pilot Focus layout](docs/assets/console-pilot.png)

---

## Why this exists

Underwater survey crews need one screen that answers three questions at all times:
*is the ROV healthy, where is it, and is what I care about being recorded?* This
console is built around that.

- **Safety first** — dead-man enforced teleop, pre-flight sensor health from the
  autopilot itself, leak/battery/temperature/link alerts, and a storage guard that
  blocks REC/SNAP before a dive-critical disk fills up.
- **Two qualities of recording, one clean file tree** — compressed video for quick
  review, uncompressed RAW for photogrammetry, per camera, clearly split on disk.
- **One obvious folder** — every dive writes to `Documents/DeepVertex` (snaps,
  videos, RAW transfers, MAVLink tlog, sensor CSV and the application log), with a
  one-time per-session choice of a custom folder.
- **Built for the boat** — large legible text at 120% by default, high-contrast ink
  theme for sunlight, everything reachable with the gamepad you already hold.

## Feature highlights

| Area | What you get |
|---|---|
| **Video** | 3 simultaneous streams (front + stereo L/R via BlueOS), camera panel fitted to the camera resolution (no letterbox), live Hz badge, health state |
| **Camera ON/OFF** | Per-camera toggle that stops/starts the stream **inside BlueOS** (frees the Pi's CPU/USB), with a red pulsing REC / green SNAP flash animation |
| **Telemetry** | Attitude + compass, depth/temperature, dual battery bars (ArduPilot remaining + LiPo estimate with 14.8 V = 0 %), sub instruments, MAVLink tlog per session |
| **Sensor log** | `sensors.csv` at 10 Hz per session (voltage, current, battery, depth, water temp, pressure, attitude, EKF, message count) — logged topside, zero extra load on the robot |
| **Teleop** | DS4 / Xbox gamepad (evdev on Linux, SDL on Windows), dead-man hold, MAX SPEED 25/50/75/100 %, depth-hold, camera tilt + lights with percentages |
| **Capture** | Per-camera **COMPRESSED \| RAW** mode, SNAP photos, REC videos, live time-to-full estimate, safety margins + hard block |
| **RAW pipeline** | Onboard agent writes uncompressed chunks on the Pi; the RAW panel has per-file checkboxes, SELECT ALL and bulk TRANSFER (sha256-verified) / DELETE; Pi auto-stop guard |
| **Pre-flight** | Sensor health chips (GYRO/ACCEL/MAG + compass count/BARO/BATTERY/MOTOR/RC), pre-arm bit, 15-item Blue Robotics pre-dive checklist with ARM soft-gate |
| **Safety systems** | Leak latch, battery low/critical, water-temperature alerts, FAILSAFE status, link watchdog; alert strip + beep |
| **Vehicle ops** | Arm/disarm, mode switch, SURFACE, accelerometer/compass/level/baro calibration, thruster test + MOTOR_DETECT, failsafe editor with one-click defaults (4S 10 Ah), parameter browser |
| **Connection** | Official BlueRobotics network setup built in: one button sets the laptop tether adapter to **192.168.2.1** (Linux `nmcli` live, Windows `netsh` via UAC, macOS `networksetup`), with Verify (adapter + robot reachability) and manual steps as fallback — the robot is never modified |
| **Maps** | OSM map with offline tile cache (Indonesia pre-cache tool included), manual anchor input (COORDS…), anchor persistence across sessions, dead-reckoning track, NED plot |
| **UX** | 4 layouts (Pilot Focus / Mapping / Setup / Capture), global text scale, ink & ivory themes, English-only UI, session naming, wheel-guard (scroll over a slider/dropdown only acts after a click), single-instance guard |

## Hardware & software

| Piece | Model |
|---|---|
| ROV | BlueRobotics BlueROV2 (Navigator + Raspberry Pi 4, BlueOS) |
| Front camera | Blue Robotics Low-Light HD USB (RAW ≤ 640×480 over USB2) |
| Stereo camera | Arducam B0495C — 2× AR0234 global shutter (streams at 1280×720@30) |
| Battery | Tattu 4S 10000 mAh LiPo (25C) — failsafe defaults preset for this pack |
| Topside | Ubuntu 24.04 or Windows 10/11 laptop, tether via Fathom-X (BlueOS at `192.168.2.2`) |
| Controller | DualShock 4 or Xbox controller (USB) |

## Quick start

1. **Install** — run the Windows installer from the releases page (per-user, no
   admin needed; shows the End-User Terms and lets you choose the install folder).
2. **Network (official BlueRobotics flow)** — plug the tether, open
   **Connection ▸ Set up network**: the laptop adapter becomes `192.168.2.1`;
   press **Verify** (adapter + robot answer). No restart needed — press **Save**.
3. **Launch** — the console connects to `udpin:0.0.0.0:14550` and shows telemetry.
   The first SNAP/RECORD of each dive asks once: **Use default** (
   `Documents/DeepVertex`) or **Choose folder…**.
4. **Dive** — REC/SNAP per camera, watch the pre-flight chip row, arm and fly.

Windows note: if the camera tiles stay black while telemetry works, allow the
video ports once in an admin PowerShell (Windows blocks UDP by default):

```powershell
New-NetFirewallRule -DisplayName "DeepVertex Video UDP In" -Direction Inbound `
  -Protocol UDP -LocalPort 5600-5602 -Action Allow -Profile Any
```

## Where your files live

```
Documents/DeepVertex/                    <- created at install / first run
├─ logs/console.log                      <- application log (easy to find)
└─ <YYYYMMDD_HHMMSS>_<session-name>/     <- one folder per dive
    ├─ compressed/                       <- snap .png + recording .mkv
    ├─ raw/                              <- uncompressed chunks transferred from the Pi
    ├─ mavlink.tlog                      <- full-fidelity MAVLink log
    └─ sensors.csv                       <- 10 Hz sensor log (topside)
```

Settings (connection, gamepad presets) live per user next to the app data and
are never touched by updates or uninstall. Details:
[docs/data-and-paths.md](docs/data-and-paths.md).

## Downloads

Release page: <https://github.com/ZaydZaidan/deepvertex-console/releases/latest>

| Platform | Artifact |
|---|---|
| **Windows 10/11** | `DeepVertexMarineConsole-Setup-<version>.exe` — installer with End-User Terms, install-folder choice, Start Menu entry and uninstaller. Bundles `ffmpeg.exe`, so camera preview, snapshots and recording work out of the box. |

Only the final installer is distributed through the release page. The application
checks for updates on startup (silent when offline); opt out with `--no-update`
or `DV_NO_UPDATE=1`.

## Testing

The suite runs without hardware (own fake autopilot + GStreamer test pipelines):

```bash
cd gui_t1
.venv/bin/python ../tests/test_console.py     # 68 checks — PASS
.venv/bin/python ../tests/test_video.py
.venv/bin/python ../tests/test_raw.py
.venv/bin/python ../tests/probe_layout.py     # geometry/truncation scan, 4 layouts
.venv/bin/python ../tests/probe_scroll.py     # no-clip + scroll reachability scan
```

CI runs the same suite headlessly on every push
(see `.github/workflows/ci.yml`).

## Documentation

| Document | For |
|---|---|
| [docs/installation.md](docs/installation.md) | Topside install, tether & BlueOS setup, verification |
| [docs/quickstart.md](docs/quickstart.md) | Your first 15 minutes: pre-flight, arm, fly, record, transfer |
| [docs/user-guide.md](docs/user-guide.md) | Every panel, layout, alert and menu — the operator manual |
| [docs/gamepad.md](docs/gamepad.md) | Default mapping, remapping, named presets, safety blocking |
| [docs/files-and-storage.md](docs/files-and-storage.md) | Session layout, compressed vs RAW, storage guard |
| [docs/architecture.md](docs/architecture.md) | Modules, threads, MAVLink layer, agent protocol, theming |
| [docs/testing.md](docs/testing.md) | Test suite, probes, fake vehicle, agent test mode |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Field-proven fixes (including the Windows video firewall) |
| [docs/platform-parity.md](docs/platform-parity.md) | Windows/Linux feature audit |
| [CHANGELOG.md](CHANGELOG.md) | Full release history |

## Status & roadmap

**ROV mode is feature-complete and field-tested** on the real BlueROV2 over the
tether, on both Ubuntu and Windows: teleop, capture, RAW transfer, pre-flight,
failsafe and calibration flows. Next (2.x): autonomous operation (AUV mode + VIO)
on the Orin companion. Known limitations: [docs/roadmap.md](docs/roadmap.md).

## Links

| | |
|---|---|
| Website | [deepvertex.tech](https://deepvertex.tech) |
| Instagram | [@deepvertex](https://instagram.com/deepvertex) |
| LinkedIn | [DeepVertex](https://www.linkedin.com/company/deepvertex) |
| Business & licensing | founders@deepvertex.tech |

## License

**PolyForm Strict 1.0.0 — source-available.** You may read, run and use this
software for any **noncommercial** purpose. You may **not** distribute it or make
**changes / new works** based on it, and commercial use requires a **written
license** — contact [founders@deepvertex.tech](mailto:founders@deepvertex.tech).
DeepVertex names, logos and branding must never be removed or altered.
See [LICENSE](LICENSE); third-party components keep their own licenses
([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — PySide6/Qt under LGPL,
dynamically linked, unmodified; bundled ffmpeg is used as an unmodified
separate executable).
