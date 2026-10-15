# Changelog

All notable changes to the DeepVertex Marine Technology Console (ROV mode).
This project follows [Keep a Changelog](https://keepachangelog.com/) and SemVer for
the application package (`MAJOR.MINOR.PATCH` — autonomy (next) features will ship as 2.x).

## [1.0.1] — 2026-10-15

### Added
- **Chrome-style organize windows**: press & drag any panel header — cyan snap
  suggestions appear for **left rail / right rail / above / below** (Chrome
  split feel incl. reordering inside a rail); dropping a panel OUTSIDE the
  console pops it out as a free-floating window (drag it back to re-dock);
  ESC cancels a drag; **Custom** segment in the LAYOUT bar + View ▸
  *Organize windows* (Apply / Save as Custom / Forget) — the Custom layout is
  stored in `gui_t1/saved/layout_custom.json` and becomes the startup layout.
- Snap-engine regression tests [36]–[40] (rail snap + anti-stuck, mid
  above/below insert, float round-trip, custom persistence, ESC + "asal"
  return); suite now 40 checks, all headless (incl. raw-agent tests).
- README assets re-taken from the REAL desktop (X11 window capture), so the
  images match the actual on-screen rendering pixel-for-pixel.

## [1.0.0] — 2026-10-14

First field-complete release of the ROV mode console, verified against the real
BlueROV2 over the Fathom-X tether.

### Added

- Three simultaneous camera streams (front + stereo L/R) with 16:9 tiles, per-tile
  health and recording state.
- Full MAVLink 2 telemetry: attitude/compass dials, depth/temperature, battery
  V/A/%, QGC-style sub instruments, per-session `mavlink.tlog`.
- Gamepad teleop (DualShock 4 via evdev): dead-man enforced, MAX SPEED caps,
  depth-hold, camera tilt/lights with % readback, keep-alive RC overrides.
- **Per-camera COMPRESSED | RAW recording.** RAW runs through the onboard agent
  (BlueOS extension) and is pulled to the topside with sha256 verification.
- **GAMEPAD MAPPING page**: painted DS4 diagram, live button/stick feedback,
  one-click remap, named presets, and full output blocking while the page is open.
- Pad actions: □ toggles recording, ○ takes a photo — same code path as the GUI.
- **Storage guard**: live time-to-full estimates (topside EMA rate + Pi RAW rate),
  warn/full margins, red alert and hard REC/SNAP block below the safe limit.
- **Per-sensor pre-flight**: GYRO/ACCEL/MAG(+compass count)/BARO/BATTERY/MOTOR/RC
  chips from SYS_STATUS present+healthy bitmasks; failing sensors are named.
- Beginner SETUP GUIDE (7 steps), one-click failsafe defaults for BlueROV2 +
  Tattu 4S 10000 mAh, editable water-temperature alerts with beep.
- Session naming (startup dialog, `--nama-sesi`, live rename), file layout
  `compressed/` vs `raw/`, in-app `saved/` for settings and parameter dumps.
- Default 120% text scale, ink/ivory/abyss themes, English-only UI, DS4-shaped
  mapping diagram, real chevron assets for spin/combo controls.
- Test suite (30 checks) + geometry/scroll/pad probes, all hardware-free; CI.

### Fixed (highlights from the field-testing gauntlet)

- Stuck windows when switching layouts quickly; PAD MAPPING crash on open.
- Mode/ARM/depth flicker from BlueOS companion heartbeat; MAG false-FAULT before
  parameter download; spin/combo arrows rendering as blank squares.
- Camera tiles aspect drift; storage panel clipping in the right rail.

## [0.x] — 2026-09 … 2026-10 (pre-release, summary)

Bootstrap: PyQt5 → PySide6 migration, video pipeline (GStreamer), MAVLink layer,
calibration/motor-test/failsafe tooling, OSM map, session folders, RAW agent v1
(stereo), themes and the layout system. Kept for history in `progress/`.
