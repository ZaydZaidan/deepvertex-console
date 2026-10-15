#!/usr/bin/env python3
"""README screenshots exactly AS THE REAL DISPLAY (J env X11 :1).

Difference from probe_readme.py: here the console RUNS FULLY VISIBLE on the
desktop (no WA_DontShowOnScreen), and the capture is taken from the X11 screen
itself (QScreen.grabWindow + crop to the window frame) — the PNG therefore
equals what a human sees on the monitor (fonts, blends, WM frame).
Requirements: real X display; ports 5600-5602 free; ports 14590/8793 free.
Output: <SCRATCH>/readme_native/*.png
"""
import os
import subprocess
import sys
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.join(HERE, "..", "gui_t1")
sys.path.insert(0, GUI)
SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test/readme_native")
os.makedirs(SCRATCH, exist_ok=True)
PORTS = {"front": 5600, "left": 5601, "right": 5602}

anak = []


def stream(pattern, teks, port):
    return subprocess.Popen(
        ["gst-launch-1.0", "-q", "videotestsrc", "pattern=%s" % pattern,
         "is-live=true", "!", "video/x-raw,width=1280,height=720,"
         "framerate=25/1", "!",
         "x264enc", "tune=zerolatency", "speed-preset=ultrafast",
         "key-int-max=25", "bitrate=800", "!",
         "rtph264pay", "config-interval=1", "pt=96", "!",
         "udpsink", "host=127.0.0.1", "port=%d" % port],
        stderr=subprocess.DEVNULL)


def main():
    anak.append(subprocess.Popen(
        [sys.executable, os.path.join(HERE, "fake_vehicle.py"),
         "--peta-ulir", "--udp-in", "0.0.0.0:14590"],
        stderr=subprocess.DEVNULL))
    anak.append(subprocess.Popen(
        [sys.executable, os.path.join(GUI, "..", "onboard", "raw_agent.py"),
         "--test", "--port", "8793"], stderr=subprocess.DEVNULL))
    anak.append(stream("smpte", "FRONT CAMERA", PORTS["front"]))
    anak.append(stream("smpte", "STEREO L (B0495C)", PORTS["left"]))
    anak.append(stream("snow", "STEREO R (B0495C)", PORTS["right"]))
    time.sleep(2.0)

    from PySide6 import QtGui, QtWidgets, QtCore
    import rov_console_real as rc
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    rc._terapkan_tema(app, "ink", 1.2)

    args = SimpleNamespace(
        mavlink="udpin:0.0.0.0:14590",
        front_port=PORTS["front"], left_port=PORTS["left"],
        right_port=PORTS["right"],
        port_front=PORTS["front"], port_stereo_L=PORTS["left"],
        port_stereo_R=PORTS["right"],
        raw_agent="http://127.0.0.1:8793",
        outdir=os.path.join(SCRATCH, "sesi"),
        preset="pilot-focus", no_pad=False,
        nama_sesi="Coral Survey A", no_prompt=True,
        tlog="off", tema=None, teks=1.2)
    w = rc.RovConsole(args)
    w.resize(1920, 1163)
    w.show()
    try:
        w.activateWindow()
        w.raise_()
    except Exception:
        pass

    def jalan(sec):
        t0 = time.time()
        while time.time() - t0 < sec:
            app.processEvents()
            time.sleep(0.02)

    def shot_widget(top, out, jeda=0.6):
        top.activateWindow() if hasattr(top, "activateWindow") else None
        top.raise_()
        jalan(jeda)
        scr = QtGui.QGuiApplication.primaryScreen()
        # X11: grab the REAL window surface by window id — identical to what
        # the monitor shows, unaffected by other windows on top.
        img = scr.grabWindow(int(top.winId()))
        img.save(out)
        jalan(0.2)
        print("shot:", out, "size:", img.width(), "x", img.height(), flush=True)

    jalan(8.0)

    for nama in ("pilot-focus", "mapping", "setup", "capture"):
        w._apply_preset(nama)
        jalan(2.0)
        out = os.path.join(SCRATCH, "%s.png" % nama)
        shot_widget(w, out, jeda=0.8)

    try:
        import padmap as _pm
        w._apply_preset("pilot-focus")
        jalan(0.8)
        d = _pm.PadMapDialog(w.gamepad, w)
        d.show()
        d.move(max(0, (QtWidgets.QApplication.primaryScreen().geometry().
                       width() - d.width()) // 2), 40)
        jalan(0.6)
        d.gambar.set_live([0.55, -0.6, 0.0, -0.3, 0.0, 0.0, 0.0, 0.0],
                          [False, False, False, True, False, True] +
                          [False] * 7)
        jalan(0.5)
        out = os.path.join(SCRATCH, "gamepad-mapping.png")
        shot_widget(d, out, jeda=0.6)
        d.close()
    except Exception as exc:
        print("padmap shot failed:", exc, flush=True)

    try:
        w.close()
    except Exception:
        pass
    jalan(0.4)
    for s in anak:
        try:
            s.terminate()
        except Exception:
            pass
    jalan(0.3)
    print("SELESAI-README-NATIVE", flush=True)


if __name__ == "__main__":
    main()
