#!/usr/bin/env python3
"""Debug 3 (12 Okt): tap tlog di level recv + cek MAVLINK20."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test")
os.makedirs(SCRATCH, exist_ok=True)

os.environ["MAVLINK20"] = "1"
from pymavlink import mavutil                 # noqa: E402
import inspect                                 # noqa: E402

print("D) dialect file:", mavutil.mavlink.__file__)
fn = mavutil.mavlink.MAVLink.rc_channels_override_send
print("D) sig rc(MAVLINK20=1):")
print("   ", inspect.signature(fn))

p = os.path.join(SCRATCH, "dbg_d.tlog")
if os.path.exists(p):
    os.remove(p)
mc = mavutil.mavlink_connection("udpin:0.0.0.0:14596", source_system=250,
                                source_component=190, dialect="ardupilotmega")
fh = open(p, "wb", buffering=0)

# tap 1: recv() level
orig_recv = mc.recv


def tap_recv(n=None):
    s = orig_recv(n)
    if s:
        try:
            fh.write(s)
        except Exception:                      # noqa: BLE001
            pass
    return s


mc.recv = tap_recv
# tap 2 juga di socket level (kalau recv_msg tidak lewat self.recv)
try:
    orig_sock = mc.port.recv
    def tap_sock(bufsize):
        s = orig_sock(bufsize)
        if s:
            try:
                fh.write(s)
            except Exception:                  # noqa: BLE001
                pass
        return s
    mc.port.recv = tap_sock
except Exception as e:                         # noqa: BLE001
    print("D) socket tap tak bisa:", e)

fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_vehicle.py"),
                         "--endpoint", "udpout:127.0.0.1:14596", "--secs", "15"])
t0 = time.time()
n = 0
while time.time() - t0 < 4:
    m = mc.recv_match(blocking=True, timeout=0.1)
    if m:
        n += 1
print("D) pesan=%d  tlog tap=%d B" % (n, os.path.getsize(p)))

# kirim rc 18 kanal (v20 sekarang)
try:
    mc.target_system = 1
    mc.target_component = 1
    mc.mav.rc_channels_override_send(1, 1, *([1500] * 18))
    print("D) send18 (v20): OK")
except Exception as e:                         # noqa: BLE001
    print("D) send18 (v20): ERR %r" % e)
fake.terminate()
