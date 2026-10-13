#!/usr/bin/env python3
"""Uji modul video (StreamPlayer): display + rekam + snapshot — tanpa hardware.

Jalankan dari folder gui_t1:.venv/bin/python../tests/test_video.py
(opsional: set GST_PLUGIN_PATH ke plugin h264parse hasil ekstraksi supaya rekaman mkv.)
"""
import glob
import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "gui_t1"))

from PySide6 import QtWidgets          # noqa: E402
import video                           # noqa: E402

SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test")
os.makedirs(SCRATCH, exist_ok=True)
PORT = 5610
RELAY = 5710


def main():
    app = QtWidgets.QApplication(sys.argv)
    src = subprocess.Popen(
        ["gst-launch-1.0", "-q", "videotestsrc", "is-live=true", "!", "video/x-raw,width=640,height=360,framerate=25/1", "!",
         "x264enc", "tune=zerolatency", "speed-preset=ultrafast", "key-int-max=25", "bitrate=700", "!",
         "rtph264pay", "config-interval=1", "pt=96", "!", "udpsink", "host=127.0.0.1", "port=%d" % PORT],
        stderr=subprocess.DEVNULL)
    time.sleep(1.5)

    def tunggu(kondisi, detik):
        t0 = time.time()
        while time.time() - t0 < detik:
            app.processEvents()
            if kondisi():
                return True
            time.sleep(0.02)
        return False

    def jalan(detik):
        t0 = time.time()
        while time.time() - t0 < detik:
            app.processEvents()
            time.sleep(0.02)

    hasil = {"frames": 0, "err": []}
    player = video.StreamPlayer("uji", PORT, RELAY)
    player.sig_frame.connect(lambda img, fps: hasil.__setitem__("frames", hasil["frames"] + 1))
    player.sig_error.connect(lambda e: hasil["err"].append(e))
    player.start()

    ok1 = tunggu(lambda: hasil["frames"] >= 20, 10)
    print("[1] display: frames=%d (butuh>=20) ok=%s err=%s"
          % (hasil["frames"], ok1, hasil["err"][:2]))

    bisa, kont = player.rekam_bisa()
    print("[2] h264parse tersedia=%s → kontainer=%s" % (video.h264parse_tersedia(), kont))
    rec = os.path.join(SCRATCH, "uji_rekam_" + time.strftime("%H%M%S"))
    ok2 = player.start_record(rec)
    tunggu(lambda: player.is_recording(), 3)           # fase spawn
    jalan(4.0)                                         # WAJIB lewat event loop:
    player.stop_record()                               # singleShot(_buka_relai) harus jalan
    ok3 = tunggu(lambda: not player.is_recording(), 10)
    files = glob.glob(rec + "*")
    size = os.path.getsize(files[0]) if files else 0
    print("[3] rekam: start=%s stop_ok=%s file=%s size=%.0f B"
          % (ok2, ok3, os.path.basename(files[0]) if files else None, size))

    snap = os.path.join(SCRATCH, "uji_snap.png")
    ok4 = player.snapshot(snap)
    print("[4] snapshot: ok=%s size=%s"
          % (ok4, os.path.getsize(snap) if os.path.exists(snap) else 0))

    frames_rec = 0
    if files:
        ffp = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                              "-show_entries", "stream=nb_read_frames,codec_name",
                              "-of", "default=nw=1", files[0]],
                             capture_output=True, text=True)
        out = ffp.stdout.strip()
        print("[5] ffprobe:", out.replace("\n", " | "),
              ("| " + ffp.stderr.strip()[:160]) if ffp.stderr.strip() else "")
        for line in out.splitlines():
            if line.startswith("nb_read_frames="):
                try:
                    frames_rec = int(line.split("=")[1])
                except ValueError:
                    frames_rec = 0

    player.stop()
    src.terminate()
    try:
        src.wait(timeout=3)
    except Exception:                                  # noqa: BLE001
        src.kill()

    lulus = ok1 and ok2 and ok3 and size > 50000 and ok4 and frames_rec >= 25
    print("HASIL-UJI-VIDEO:", "LULUS" if lulus else "GAGAL",
          "(frame rekaman=%d, butuh>=25)" % frames_rec)
    return 0 if lulus else 1


if __name__ == "__main__":
    sys.exit(main())
