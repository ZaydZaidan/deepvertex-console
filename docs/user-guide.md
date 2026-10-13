# Operator manual

The console is one window with four **layouts** (top bar) around a shared header,
status bar, and alert strip. Everything is designed so that a pilot never has to
leave the current layout while flying.

## The header

- **Logo** (DeepVertex) — no clutter.
- **Layout switcher**: *Pilot Focus · Mapping · Setup · Capture* (instant, no state loss).
- **Status pills**: MODE (current flight mode), ARM (armed/disarmed), PAD (gamepad
  readiness) — always visible.
- **Chips**: LINK (MAVLink), REC (any camera recording), FS (failsafe text active),
  EKF (estimator health).

### Alert strip

Appears only when something is wrong; red = critical, amber = warning, with a beep on
new critical events. Sources: **leak** (latched until acknowledged by a fresh healthy
status), **battery** (low/critical V and % from your BATT_* params), **water
temperature** (editable warn/crit thresholds), **failsafe** status text, **link loss**,
and **storage** (low → amber; full → red + REC/SNAP blocked).

## Layouts

### Pilot Focus
Everything needed to fly: FLIGHT dials (attitude + compass), VEHICLE (mode, arm,
lights, tilt with %, gain, MAX SPEED), POWER, INSTRUMENTS (QGC-style sub values),
PRE-FLIGHT CHECK + checklist, camera + map, CAPTURE, TELEOP bars + pad status.

### Mapping
Map-first: OSM tiles (cached for offline), anchor placement, ROV track (dead-reckoning
estimate — armed-gated), NED plot with survey area and planned lanes, capture rail.

### Setup
The guided **SETUP GUIDE** (7 numbered steps) + calibration panel (GYRO / ACCEL 6-POS /
COMPASS / LEVEL / BARO / PARAMS), thruster tools, FAILSAFE & BATTERY with one-click
defaults, and the parameter browser.

### Capture
Larger capture rail with recording/snapshot per camera and RAW transfer tools
prominent.

## Key panels

### VEHICLE (PILOT)
- **Mode dropdown + SET** — MANUAL, STABILIZE, ALT_HOLD (depth-hold), SURFACE.
- **ARM / DISARM / SURFACE** — arm has a soft gate if the checklist is incomplete.
- **LIGHTS %** and **TILT %** — slider **and** type-in box (same code path);
  TILT **RESET** returns the camera to 50% and re-centers automatically at session start.
- **GAIN −/+** — pilot gain (writes `JS_GAIN_DEFAULT`).
- **MAX SPEED** — caps all joystick output: 25% / 50% / 75% / 100%.

### CAPTURE
Per camera (FRONT / STEREO L / STEREO R): a **COMPRESSED | RAW** mode selector,
record toggle, snapshot. Below: session folder, `save:` legend, disk free,
**estimate** of remaining recording time and snap count, storage-guard state.

### RAW CAPTURE (onboard)
Controls for the Pi agent: RAW L / RAW R / L+R / **RAW F**(ront), STOP / STOP F,
chunk size, REFRESH, **TRANSFER ►** (with sha256 verify), **DELETE ✓** (only files
already verified on the laptop). Shows Pi free space, auto-stop margin, and a live
`~N min RAW left` estimate while recording.

### TELEOP (GAMEPAD)
Labeled bars for FWD/LAT/DEPTH/YAW/PAD-X/PAD-Y, pad name + dead-man state, and the
**PAD MAPPING…** button.

### TELEOP status line
`pad: DS4 | deadman HELD/RELEASED | tilt 1500 µs` — full names live in the tooltip.

### FAILSAFE & BATTERY
Every FS_/BATT_ parameter that matters, with units and safety guidance in tooltips.
**APPLY RECOMMENDED (4S 10000 mAh)** writes the proven defaults for this ROV. Rows
stay editable. SAVE FILE / LOAD FILE use the in-app `saved/` folder.

### PARAMETERS
Filter box (e.g. `FS_`, `BATT_`, `RC`), RELOAD (also automatic once per connection),
SET… for a single parameter, and **SAVE… / LOAD…** for full dumps in `saved/`
(`params_<time>.json`; LOAD sends everything as PARAM_SET).

### PRE-FLIGHT CHECK
Live chips (LINK/MODE/ARM/BATT/LEAK/PAD/CAM/PRE-ARM + the SENSORS row), status line,
and the 15-item pre-dive checklist (saved per session).

### MAP / MISSION MAP
OSM tiles with offline cache; **PLACE ANCHOR** pins the local origin; **FOLLOW ROV**
re-centers; **RESET EST.** zeroes the dead-reckoned track. Marker legend: anchor dot,
ROV arrow (visible on top when at the anchor), REC/SNAP markers along the track.

## Menus

- **View ▸ Appearance** — theme (ink/ivory/abyss) and text size (Normal 100% / Large
  120% / Extra 140%). Everything scales, including dialogs.
- **Vehicle** — Download parameters, **Gamepad mapping…**, pre-dive
  checklist viewer.
- **File** — snapshot the window, quit.

## Window title & app icon

The taskbar/dock shows **"DeepVertex Marine Technology Console"** with the DeepVertex
mono icon. Desktop and application launchers are created by the field setup
(`~/.local/share/applications/deepvertex-console.desktop`).
