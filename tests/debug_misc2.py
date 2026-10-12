#!/usr/bin/env python3
"""Debug 2 (12 Okt): signature RC override + perilaku tlog raw (flush)."""
import inspect
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test")
os.makedirs(SCRATCH, exist_ok=True)
STATS = os.path.join(SCRATCH, "dbg_c_stats.json")

from pymavlink import mavutil                 # noqa: E402

try:
    os.remove(STATS)
except OSError:
    pass
fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_vehicle.py"),
                         "--endpoint", "udpout:127.0.0.1:14593", "--secs", "30",
                         "--stats", STATS])
time.sleep(1.0)
mc = mavutil.mavlink_connection("udpin:0.0.0.0:14593", source_system=250,
                                source_component=190, dialect="ardupilotmega")
hb = mc.recv_match(type="HEARTBEAT", blocking=True, timeout=5)
print("C) heartbeat:", hb is not None)
print("C) signature rc_channels_override_send:")
print("   ", inspect.signature(mc.mav.rc_channels_override_send))

for n in (18, 8):
    try:
        mc.mav.rc_channels_override_send(1, 1, *([1500] * n))
        print("C) send%d: OK" % n)
    except Exception as e:                     # noqa: BLE001
        print("C) send%d: ERR %r" % (n, e))

time.sleep(1.2)
try:
    with open(STATS, encoding="utf-8") as f:
        st = json.load(f)
    print("C) fake rc count:", st.get("rc"))
except Exception as e:                         # noqa: BLE001
    print("C) stats error:", e)

# ---- tlog raw: kapan byte muncul? ----
tp = os.path.join(SCRATCH, "dbg_c.tlog")
if os.path.exists(tp):
    os.remove(tp)
mc.setup_logfile_raw(tp, "ab")
t0 = time.time()
while time.time() - t0 < 3:
    mc.recv_match(blocking=True, timeout=0.1)
print("C) tlog size (sebelum flush):", os.path.getsize(tp))
mc.logfile_raw.flush()
print("C) tlog size (setelah flush):", os.path.getsize(tp))
try:
    mc.close()
except Exception:                              # noqa: BLE001
    pass
print("C) tlog size (setelah close):", os.path.getsize(tp))
fake.terminate()
