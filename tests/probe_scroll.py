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
        nama_sesi=None, no_prompt=True,
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
    # tangkapan tambahan: preset pilot, semua rel di-scroll bawah (PRE-FLIGHT ▸ SENSORS)
    w._apply_preset("pilot-focus")
    jalan(0.6)
    for s2 in w.findChildren(QtWidgets.QScrollArea):
        s2.verticalScrollBar().setValue(s2.verticalScrollBar().maximum())
    jalan(0.5)
    shot3 = os.path.join(SCRATCH, "probe_pilot_bawah.png")
    w.grab().save(shot3)
    print("screenshot pilot-bawah:", shot3)

    # ---- sweep ukuran jendela (proxy device/OS): rel kanan tanpa klip-H
    print("== sweep ukuran (dinamis, device/OS berbeda) ==")
    klip_total = []
    for (ww, hh) in ((1280, 800), (1366, 768), (1440, 900), (1620, 1000),
                     (1854, 1011)):
        w.resize(ww, hh)
        jalan(0.9)
        klip = []
        for s in w.findChildren(QtWidgets.QScrollArea):
            vb = s.verticalScrollBar()
            vp = s.viewport().width()
            dalam = s.widget()
            minw = dalam.minimumSizeHint().width() if dalam else 0
            if minw > vp + 2:
                ttl = "?"
                for ch in dalam.findChildren(
                        QtWidgets.QWidget,
                        options=QtCore.Qt.FindDirectChildrenOnly):
                    if ch.isVisible() and ch.minimumSizeHint().width() > vp + 2:
                        try:
                            lab = ch.findChild(QtWidgets.QLabel, "PanelTitle")
                            ttl = lab.text() if lab is not None else \
                                ch.__class__.__name__
                        except Exception:                   # noqa: BLE001
                            ttl = ch.__class__.__name__
                        break
                klip.append("%s minW=%d>vpW=%d" % (ttl, minw, vp))
        t58 = w.P["cam_front"].tile
        asp = getattr(t58, "_aspect", 16.0 / 9.0)
        pakai = t58.width() <= int(max(1, t58.height() - 18) * asp) + 3
        tidak_jepit = t58.height() >= t58.heightForWidth(t58.width()) - 6
        print("  %dx%d -> rel:%s | tile:%s" %
              (ww, hh, "OK" if not klip else "KLIP %s" % klip,
               "PENUH+FIT" if (pakai and tidak_jepit)
               else ("BORDER" if not pakai else "TERJEPIT")))
        klip_total.extend(["%dx%d %s" % (ww, hh, k) for k in klip])
    print("SWEEP:", "SEMUA OK" if not klip_total else klip_total)

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
