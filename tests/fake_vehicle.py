#!/usr/bin/env python3
"""Kendaraan ArduSub palsu (telemetri MAVLink) untuk uji konsol tanpa hardware.

Kirim: HEARTBEAT(1 Hz), ATTITUDE(10 Hz), VFR_HUD(4 Hz), SCALED_PRESSURE2(4 Hz),
SYS_STATUS(1 Hz). Tanggapi: PARAM_REQUEST_LIST (balas 12 param), PARAM_SET (echo),
COMMAND_LONG (ACK ACCEPTED). Hitung MANUAL_CONTROL masuk.
--stats PATH: tulis statistik JSON tiap detik (dibaca uji).
"""
import argparse
import json
import math
import time

from pymavlink import mavutil

PARAMS = [("FS_GCS_ENABLE", 0), ("FS_GCS_TIMEOUT", 5), ("FS_PILOT_INPUT", 0),
          ("FS_PILOT_TIMEOUT", 3), ("FS_LEAK_ENABLE", 2), ("BATT_LOW_VOLT", 13.2),
          ("BATT_CRIT_VOLT", 12.5), ("BATT_LOW_MAH", 0), ("BATT_CRIT_MAH", 0),
          ("BATT_FS_LOW_ACT", 0), ("BATT_FS_CRT_ACT", 0), ("ARMING_CHECK", 1)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="udpout:127.0.0.1:14550")
    ap.add_argument("--secs", type=float, default=30.0)
    ap.add_argument("--stats", default=None)
    a = ap.parse_args()
    m = mavutil.mavlink_connection(a.endpoint, source_system=1, source_component=1,
                                   dialect="ardupilotmega")
    stats = {"manual": 0, "param_list_req": 0, "param_set": [], "cmd": []}
    t0 = time.time()
    t_stats = 0.0
    i = 0
    while time.time() - t0 < a.secs:
        t = time.time() - t0
        ms = int(t * 1000)
        m.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_SUBMARINE,
                             mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA,
                             81, 0, mavutil.mavlink.MAV_STATE_ACTIVE)
        m.mav.attitude_send(ms, 0.05 * math.sin(t * 0.7), 0.03 * math.cos(t * 0.9),
                            0.6 * math.sin(t * 0.3), 0.0, 0.0, 0.0)
        m.mav.vfr_hud_send(0.4, 0.0, int((t * 12) % 360), 30, 5.0, 0.0)
        # press_diff ~ 502 hPa → depth ≈ 5 m (temp 21.00 °C)
        m.mav.scaled_pressure2_send(ms, 1013.25, 502.0, 2100)
        m.mav.sys_status_send(0, 0, 0, 500, 14800, 150, 75, 0, 0, 0, 0, 0, 0)
        # nilai sub ala ArduSub (NAMED_VALUE_FLOAT) + servo outputs (PWM ch16 tilt)
        for nm, val in ((b"CamTilt", 0.62), (b"Lights1", 0.35),
                        (b"PilotGain", 0.50), (b"TetherTrn", 0.0)):
            try:
                m.mav.named_value_float_send(ms, nm, val)
            except Exception:                      # noqa: BLE001
                pass
        # servo outputs: signature beda antar versi pymavlink -> coba berlapis
        for args in ((ms, 0, 1500, 1500, 1500, 1500, 1500, 1500, 0, 0, 1500, 1500, 0, 0, 0, 0, 1680),
                     (ms, 0, 1500, 1500, 1500, 1500, 1500, 1500, 0, 0),
                     (ms,)):
            try:
                m.mav.servo_output_raw_send(*args)
                break
            except Exception:                      # noqa: BLE001
                continue

        while True:
            msg = m.recv_match(blocking=False)
            if msg is None:
                break
            tp = msg.get_type()
            if tp == "PARAM_REQUEST_LIST":
                stats["param_list_req"] += 1
                for idx, (nama, val) in enumerate(PARAMS):
                    m.mav.param_value_send(nama.encode("utf-8"), float(val),
                                           mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
                                           len(PARAMS), idx)
                    time.sleep(0.004)
            elif tp == "PARAM_SET":
                nm = msg.param_id
                if isinstance(nm, bytes):
                    nm = nm.decode("utf-8", "ignore")
                nm = str(nm).rstrip("\x00")
                stats["param_set"].append([nm, float(msg.param_value)])
                m.mav.param_value_send(nm.encode("utf-8"), float(msg.param_value),
                                       mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
                                       len(PARAMS), 0)
            elif tp == "COMMAND_LONG":
                stats["cmd"].append([int(msg.command), float(msg.param1)])
                m.mav.command_ack_send(int(msg.command), 0)      # ACCEPTED
            elif tp == "MANUAL_CONTROL":
                stats["manual"] += 1

        if a.stats and t - t_stats > 1.0:
            t_stats = t
            try:
                with open(a.stats, "w", encoding="utf-8") as f:
                    json.dump(stats, f)
            except OSError:
                pass
        i += 1
        time.sleep(0.1)
    if a.stats:
        try:
            with open(a.stats, "w", encoding="utf-8") as f:
                json.dump(stats, f)
        except OSError:
            pass
    print("fake_vehicle: selesai (%d iterasi)" % i)


if __name__ == "__main__":
    main()
