#!/usr/bin/env python3
"""Battery setup for the real ROV (4S LiPo Tattu 10000 mAh).

Reads current BATT_* params, sets capacity + failsafe thresholds (voltage & mAh),
reads back and waits for SYS_STATUS.battery_remaining to become valid.
Run: .venv/bin/python ../tests/real_rov_battery_setup.py
"""
import os
import time

os.environ.setdefault("MAVLINK20", "1")

from pymavlink import mavutil                                       # noqa: E402

READ = ["BATT_MONITOR", "BATT_CAPACITY", "BATT_LOW_VOLT", "BATT_CRT_VOLT",
        "BATT_LOW_MAH", "BATT_CRT_MAH", "BATT_FS_LOW_ACT", "BATT_FS_CRT_ACT",
        "BATT_ARM_VOLT", "BATT_ARM_MAH"]
SET = {"BATT_CAPACITY": 10000, "BATT_LOW_VOLT": 14.0, "BATT_CRT_VOLT": 13.2,
       "BATT_LOW_MAH": 2000, "BATT_CRT_MAH": 1000}


def main():
    m = mavutil.mavlink_connection("udpin:0.0.0.0:14550", source_system=255,
                                   source_component=190, dialect="ardupilotmega")
    hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=8)
    assert hb is not None, "tidak ada heartbeat"
    sysid = hb.get_srcSystem()
    m.target_system = sysid
    m.target_component = 1

    def baca(nm, timeout=2.0):
        m.mav.param_request_read_send(sysid, 1, nm.encode("utf-8"), -1)
        t0 = time.time()
        while time.time() - t0 < timeout:
            msg = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.4)
            if msg is None:
                continue
            pid = msg.param_id
            if isinstance(pid, bytes):
                pid = pid.decode("utf-8", "ignore")
            if str(pid).rstrip("\x00") == nm:
                return msg.param_value
        return None

    def tulis(nm, val):
        m.mav.param_set_send(sysid, 1, nm.encode("utf-8"), float(val),
                             mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
        t0 = time.time()
        while time.time() - t0 < 2.0:                    # tunggu echo PARAM_VALUE
            msg = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.4)
            if msg is None:
                continue
            pid = msg.param_id
            if isinstance(pid, bytes):
                pid = pid.decode("utf-8", "ignore")
            if str(pid).rstrip("\x00") == nm:
                return msg.param_value
        return None

    print("== SEBELUM ==")
    for nm in READ:
        print("  %-16s = %s" % (nm, baca(nm)))

    print("== TULIS ==")
    for nm, val in SET.items():
        hasil = tulis(nm, val)
        print("  %-16s -> %s (echo: %s)" % (nm, val, hasil))

    print("== SESUDAH ==")
    for nm in READ:
        print("  %-16s = %s" % (nm, baca(nm)))

    print("== tunggu SYS_STATUS.battery_remaining valid ==")
    t0 = time.time()
    terakhir = None
    while time.time() - t0 < 12:
        msg = m.recv_match(type="SYS_STATUS", blocking=True, timeout=0.5)
        if msg:
            terakhir = (msg.voltage_battery / 1000.0, msg.current_battery / 100.0,
                        int(msg.battery_remaining))
            if terakhir[2] >= 0:
                break
    print("  volt=%.2f V | arus=%.2f A | sisa=%s%%" % terakhir if terakhir else "  (tidak ada)")


if __name__ == "__main__":
    main()
