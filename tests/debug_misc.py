#!/usr/bin/env python3
"""Debug tlog + RC keep-alive (12 Okt — investigasi uji [13]/[15] gagal)."""
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.join(HERE, "..", "gui_t1")
sys.path.insert(0, GUI)

from PySide6 import QtWidgets                  # noqa: E402

SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test")
os.makedirs(SCRATCH, exist_ok=True)
STATS = os.path.join(SCRATCH, "dbg_stats.json")

# --- A) murni pymavlink: apakah recv_msg menulis logfile_raw? ---
from pymavlink import mavutil                 # noqa: E402

path_a = os.path.join(SCRATCH, "dbg_a.tlog")
if os.path.exists(path_a):
    os.remove(path_a)
ma = mavutil.mavlink_connection("udpin:0.0.0.0:14591")
ma.setup_logfile_raw(path_a, "ab")
fakea = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_vehicle.py"),
                          "--endpoint", "udpout:127.0.0.1:14591", "--secs", "12",
                          "--stats", STATS])
t0 = time.time()
n = 0
while time.time() - t0 < 6:
    m = ma.recv_match(blocking=True, timeout=0.1)
    if m:
        n += 1
print("A) murni pymavlink: pesan=%d tlog=%d B (ada=%s)" % (
    n, os.path.getsize(path_a) if os.path.exists(path_a) else -1,
    os.path.exists(path_a)))
fakea.terminate()

# --- B) konsol penuh: tlog + RC keep-alive ---
app = QtWidgets.QApplication(sys.argv)
import rov_console_real as rc                  # noqa: E402

try:
    os.remove(STATS)
except OSError:
    pass
fakeb = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_vehicle.py"),
                          "--endpoint", "udpout:127.0.0.1:14592", "--secs", "60",
                          "--stats", STATS])
time.sleep(1.0)
args = SimpleNamespace(
    mavlink="udpin:0.0.0.0:14592",
    front_port=5640, left_port=5641, right_port=5642,
    port_front=5640, port_stereo_L=5641, port_stereo_R=5642,
    raw_agent="http://127.0.0.1:9",
    outdir=os.path.join(SCRATCH, "dbg"),
    preset="pilot-focus", no_pad=False)
w = rc.RovConsole(args)
w.show()


def pump(sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        app.processEvents()
        time.sleep(0.02)


def stats():
    try:
        with open(STATS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                          # noqa: BLE001
        return {}


pump(6)
print("B) connected:", w.bridge.state.get("link"), "| endpoint:", w.bridge.state.get("endpoint"))
tp = w.bridge.tlog_path
print("B) tlog_path:", tp, "| size:", os.path.getsize(tp) if tp and os.path.exists(tp) else "(tidak ada)")
print("B) gamepad.aktif:", getattr(w.gamepad, "aktif", None))
print("B) rc awal:", stats().get("rc"), "| manual:", stats().get("manual"))
w.sl_tilt.setValue(1800)
print("B) _rc_keep setelah setValue:", dict(w._rc_keep))
pump(5)
print("B) rc setelah 5s:", stats().get("rc"), "| _rc_keep:", dict(w._rc_keep))
w.bridge.rc_override({8: 1700})
pump(1.5)
print("B) rc setelah override manual:", stats().get("rc"))
w.close()
pump(0.3)
fakeb.terminate()
