# gui_t1 — T1 ROV Mission Console (+ simulator GUI originals)

This folder is the working root of the **Stage 1 (ROV mode) console** for the real
BlueROV2 (BlueOS), plus the original simulator GUI files it was derived from.

## T1 console (primary)

`rov_console_real.py` — the console for the real ROV. Design & widgets mirror the
current simulator GUI (ROV Mission Console incl. the 28 Sep 2026 redesign); the binding
has been migrated to **PySide6**.

Capabilities (Oct 12, 2026 — console v2.2 + ArduSub/QGC tech-spec fixes):

- 3 simultaneous camera streams (front + stereo L/R) from the BlueOS camera manager;
- MAVLink 2 telemetry: attitude + compass dials, depth/temp, battery, QGC-style
  INSTRUMENTS (Cam Tilt / Lights / Pilot Gain / Tether), SYS_STATUS pre-arm bit, link
  watchdog + auto-reconnect, per-session telemetry tlog;
- teleop (DS4 via evdev): dead-man (L1) + MANUAL_CONTROL at 20 Hz; camera-tilt & lights
  via RC override with keep-alive; pilot gain via `JS_GAIN_DEFAULT`;
- vehicle ops à la QGC: arm/disarm + ARM soft-gate against the pre-dive checklist, mode
  change (incl. SURFACE), gyro/baro/level calibration, 6-position accelerometer wizard,
  compass calibration (QGC sequence), ArduSub-compliant motor test, failsafe & battery
  panel, parameter browser;
- OSM map à la rovgui + NED plot with a dead-reckoning position estimate (true position
  comes with Stage 2 / VIO) and capture markers;
- four layout presets: Pilot Focus / Mapping / Setup / Capture;
- capture: compressed recording + snapshots on the topside; RAW via the onboard agent;
- AUTONOMOUS / VIO = *coming soon* (Stage 2, AUV mode).

Run instructions, dependencies, test suite and the "do not change without re-testing"
recipes are in **`README_T1.md`** (working notes).

### T1 console files

| File | Contents |
|---|---|
| `rov_console_real.py` | T1 console: main window, panels, wiring, capture/RAW/UAV presets |
| `video.py` | GStreamer display / relay / recording / snapshot |
| `rawcap.py` | RAW agent HTTP client + panel |
| `vehicleops.py` | calibration, motor test, failsafe, parameter browser, pre-flight |
| `gamepad.py` | DS4 evdev teleop (dead-man + MANUAL_CONTROL) |
| `peta_misi.py` | NED plot (survey area, lanes, trail, markers) |
| `maviface.py` | MAVLink bridge (MAVLink 2, tlog, params, commands) |
| `ui_common.py`, `theme.py`, `widgets.py` | shared layout helpers, theme, custom widgets (incl. the compass) |
| `peta_panel.py`, `peta_geo.py`, `peta_penyedia.py` | OSM map widget (à la rovgui) + geo/tile helpers |
| `demo_local.sh`, `run_console_real.sh` | display demo / real-ROV launchers |
| `fonts/`, `ikon/`, `plugins/`, `tools/` | theme assets; bundled GStreamer plugin; bundled libxcb-cursor (sudo-free) |
| `README_T1.md` | working notes: run, dependencies, tests, key recipes |

## Simulator GUI originals (design base)

Files kept from the simulator console; they are the visual/behavioural reference for the
T1 console. The full sim stack (Stonefish + ArduSub SITL + VINS-Fusion) is NOT part of
this repo, so they are not runnable standalone from here.

| File | Contents |
|---|---|
| `rov_dashboard.py` | sim main window (design reference) |
| `stackui.py`, `procman.py` | STACK panel + sim process manager |
| `rosiface.py` | ROS 2 interface (sim only) |
| `xembed.py` | simulator window embedding utilities (sim only) |
| `pid_view.py` | Auto-PID response + error window (sim + real robot) |
| `dem3d_plot.py`, `dem3d_viewer.py`, `estimasi_misi.py` | sim extras (not part of T1) |
| `run_dashboard.sh` | sim GUI launcher (sources ROS 2; not used by T1) |

Warnings carried over from the sim GUI: never use `--embed reparent` (X11 reparenting
can cover the whole screen), and close the GUI through its window so the stack stops
cleanly.
