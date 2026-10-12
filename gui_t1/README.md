# ROV Mission Console

Local dashboard (PyQt5) for BlueROV2 coral mapping missions: monitors the Stonefish
simulator, stereo VIO (VINS-Fusion), VIO trajectory vs ground truth, and
ArduSub/Pixhawk telemetry. **The GUI also launches its entire stack itself.**

```
bash "/home/zayd/Project BRIN VIO/gui/run_dashboard.sh"     # or: rovgui
```

The code lives in `gui/` in the `stonefish_bluerov2` repo; the local path
`/home/zayd/Project BRIN VIO/gui` is a symlink to it, so `rovgui` still works.

## What the GUI does when opened

1. cleans up leftover test processes (sim, relay, SITL, VINS, lawnmower);
2. starts the **simulator + relay** (`ros2 launch stonefish_bluerov2 bluerov2_sim_spec.launch.py`)
   → waits for `/bluerov2/odometry` to stream (>5 Hz);
3. starts **ArduSub SITL** (`scripts/start_ardusub.sh`, TCP 5760 + telemetry 5762)
   → waits for MAVLink packets;
4. starts **VINS-Fusion** (`ros2 launch vins_fusion_ros2 vins_bluerov2.launch.py`);
5. the **START AUTONOMOUS** button lights up once steps 2+3 are ready; pressing it runs
   the lawnmower controller (`ros2 run stonefish_bluerov2 bluerov2_lawnmower.py`).

Closing the GUI = stopping the mission and the entire stack (`--keep-stack` to let the stack keep running).

## Process control

| Element | Function |
|---|---|
| `START ALL` | start-all node starts every component in sequence |
| `STOP ALL` | stop the mission then all components (SIGINT to process group, SIGKILL if needed) |
| `▶ / ■` per component | start/stop a single component (with dependency check) |
| `START AUTONOMOUS` | mission toggle: on = lawnmower running, off = lawnmower stopped + RC neutralized |
| `START MAPPING MANUAL` | toggle a MANUAL mapping session (Oct 2, 2026): the pilot drives with a gamepad (deadman), while **capture relay + logger + guide node** run. The guide draws the **planned path (dashed) + area box** on the map. STOP = teleop neutralized + run closed like an autonomous mission (analysis + SFM). |
| `START MANUAL PILOT` | pilot only (old MANUAL mode): **no** capture, **no** guide |

## Auto-PID (Tuning menu)

The **Tuning ▸ Auto-PID** menu (Ctrl+T) tunes the REAL ArduSub parameters over MAVLink
(`rov_pid_autotune.py`): depth loop (`PSC_D_*`), heading/roll/pitch (`ATC_*`), based on
**disturbance** tests (force pulse then measure the response), not RC steps.

- **Options dialog**: APPLY (save the best candidate), auto ARM, iterations, amplitude, step
  duration, initial method (auto/SIMC/ZN/...), test method (disturbance/step), minimum test depth,
  **REAL ROV**, and **MAVLink host**.
- **REAL ROV (Oct 4, 2026)**: check REAL ROV for the real robot — the sim+SITL requirement is SKIPPED,
  automatic disturbance uses RC pulses (the 8-thruster pattern only exists in SITL), and the MAVLink
  host is pointed at the robot link (e.g. `udp:192.168.2.1:14550` / `serial:/dev/ttyACM0:115200`).
  Parameter snapshot is automatic; **Ctrl+C = parameters restored**. The snapshot can also be restored
  via the Tuning ▸ Restore parameters menu.
- **Response+error window** (`pid_view.py`) opens AUTOMATICALLY in tuning mode: each iteration
  is drawn as its own trace + error. Manual: `/usr/bin/python3 gui/pid_view.py`.
- Offscreen verification: `scripts/tools/uji_gui_tuning.py` (source ROS first).

## Panels

- **Stack · processes** — status + uptime for each component, start/stop buttons.
- **Stonefish · simulator view** — **panel intentionally left empty** (Sep 23, 2026): the
  `mirror` mode was removed, the system owner watches the Stonefish window directly.
  `reparent` mode (real embed) only when explicitly requested and risky.
- **VIO left / right** — 720p feed; **capture** (optional) 1920×1200 RGB for SfM.
- **Trajectory** — VIO trajectory vs ground truth + RMSE, path length, scale ratio.
  In the **MAP** tab, besides the GT trace (black) & VIO (red) there is a **guide line**
  from the mission node: the PLANNED path dashed + movement-direction arrows + area boundary box,
  complete with a `NEXT` marker (nearest waypoint). The guide appears **the moment the mission
  button is pressed** — the autonomous path is known before the robot moves (§Oct 2, 2026).
- **Flight / Gyro / Power** — mode, armed, attitude, depth, altitude AGL, heading,
  throttle, gyro, acceleration, battery.
- **VIO·sim status**, **ArduSub messages**, **Log stack**.

## Options

```
--embed auto|reparent|off          default auto = panel simulasi kosong (mirror dihapus)
--no-autostart                     do not auto-start the stack (start manually from the panel)
--autostart-mission                begitu stack siap, langsung toggle START AUTONOMOUS
--keep-stack                       jangan hentikan stack saat GUI ditutup
--log-dir DIR                      folder log proses (default /tmp/rov_dashboard_logs)
--capture-preview                  tambah tile capture full-res
--mavlink auto|tcp:127.0.0.1:5762  sumber telemetri (default auto)
--size WxH, --shot FILE, --shot-after N, --no-ros, --no-mav
```

## Important warnings

1. **Do not use `--embed reparent`** unless you know the risk: reparenting an
   X11 window once made the dashboard window cover the whole screen and lose the
   terminal. The default mode does not touch the simulator window at all — the
   simulator panel is left empty (`mirror` mode removed Sep 23, 2026).
2. Close the GUI through its window (or `q`/Ctrl-C in the launcher terminal) so the stack
   is stopped cleanly.
3. MAVLink link map (SERIAL0/TCP 5760 accepts only **one** client, and ArduSub SITL
   stops at `Waiting for connection ....` until that client arrives):
   - **5760** (SERIAL0) — dashboard (also unblocks the SITL boot)
   - **5762** (SERIAL2) — lawnmower controller (`mavlink_url:=tcp:127.0.0.1:5762`)
   - **14550** (SERIAL1/UDP) — QGC (the dashboard can use this as a fallback)
   The Stage B runner (without GUI) still uses the controller default at 5760.

## Files

| File | Contents |
|---|---|
| `rov_dashboard.py` | main window, panel layout, timers |
| `stackui.py` | STACK panel, START AUTONOMOUS button, sequential autostart |
| `procman.py` | process manager (SIGINT/SIGKILL group, logs, leftover-process cleanup) |
| `widgets.py` | custom QPainter widgets (panels, camera tiles, plots) |
| `theme.py` | neutral dark palette + amber accent |
| `rosiface.py` | ROS 2: VIO/capture images, odometry, ground truth |
| `maviface.py` | MAVLink via pymavlink (telemetry + RC neutralization) |
| `xembed.py` | simulator window search, WM check, detach |
| `pid_view.py` | realtime Auto-PID **response + error** window (opens automatically in tuning mode; also for the real robot) |
| `run_dashboard.sh` | launcher (sources ROS 2 + DDS profile 16 MB) |
