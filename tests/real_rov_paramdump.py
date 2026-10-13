#!/usr/bin/env python3
"""Dump firmware version + ALL parameters from the real ROV (read-only).

Run (console BEST stopped first — avoids the UDP socket race):.venv/bin/python../tests/real_rov_paramdump.py [--filter BATT] [--all]
"""
import argparse
import os
import time

os.environ.setdefault("MAVLINK20", "1")

from pymavlink import mavutil                                       # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter", default="BATT")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()

    m = mavutil.mavlink_connection("udpin:0.0.0.0:14550", source_system=255,
                                   source_component=190, dialect="ardupilotmega")
    hb = m.recv_match(type="HEARTBEAT", blocking=True, timeout=8)
    assert hb is not None, "tidak ada heartbeat"
    sysid = hb.get_srcSystem()
    m.target_system = sysid
    m.target_component = 1
    print("heartbeat sys%d | mode %s" % (sysid, mavutil.mode_string_v10(hb)))

    m.mav.command_long_send(sysid, 1, 512, 0, 148, 0, 0, 0, 0, 0, 0)  # REQ AUTOPILOT_VERSION
    t0 = time.time()
    while time.time() - t0 < 6:
        msg = m.recv_match(type="AUTOPILOT_VERSION", blocking=True, timeout=0.5)
        if msg:
            v = int(msg.flight_sw_version)
            print("firmware: %d.%d.%d board=%x vendor=%x product=%x"
                  % (v // (1 << 24), (v // (1 << 16)) % 256, (v // 256) % 256,
                     msg.board_version, msg.vendor_id, msg.product_id))
            break

    m.mav.param_request_list_send(sysid, 1)
    params = {}
    total = None
    t0 = time.time()
    while time.time() - t0 < 60:
        msg = m.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
        if msg is not None:
            pid = msg.param_id
            if isinstance(pid, bytes):
                pid = pid.decode("utf-8", "ignore")
            params[str(pid).rstrip("\x00")] = msg.param_value
            if msg.param_count:
                total = int(msg.param_count)
            if total and len(params) >= total:
                break
        elif total is None and time.time() - t0 > 8:
            break
    print("parameter: %d / %s" % (len(params), total))
    for k in sorted(params):
        if a.all or a.filter.upper() in k:
            print("   %-18s = %s" % (k, params[k]))


if __name__ == "__main__":
    main()
