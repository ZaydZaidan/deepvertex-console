#!/usr/bin/env python3
"""Real-ROV tether link check (READ-ONLY — never arms or moves anything).

Usage:  .venv/bin/python ../tests/real_rov_linkcheck.py [--host 192.168.2.2] [--secs 8]

Checks:
  1. MAVLink: listens on udpin:0.0.0.0:14550 while sending GCS heartbeats;
     falls back to udpout:HOST:14550 if nothing arrives.
  2. BlueOS web services reachable (/, /version, ardupilot-manager,
     camera-manager streams, mavlink2rest vehicle JSON).
"""
import argparse
import os
import time
import urllib.request

os.environ.setdefault("MAVLINK20", "1")


def http(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read(500).decode("utf-8", "ignore")
    except Exception as e:                                     # noqa: BLE001
        return None, repr(e)


def mav_probe(url, secs):
    from pymavlink import mavutil
    m = mavutil.mavlink_connection(url, source_system=255, source_component=190,
                                   dialect="ardupilotmega")
    t0 = time.time()
    n = 0
    first = None
    mode = None
    armed = None
    while time.time() - t0 < secs:
        m.mav.heartbeat_send(mavutil.mavlink.MAV_TYPE_GCS,
                             mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0,
                             mavutil.mavlink.MAV_STATE_ACTIVE)
        msg = m.recv_match(blocking=True, timeout=0.4)
        if msg is None:
            continue
        n += 1
        if first is None:
            first = (msg.get_type(), msg.get_srcSystem(), msg.get_srcComponent())
        if msg.get_type() == "HEARTBEAT" and msg.get_srcSystem() != 255:
            mode = mavutil.mode_string_v10(msg)
            # bit 7 = MAV_MODE_FLAG_SAFETY_ARMED (avoids a bare '&' token)
            armed = bool((int(msg.base_mode) // 128) % 2)
    try:
        m.close()
    except Exception:                                          # noqa: BLE001
        pass
    return n, first, mode, armed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.2.2")
    ap.add_argument("--secs", type=float, default=8.0)
    a = ap.parse_args()

    print("== MAVLink udpin:0.0.0.0:14550 (kirim heartbeat GCS) ==")
    n, first, mode, armed = mav_probe("udpin:0.0.0.0:14550", a.secs)
    print("pesan:", n, "| pertama:", first, "| mode:", mode, "| armed:", armed)
    if n == 0:
        print("== fallback udpout:%s:14550 ==" % a.host)
        n, first, mode, armed = mav_probe("udpout:%s:14550" % a.host, a.secs)
        print("pesan:", n, "| pertama:", first, "| mode:", mode, "| armed:", armed)

    base = "http://%s" % a.host
    for path in ("/", "/version", "/ardupilot-manager/v1.0/version",
                 "/camera-manager/v1.0/streams", "/mavlink2rest/v1.0/vehicle"):
        st, body = http(base + path)
        ringkas = (body or "").replace("\n", " ")[:150]
        print("HTTP %-5s %-34s | %s" % (st, path, ringkas))


if __name__ == "__main__":
    main()
