#!/usr/bin/env python3
"""Probe geometri konsol T1 di DISPLAY nyata (DPI sama dengan sesi pemilik).

Jalankan dari gui_t1:   .venv/bin/python ../tests/probe_layout.py
Mengukur: lebar rail, ukuran dial, readout vs kebutuhan teks, TOMBOL TERPOTONG,
CHECKBOX TERPOTONG; menyimpan 2 screenshot (pilot + mapping) untuk verifikasi visual.
"""
import os
import subprocess
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.join(HERE, "..", "gui_t1")
sys.path.insert(0, GUI)

from PySide6 import QtGui, QtWidgets          # noqa: E402

SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test")
os.makedirs(SCRATCH, exist_ok=True)


def main():
    app = QtWidgets.QApplication(sys.argv)
    fake = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "fake_vehicle.py"),
         "--endpoint", "udpout:127.0.0.1:14580", "--secs", "30",
         "--stats", os.path.join(SCRATCH, "fv_stats2.json")],
        stdout=subprocess.DEVNULL)
    time.sleep(1.0)

    import rov_console_real as rc
    rc._terapkan_tema(app)          # WAJIB: QSS + font tema (kalau tidak, tampil polos)

    args = SimpleNamespace(
        mavlink="udpin:0.0.0.0:14580",
        front_port=5630, left_port=5631, right_port=5632,
        port_front=5630, port_stereo_L=5631, port_stereo_R=5632,
        raw_agent="http://127.0.0.1:9",
        outdir=os.path.join(SCRATCH, "probe"),
        preset="pilot-focus", no_pad=True)
    w = rc.RovConsole(args)
    w.resize(1560, 940)
    w.show()

    def jalan(sec):
        t0 = time.time()
        while time.time() - t0 < sec:
            app.processEvents()
            time.sleep(0.02)

    jalan(4.0)

    fm = QtGui.QFontMetrics(w.font())
    print("dpr=%.2f  font=%s pixelSize=%d" % (w.devicePixelRatioF(), w.font().family(),
                                              w.font().pixelSize()))
    print("rail L=%d  mid=%d  rail R=%d" % (w.col_left.width(), w.col_mid.width(),
                                            w.col_right.width()))
    p = w.P["flight"]
    print("flight panel %dx%d" % (p.width(), p.height()))
    print("horizon %dx%d   kompas %dx%d" % (w.horizon.width(), w.horizon.height(),
                                            w.kompas.width(), w.kompas.height()))
    for nm, ro_ in (("depth", w.ro_depth), ("hdg", w.ro_head), ("temp", w.ro_temp),
                    ("camtilt", w.ro_camtilt), ("gain", w.ro_gain)):
        perlu = fm.horizontalAdvance(ro_.lab.text()) + fm.horizontalAdvance(ro_.val.text()) + 14
        print("readout %-8s w=%d perlu=%d text=%r%s" % (
            nm, ro_.width(), perlu, ro_.val.text(),
            "  <-- SEMPIT" if ro_.width() < perlu else ""))

    bad = 0
    for b in w.findChildren(QtWidgets.QPushButton):
        if b.isVisible() and b.text():
            perlu = fm.horizontalAdvance(b.text()) + 12
            if b.width() < perlu:
                print("TRUNC-BTN %r w=%d perlu=%d" % (b.text(), b.width(), perlu))
                bad += 1
    for k, cb_ in w.pf.cb.items():
        perlu = fm.horizontalAdvance(cb_.text()) + 32
        if cb_.width() < perlu:
            print("TRUNC-CB [%s] w=%d perlu=%d %r" % (k, cb_.width(), perlu, cb_.text()))
            bad += 1
    print("TRUNC TOTAL:", bad)

    w.grab().save(os.path.join(SCRATCH, "probe_pilot.png"))
    w._apply_preset("mapping")
    w.peta.zoom(-3)          # zoom-out supaya konteks pulau/laut terlihat di bukti screenshot
    jalan(8.5)
    w.grab().save(os.path.join(SCRATCH, "probe_mapping.png"))
    print("screenshot: probe_pilot.png + probe_mapping.png")

    w.close()
    jalan(0.3)
    fake.terminate()
    try:
        fake.wait(timeout=3)
    except Exception:                                      # noqa: BLE001
        fake.kill()
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
