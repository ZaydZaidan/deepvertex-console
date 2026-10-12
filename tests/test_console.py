#!/usr/bin/env python3
"""Uji konsol T1 offscreen: 3 stream dummy + MAVLink palsu + agen RAW (--test).

Prasyarat: agen RAW jalan di :8791 (lihat tests/test_raw.py docstring).
Jalankan dari folder gui_t1:  .venv/bin/python ../tests/test_console.py
"""
import glob
import os
import subprocess
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.join(HERE, "..", "gui_t1")
sys.path.insert(0, GUI)

from PySide6 import QtWidgets          # noqa: E402

SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test")
os.makedirs(SCRATCH, exist_ok=True)


def sumber(port):
    return subprocess.Popen(
        ["gst-launch-1.0", "-q", "videotestsrc", "is-live=true", "!", "video/x-raw,width=640,height=360,framerate=25/1", "!",
         "x264enc", "tune=zerolatency", "speed-preset=ultrafast", "key-int-max=25", "bitrate=600", "!",
         "rtph264pay", "config-interval=1", "pt=96", "!", "udpsink", "host=127.0.0.1", "port=%d" % port],
        stderr=subprocess.DEVNULL)


def main():
    app = QtWidgets.QApplication(sys.argv)
    srcs = [sumber(p) for p in (5600, 5601, 5602)]
    fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_vehicle.py"),
                             "--endpoint", "udpout:127.0.0.1:14550", "--secs", "30"])
    time.sleep(1.5)

    import rov_console_real as rc

    args = SimpleNamespace(
        mavlink="udpin:0.0.0.0:14550",
        front_port=5600, left_port=5601, right_port=5602,
        port_front=5600, port_stereo_L=5601, port_stereo_R=5602,
        raw_agent="http://127.0.0.1:8791",
        outdir=os.path.join(SCRATCH, "cons"))
    w = rc.RovConsole(args)
    w.show()

    def jalan(sec):
        t0 = time.time()
        while time.time() - t0 < sec:
            app.processEvents()
            time.sleep(0.02)

    def tunggu(kondisi, detik):
        t0 = time.time()
        while time.time() - t0 < detik:
            app.processEvents()
            if kondisi():
                return True
            time.sleep(0.02)
        return False

    lulus = True
    ok_frames = tunggu(lambda: all(w.players[n]._qimg is not None
                                   for n in ("front", "stereo L", "stereo R")), 15)
    print("[1] frame tiga stream:", ok_frames)
    lulus &= ok_frames

    ok_mav = tunggu(lambda: (w.last.get("mode") not in (None, "--")) and bool(w.last.get("volt")), 15)
    print("[2] telemetri: mode=%s armed=%s volt=%s depth=%s head=%s" % (
        w.last.get("mode"), w.last.get("armed"), w.last.get("volt"),
        w.last.get("depth"), w.last.get("heading")))
    lulus &= bool(ok_mav)

    w.btn_rec["front"].setChecked(True)
    jalan(4.0)
    w.btn_rec["front"].setChecked(False)
    ok_stop = tunggu(lambda: not w.players["front"].is_recording(), 10)
    files = sorted(glob.glob(os.path.join(w.sesi, "front_*")))
    size = os.path.getsize(files[-1]) if files else 0
    ok_rec = bool(files) and size > 50000
    print("[3] rekam front: stop_ok=%s file=%s size=%.0f B" % (
        ok_stop, os.path.basename(files[-1]) if files else None, size))
    lulus &= ok_rec

    w._snap("stereo L")
    snaps = glob.glob(os.path.join(w.sesi, "snap_stereo_L_*.png"))
    print("[4] snapshot stereo L:", bool(snaps))
    lulus &= bool(snaps)

    hasil = {}
    w.rawpanel.client.sig_result.connect(lambda tag, d: hasil.__setitem__(tag, d))
    w.rawpanel.client.status()
    ok_raw = tunggu(lambda: "status" in hasil, 8) and bool(hasil.get("status", {}).get("ok"))
    print("[5] agen RAW dari panel:", ok_raw)
    lulus &= bool(ok_raw)

    print("HASIL-UJI-KONSOL:", "LULUS" if lulus else "GAGAL")
    w.close()
    jalan(0.5)
    for s in srcs:
        s.terminate()
    fake.terminate()
    for p in srcs + [fake]:
        try:
            p.wait(timeout=3)
        except Exception:                                  # noqa: BLE001
            p.kill()
    return 0 if lulus else 1


if __name__ == "__main__":
    sys.exit(main())
