# Calibration protocol audit (vs. official sources)

Checked against:
- ArduPilot developer spec, *Sensor Calibration* MAVLink commands:
  https://ardupilot.org/dev/docs/mavlink-calibration.html (authoritative
  for command fields):
  - `MAV_CMD_PREFLIGHT_CALIBRATION` (241): param1 = 1 gyro; param3 = 1 baro
    ("Calibrate Pressure" — on ArduSub this IS the depth-sensor zero);
    param5 = 1 full 6-position accel; param5 = 2 board-level/trim
  - full 6-position accel is ENTERED by 241(param5=1) and then driven per
    position with `MAV_CMD_ACCELCAL_VEHICLE_POS` (42429)
  - compass calibrations: `MAV_CMD_DO_CANCEL_MAG_CAL` / `242/42424`-style START
    with autosave flags, progress via `MAG_CAL_PROGRESS`, status via
    `MAG_CAL_REPORT`
- ArduSub docs: accelerometer calibration is mandatory, cannot run while
  armed; positions = level, right, left, nose-down, nose-up, on-its-back;
  *Calibrate Level* corrects ≤10° pitch/roll only:
  https://ardupilot.org/sub/docs/common-accelerometer-calibration.html
- Compass onboard calibration: rotate through all axes until completion;
  **reboot the autopilot before arming** is expected after success:
  https://ardupilot.org/sub/docs/common-compass-calibration-in-mission-planner.html
- Blue Robotics BlueOS/QGC workflow for BlueROV2: accelerometer full cal,
  compass full (onboard) cal, "Calibrate Pressure", motor directions:
  https://bluerobotics.com/learn/bluerov2-software-setup/

| Console feature | What DeepVertex does | Official protocol | Parity |
|---|---|---|---|
| GYRO | `241 p1=1` (tag gyro-cal) | 241 p1=1 | ✓ |
| ACCEL (6-POS) | wizard sends `241 p5=1` **enter** command, then `42429` per position 1..6 (level/right/left/nose-down/nose-up/on-back) | 241 p5=1 + 42429 per position; still at each position; mandatory, not while armed | ✓ (enter-command added 15 Oct; dialog already enforced still/hold still wording) |
| LEVEL | `241 p5=2` | 241 p5=2 board-level/trim; corrects pitch/roll ≤10°, not yaw | ✓ (dialog notes the ≤10° caveat) |
| COMPASS | cancel (flush) then `DO_START_MAG_CAL` p1=0 all compasses, p2=1 retry, p3=1 autosave; live `MAG_CAL_PROGRESS` + `MAG_CAL_REPORT` (incl. rx autosave flag → explicit `DO_ACCEPT_MAG_CAL`), completion notes the reboot-before-arming advice | same command set; progress streaming; reboot advised | ✓ |
| **DEPTH (BARO)** | NEW: explicit "DEPTH (BARO)" dialog = `241 p3=1` (was hiding as BARO); guidance "at the surface before every dive" | QGC "Calibrate Pressure" = 241 p3; ArduSub depth = the water pressure sensor, zeroed at the surface | ✓ (per-owner naming added 15 Oct) |
| MOTOR TEST | `DO_MOTOR_TEST` per thruster, repeated ≥2× with p6=board order; MOTOR DETECT mode for in-water spin direction | QGC/ArduSub motor table + mission-planner-like test flow | ✓ |
| FAILSAFE & BATTERY params | writes FS_*/BATT_* (BlueROV2 + Tattu 4S 10 Ah defaults, still user-editable) | QGC Safety page equivalent | ✓ |
| PARAMETERS | full list browser + save/load JSON into the in-app `saved/` folder | QGC parameters tool | ✓ (extra: JSON archiving) |

Battery display note (owner question 15 Oct): ArduPilot's `battery_remaining`
is **energy consumed since power-on / `BATT_CAPACITY`** for a current-sensing
monitor (BATT_CAPACITY = 10000 mAh → 7.5 kJ ≈ 134 mAh ≈ 98–99% just idling).
Voltage is the honest state-of-charge signal: 15.47 V ≈ 3.87 V/cell ≈ 55–65%
SoC. The console now shows BOTH: Ardu remaining % **and** a voltage-based
LiPo estimate (`_lipo_pct`, piecewise anchor curve).

Raw evidence pages were captured with the REA skill (web capture) on 15 Oct;
sources listed above are the canonical official pages.
