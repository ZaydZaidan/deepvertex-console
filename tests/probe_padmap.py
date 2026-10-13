#!/usr/bin/env python3
"""probe_padmap.py — reproduksi tombol PAD MAPPING (bug 'ngebug' 13 Okt).

Membangun konsol (mode probe), lalu membuka PadMapDialog persis seperti tombol
PAD MAPPING… lakukan, menjalankan semua operasinya dengan payload sinyal palsu,
dan MENANGKAP traceback apa pun. Screenshot dialog disimpan untuk verifikasi.
"""
import os
import sys
import time
import traceback
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.join(HERE, "..", "gui_t1")
sys.path.insert(0, GUI)

from PySide6 import QtWidgets                     # noqa: E402

SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test")
os.makedirs(SCRATCH, exist_ok=True)


def main():
    app = QtWidgets.QApplication(sys.argv)
    import rov_console_real as rc
    import padmap
    rc._terapkan_tema(app, "abyss", 1.2)

    args = SimpleNamespace(
        mavlink="udpin:0.0.0.0:14584",
        front_port=5636, left_port=5637, right_port=5638,
        port_front=5636, port_stereo_L=5637, port_stereo_R=5638,
        raw_agent="http://127.0.0.1:9",
        outdir=os.path.join(SCRATCH, "probe"),
        preset="pilot-focus", no_pad=False)
    w = rc.RovConsole(args)
    w.resize(1620, 1000)
    w.show()

    def jalan(sec):
        t0 = time.time()
        while time.time() - t0 < sec:
            app.processEvents()
            time.sleep(0.02)

    jalan(2.0)
    print("gamepad obj:", type(w.gamepad).__name__, "konek:", getattr(w.gamepad, "_konek", None))
    keys = [k for k in ("axis_fwd", "axis_lat", "axis_depth", "axis_yaw", "axis_tilt",
                        "deadman_buttons", "btn_arm_toggle", "btn_mode_cycle",
                        "btn_depth_hold", "btn_center_cam")]
    print("cfg keys ada:", {k: (k in w.gamepad.cfg) for k in keys})

    # 1) konstruksi dialog (persis seperti _padmap_dialog)
    try:
        d = padmap.PadMapDialog(w.gamepad, w)
        d.show()
        jalan(0.8)
        print("dialog konstruksi+show: OK")
    except Exception:                                  # noqa: BLE001
        print("!! dialog konstruksi GAGAL:")
        print("EXC>>>")
        print(traceback.format_exc())
        print("<<<EXC")
        return

    # 2) operasi segarkan & tangkap
    for fn, nm in ((lambda: d._segarkan(), "_segarkan"),
                   (lambda: d._mulai_tangkap("axis_fwd"), "_mulai_tangkap(axis_fwd)"),
                   (lambda: d._live({"axes": [0.0] * 8, "buttons": [False] * 13}), "_live #1"),
                   (lambda: d._live({"axes": [0.0, 0.9, 0, 0, 0, 0, 0, 0],
                                     "buttons": [False] * 13}), "_live #2 (sumbu)"),
                   (lambda: d._mulai_tangkap("btn_arm_toggle"), "tangkap btn_arm"),
                   (lambda: d._live({"axes": [0.0] * 8, "buttons": [True] + [False] * 12}),
                    "_live #3 (tombol)"),
                   (lambda: d._terapkan(), "_terapkan"),
                   (lambda: d._segarkan(), "_segarkan #2")):
        try:
            fn()
            jalan(0.1)
            print("  %-28s OK" % nm)
        except Exception:                              # noqa: BLE001
            print("  %-28s GAGAL:" % nm)
            print("EXC>>>")
            print(traceback.format_exc())
            print("<<<EXC")

    print("label axis_fwd ->", d.lbl["axis_fwd"].text())
    print("label btn_arm  ->", d.lbl["btn_arm_toggle"].text())
    print("label deadman  ->", d.lbl["deadman_buttons"].text())
    print("file preset ada:", os.path.exists(d.path), d.path)

    shot = os.path.join(SCRATCH, "padmap_dialog.png")
    d.grab().save(shot)
    print("screenshot dialog:", shot)
    print("ukuran dialog:", d.width(), "x", d.height(),
          "| CLOSE sisa bawah:", d.rect().bottom() - d.b_close.geometry().bottom())

    # 3) jalur tombol konsol (handler yang dipakai PAD MAPPING…) — tanpa exec
    try:
        import padmap as pm
        _pm = pm
        d2 = pm.PadMapDialog(w.gamepad, w)
        d2.show()
        jalan(0.4)
        d2.close()
        print("handler tombol (dialog kedua): OK")
        # uji visual INTERAKTIF deterministik: widget BERSIH (tanpa overwrite tick
        # gamepad asli 20 Hz) — payload palsu harus membuat elemen menyala.
        diag = _pm.PadDiagram()
        diag.resize(640, 400)
        diag.set_live([0.6, -0.8, 0.0, -0.5, 0.4, 0.0, -1.0, 0.6],
                      [True, False, False, True, False, True] + [False] * 7)
        diag.show()
        jalan(0.4)
        shot2 = os.path.join(SCRATCH, "padmap_live.png")
        diag.grab().save(shot2)
        print("screenshot live:", shot2)
        diag.close()
    except Exception:                                  # noqa: BLE001
        print("!! dialog kedua GAGAL:")
        print("EXC>>>")
        print(traceback.format_exc())
        print("<<<EXC")

    # tutup konsol dengan rapi (stop gamepad/bridge/players) — menghindari race
    # teardown thread evdev (segfault saat exit).
    try:
        w.close()
        app.processEvents()
    except Exception:                                  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
