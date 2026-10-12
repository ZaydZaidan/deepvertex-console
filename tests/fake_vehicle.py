#!/usr/bin/env python3
"""Kendaraan ArduSub palsu (telemetri MAVLink) untuk uji konsol tanpa hardware.

Kirim HEARTBEAT(1 Hz), ATTITUDE(10 Hz), VFR_HUD(4 Hz), SCALED_PRESSURE2(4 Hz),
SYS_STATUS(1 Hz) ke endpoint GCS.
"""
import argparse
import math
import time

from pymavlink import mavutil


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="udpout:127.0.0.1:14550")
    ap.add_argument("--secs", type=float, default=30.0)
    a = ap.parse_args()
    m = mavutil.mavlink_connection(a.endpoint, source_system=1, source_component=1,
                                   dialect="ardupilotmega")
    t0 = time.time()
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
        i += 1
        time.sleep(0.1)
    print("fake_vehicle: selesai (%d iterasi)" % i)


if __name__ == "__main__":
    main()
