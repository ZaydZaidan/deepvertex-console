#!/usr/bin/env python3
"""probe_scroll.py — cek elemen yang berada DI BAWAH LIPATAN panel (scroll).

Kasus: panel FAILSAFE (& BATERAI) kini punya tombol APPLY RECOMMENDED + baris
TEMP warn/crit. Probe biasa memotret tanpa scroll, jadi elemen di dasar panel
tidak terlihat. Skrip ini scroll semua kolom ke bawah lalu memotret + mengukur.
"""
import os
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.join(HERE, "..", "gui_t1")
sys.path.insert(0, GUI)

from PySide6 import QtCore, QtWidgets          # noqa: E402

SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test")
os.makedirs(SCRATCH, exist_ok=True)


def main():
    app = QtWidgets.QApplication(sys.argv)
    import rov_console_real as rc
    rc._terapkan_tema(app, "abyss", 1.2)

    args = SimpleNamespace(
        mavlink="udpin:0.0.0.0:14582",
        front_port=5633, left_port=5634, right_port=5635,
        port_front=5633, port_stereo_L=5634, port_stereo_R=5635,
        raw_agent="http://127.0.0.1:9",
        outdir=os.path.join(SCRATCH, "probe"),
        preset="setup", no_pad=True)
    w = rc.RovConsole(args)
    w.resize(1620, 1000)
    w.show()

    def jalan(sec):
        t0 = time.time()
        while time.time() - t0 < sec:
            app.processEvents()
            time.sleep(0.02)

    jalan(1.5)

    for nm in ("pilot-focus", "mapping", "setup", "capture"):
        w._apply_preset(nm)
        jalan(0.7)
        sa = w.findChildren(QtWidgets.QScrollArea)
        print("== preset", nm)
        for i, s in enumerate(sa):
            vb = s.verticalScrollBar()
            vp = s.viewport().width()
            dalam = s.widget()
            minw = dalam.minimumSizeHint().width() if dalam else 0
            print("  scroll#%d max=%d vpW=%d innerMinW=%d klip-H=%s" %
                  (i, vb.maximum(), vp, minw, minw > vp + 2))
            if dalam is not None:
                for ch in dalam.findChildren(
                        QtWidgets.QWidget,
                        options=QtCore.Qt.FindDirectChildrenOnly):
                    cm = ch.minimumSizeHint().width()
                    if cm > vp + 2 and ch.isVisible():
                        ttl = ""
                        try:
                            lab = ch.findChild(QtWidgets.QLabel, "PanelTitle")
                            ttl = lab.text() if lab is not None else ""
                        except Exception:                  # noqa: BLE001
                            pass
                        print("     PELANGGAR: %s minW=%d>%d  %s" %
                              (ch.__class__.__name__, cm, vp, ttl[:44]))
            vb.setValue(vb.maximum())
        jalan(0.5)
    w._apply_preset("setup")
    jalan(0.6)

    pf = w.P["failsafe"]
    btns = [b for b in pf.findChildren(QtWidgets.QPushButton)
            if b.text().startswith("APPLY RECOMMENDED")]
    print("tombol APPLY RECOMMENDED:", len(btns))
    if btns:
        b = btns[0]
        pos = b.mapTo(w, QtCore.QPoint(0, 0))
        dalam = (0 <= pos.x() < w.width()) and (0 <= pos.y() < w.height())
        print("  teks=%r ukuran=%dx%d pos-in-window=(%d,%d) dalam-window=%s cut=%s"
              % (b.text(), b.width(), b.height(), pos.x(), pos.y(), dalam,
                 b.fontMetrics().horizontalAdvance(b.text()) > b.width() - 16))
    tw = w.sp_temp_warn
    pos = tw.mapTo(w, QtCore.QPoint(0, 0))
    dalam = (0 <= pos.x() < w.width()) and (0 <= pos.y() < w.height())
    print("sp_temp_warn pos-in-window=(%d,%d) dalam-window=%s nilai=%d"
          % (pos.x(), pos.y(), dalam, tw.value()))
    tc = w.sp_temp_crit
    pos = tc.mapTo(w, QtCore.QPoint(0, 0))
    print("sp_temp_crit pos-in-window=(%d,%d) dalam-window=%s"
          % (pos.x(), pos.y(), (0 <= pos.x() < w.width()) and (0 <= pos.y() < w.height())))

    # panel guide juga dicek: tombol langkah 6 di guide
    pg = w.P.get("guide")
    if pg is not None:
        gb = [b for b in pg.findChildren(QtWidgets.QPushButton)]
        print("tombol guide:", len(gb), [b.text() for b in gb])

    shot = os.path.join(SCRATCH, "probe_setup_bawah.png")
    w.grab().save(shot)
    print("screenshot:", shot)


if __name__ == "__main__":
    main()
