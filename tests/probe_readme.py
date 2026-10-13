#!/usr/bin/env python3
"""probe_readme.py — README screenshots: default INK theme, window size exactly
like running the console on the owner's screen (no tall canvas, no right-side
clipping). Live demo data: fake autopilot, 3 camera test streams, local RAW agent.

Run from gui_t1:   .venv/bin/python ../tests/probe_readme.py
Output: ~/.hermes/cache/scratch/t1test/readme/<preset>.png + gamepad-mapping.png
"""
import os
import subprocess
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.join(HERE, "..", "gui_t1")
sys.path.insert(0, GUI)

from PySide6 import QtCore, QtWidgets   # noqa: E402

SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test/readme")
os.makedirs(SCRATCH, exist_ok=True)

PORTS = {"front": 5640, "left": 5641, "right": 5642}


def stream(pattern, teks, port):
    return subprocess.Popen(
        ["gst-launch-1.0", "-q",
         "videotestsrc", "is-live=true", "pattern=%s" % pattern, "!",
         "video/x-raw,width=1280,height=720,framerate=25/1", "!",
         "textoverlay", "text=%s" % teks, "font-desc=Sans 28",
         "valignment=top", "halignment=left", "shaded-background=true", "!",
         "x264enc", "tune=zerolatency", "speed-preset=ultrafast",
         "key-int-max=25", "bitrate=1500", "!", "rtph264pay",
         "config-interval=1", "pt=96", "!", "udpsink", "host=127.0.0.1",
         "port=%d" % port],
        stderr=subprocess.DEVNULL)


def main():
    app = QtWidgets.QApplication(sys.argv)
    anak = []
    anak.append(subprocess.Popen(
        [sys.executable, os.path.join(HERE, "fake_vehicle.py"),
         "--endpoint", "udpout:127.0.0.1:14590", "--secs", "300",
         "--stats", os.path.join(SCRATCH, "fv.json")],
        stdout=subprocess.DEVNULL))
    anak.append(subprocess.Popen(
        [sys.executable, os.path.join(GUI, "..", "onboard", "raw_agent.py"),
         "--test", "--port", "8793"],
        stdout=subprocess.DEVNULL))
    anak.append(stream("smpte", "FRONT CAMERA", PORTS["front"]))
    anak.append(stream("smpte", "STEREO L (B0495C)", PORTS["left"]))
    anak.append(stream("snow", "STEREO R (B0495C)", PORTS["right"]))
    time.sleep(2.0)

    import rov_console_real as rc
    rc._terapkan_tema(app, "ink", 1.2)          # INK = default theme

    args = SimpleNamespace(
        mavlink="udpin:0.0.0.0:14590",
        front_port=PORTS["front"], left_port=PORTS["left"],
        right_port=PORTS["right"],
        port_front=PORTS["front"], port_stereo_L=PORTS["left"],
        port_stereo_R=PORTS["right"],
        raw_agent="http://127.0.0.1:8793",
        outdir=os.path.join(SCRATCH, "sesi"),
        preset="pilot-focus", no_pad=False,      # DS4 asli -> PAD ready
        nama_sesi="Coral Survey A", no_prompt=True)
    w = rc.RovConsole(args)
    # WM (GNOME) membatasi tinggi window ke ukuran layar; atribut ini membuat resize
    # dipatuhi penuh & grab() merender seluruh jendela. Ukuran window = SAMA dengan
    # tampilan asli saat pemilik menjalankan di layarnya (1920x1163).
    w.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    w.resize(1920, 1163)
    w.show()

    def jalan(sec):
        t0 = time.time()
        while time.time() - t0 < sec:
            app.processEvents()
            time.sleep(0.02)

    jalan(8.0)

    for nama in ("pilot-focus", "mapping", "setup", "capture"):
        w._apply_preset(nama)
        jalan(1.2)
        for sa in w.findChildren(QtWidgets.QScrollArea):
            sa.verticalScrollBar().setValue(0)
        jalan(0.4)
        out = os.path.join(SCRATCH, "%s.png" % nama)
        w.grab().save(out)
        klip = []
        for sa in w.findChildren(QtWidgets.QScrollArea):
            dalam = sa.widget()
            if dalam is not None and \
                    dalam.minimumSizeHint().width() > sa.viewport().width() + 2:
                klip.append(dalam.minimumSizeHint().width())
        print("shot:", out, "| klip-H:",
              "TIDAK ADA" if not klip else klip)

    try:
        import padmap as _pm
        w._apply_preset("pilot-focus")
        jalan(0.6)
        d = _pm.PadMapDialog(w.gamepad, w)
        d.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        d.show()
        jalan(0.4)
        d.gambar.set_live([0.55, -0.6, 0.0, -0.3, 0.0, 0.0, 0.0, 0.0],
                          [False, False, False, True, False, True] + [False] * 7)
        jalan(0.4)
        out = os.path.join(SCRATCH, "gamepad-mapping.png")
        d.grab().save(out)
        print("shot:", out)
        d.close()
    except Exception as exc:                               # noqa: BLE001
        print("padmap shot failed:", exc)

    try:
        w.close()
    except Exception:                                      # noqa: BLE001
        pass
    app.processEvents()
    for p in anak:
        try:
            p.terminate()
        except Exception:                                  # noqa: BLE001
            pass
    print("done — folder:", SCRATCH)


if __name__ == "__main__":
    main()
