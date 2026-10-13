# Quick start — your first dive in 15 minutes

Assumes the topside install is done ([installation.md](installation.md)) and the ROV
is on the bench or in the water with the tether connected.

## 1. Start the console

```bash
deepvertexrov          # or ./gui_t1/run_console_real.sh
```

- The **Session name** dialog appears — type the dive name (e.g. `Coral Survey A`).
  Cancel = "dive". The folder becomes `~/DeepVertex-ROV-Captures/<date>_<time>_<name>/`.
- Wait for: **LINK ok**, camera tiles showing video, **PAD ready**.

## 2. Pre-flight (Setup layout — first time on a new ROV)

Open the **Setup** layout and follow the **SETUP GUIDE** steps 1–7 in order
(accelerometer → compass → level → baro → thruster check → failsafe defaults →
gamepad). One click per step; each dialog explains what to do.

For every later dive, only step 4 (barometer) is recommended, plus the
**PRE-FLIGHT CHECK** panel:

- Green chips: LINK / MODE / ARM / BATT / LEAK / PAD / CAM / PRE-ARM
- **SENSORS (robot):** GYRO · ACCEL · MAG (n compass) · BARO · BATTERY · MOTOR · RC IN —
  a broken sensor is named right there (`FAULT` / `NOT FOUND`).
- Tick the **15-item Blue Robotics pre-dive checklist**. Arming without it triggers a
  confirmation gate on purpose.

## 3. Arm and fly

- Modes: use the mode dropdown (MANUAL → ALT_HOLD for depth-hold) or the pad's
  SHARE button to cycle.
- **ARM** button (or pad ×). `SURFACE` is the emergency bring-up command.
- Teleop: hold **L1** (dead-man) — left stick moves, right stick turns/ascends,
  D-PAD tilts the camera, **L2 hold** = depth hold, **□** toggles recording,
  **○** takes a photo. Full table: [gamepad.md](gamepad.md).
- Keep an eye on the **alert strip** under the header — it only shows up when
  something needs you (leak / battery / temperature / link / storage).

## 4. Record & capture

In **Pilot Focus**, the right rail CAPTURE panel is your recorder:

| Row | What REC does |
|---|---|
| COMPRESSED | records `.mkv` video from the live stream into `compressed/` |
| RAW | tells the onboard agent to write uncompressed chunks on the Pi (`raw/` after transfer) |

- **SNAP** always saves a `.png` into `compressed/`.
- The `estimate:` line shows **~N min of recording left** at the current write rate;
  the red margin blocks REC/SNAP before the disk fills (`docs/files-and-storage.md`).

## 5. After the dive

- **TRANSFER RAW** (RAW CAPTURE panel) pulls Pi chunks into the session's `raw/`
  with sha256 verification, then **DELETE ✓** frees the Pi.
- Close the console — the session is self-contained: video, photos, RAW, `mavlink.tlog`.
- Need a different name? **SESSION NAME…** renames the folder live; open-files stay valid.
