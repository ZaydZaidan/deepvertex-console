# DeepVertex — Underwater Drone GUI Installer (Stage 1 · ROV mode)

Topside software package for the BlueROV2 coral-mapping delivery, Phase 1 (ROV mode):
a QGroundControl-style mission console (PySide6) plus capture and onboard RAW support.

**PRIVATE repo — closed-source.** Distribution artifacts for the client (binary-only +
license) are prepared separately during packaging; the robot itself stays on stock
BlueOS (its only custom component, the RAW recorder, is deployed as a BlueOS extension).

## What's inside

| Folder | Contents |
|---|---|
| `gui_t1/` | The T1 console ("ROV Mission Console", PySide6) — live 3-stream view, full MAVLink telemetry HUD, teleop, calibration / motor test / failsafe / parameter tools, OSM map, compressed capture. Folder overview: `gui_t1/README.md`; deep working notes (run, recipes, tests): `gui_t1/README_T1.md`. |
| `onboard/` | `raw_agent.py` — onboard RAW recorder (production on the Pi via the BlueOS extension scaffold in `onboard/extension/`). |
| `tests/` | Hardware-free test suite: `test_video.py`, `test_raw.py`, `test_console.py` (**all checks PASS** — includes preset-stress, gamepad-page, pad REC/PHOTO and file-flow checks; uses `fake_vehicle.py`), `probe_layout.py` (real-DPI layout scan for ALL presets), `probe_scroll.py`, `probe_padmap.py`, `debug_misc*.py`. |
| `install/` | `install_topside.sh` — topside installer. |
| `migrate_pyqt5_to_pyside6.py` | One-shot binding migration script (58 replacements). |

## Where files go (one folder per dive)

Every session creates a clearly split folder — compressed media and RAW archives never mix:

    ~/DeepVertex-ROV-Captures/<YYYYMMDD_HHMMSS>_dive/
        compressed/     .mkv video + .png snapshots from the live BlueOS streams
        raw/            unmcompressed chunks pulled from the Pi (RAW transfer)
        mavlink.tlog    full MAVLink telemetry log
        session_start.json / session_end.json

Per camera you choose the REC mode in the CAPTURE panel: **COMPRESSED** (video via the
BlueOS stream) or **RAW** (uncompressed via the onboard agent; files live on the Pi
until you press TRANSFER ► in the RAW CAPTURE panel, which verifies sha256 and drops
them into this session's `raw/`). Cameras: **front = Blue Robotics Low-Light HD USB**
(RAW limited to ≤640×480 on USB2) and **stereo = Arducam B0495C** (AR0234 global
shutter, native YUY2; recommended 1280×720@30 for streaming). SNAPSHOT is always a
quick .png into `compressed/`.

## Status (13 Oct 2026)

Console **v2.3** — all tests PASS without hardware. Key capabilities:

- 3 camera streams at once (front + stereo L/R) from the BlueOS camera manager;
- MAVLink 2 telemetry: attitude + compass dials, depth/temp, battery, QGC-style
  INSTRUMENTS (Cam Tilt / Lights / Pilot Gain / Tether), SYS_STATUS pre-arm bit, link
  watchdog + auto-reconnect, per-session telemetry tlog;
- teleop with dead-man (MANUAL_CONTROL 20 Hz; RC-override keep-alive for camera tilt &
  lights; pilot gain via `JS_GAIN_DEFAULT`);
- vehicle ops à la QGC: arm/disarm (+ soft-gate against the pre-dive checklist), mode
  change incl. SURFACE, gyro/baro/level calibration, 6-position accelerometer wizard,
  compass calibration (QGC sequence), ArduSub-compliant motor test, failsafe & battery
  panel (`ARMING_SKIPCHK` era), parameter browser;
- OSM map à la rovgui + NED plot with a dead-reckoning position estimate (true position:
  Stage 2 / VIO), capture markers; four layout presets (Pilot Focus / Mapping / Setup /
  Capture);
- capture: compressed recording + snapshots on the topside; RAW via the onboard agent;
- AUTONOMOUS / VIO = *coming soon* (Stage 2, AUV mode).

## Quick start

    cd gui_t1
    bash demo_local.sh                 # full display demo, no hardware
    bash run_console_real.sh           # against a real ROV (MAVLink 14550, streams 5600+)

## Notes

- Python venv lives in `gui_t1/.venv` (PySide6 6.12 + system site packages for
  GStreamer / pymavlink).
- `.mkv` recording needs the `h264parse` plugin (`gstreamer1.0-plugins-bad`); a
  sudo-free bundle can live in `gui_t1/plugins/` and is loaded automatically.
- `libxcb-cursor0` is required for X11 (bundled sudo-free in `gui_t1/tools/xcb/`).
- The `-SBC-Installer` repo is parked (Stage 2; the Stage-1 raw-extension path covers
  the robot side).
- All documents in this project are written in English.
