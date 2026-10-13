#!/usr/bin/env python3
"""Uji alur RAW end-to-end TANPA hardware: agen (mode --test) + RawClient.

Prasyarat: agen sudah jalan, mis.:
  /usr/bin/python3 onboard/raw_agent.py --test --dir <scratch>/rawtest \
      --chunk-mb 4 --port 8791 --width 640 --height 360 --fps 25
Jalankan:.venv/bin/python../tests/test_raw.py
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "gui_t1"))

from PySide6 import QtWidgets          # noqa: E402
import rawcap                          # noqa: E402

BASE = "http://127.0.0.1:8791"
SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test")
OUT = os.path.join(SCRATCH, "rawout")
os.makedirs(OUT, exist_ok=True)


def main():
    app = QtWidgets.QApplication(sys.argv)
    client = rawcap.RawClient(BASE)
    hasil = {}
    client.sig_result.connect(lambda tag, d: hasil.__setitem__(tag, d))

    def tunggu(tag, detik=20):
        hasil.pop(tag, None)
        t0 = time.time()
        while time.time() - t0 < detik:
            app.processEvents()
            if tag in hasil:
                return hasil[tag]
            time.sleep(0.02)
        return None

    def jalan(sec):
        t0 = time.time()
        while time.time() - t0 < sec:
            app.processEvents()
            time.sleep(0.02)

    lulus = True
    d = tunggu((client.status(), "status")[0] if False else "status") if False else None
    client.status()
    d = tunggu("status", 8)
    ok = bool(d and d.get("ok"))
    lulus &= ok
    print("[1] status: ok=%s test_mode=%s disk_free=%s" % (ok, (d or {}).get("test"), (d or {}).get("disk_free_mb")))

    client.start("both", chunk_mb=4)
    d = tunggu("start", 10)
    ok = bool(d and d.get("ok"))
    lulus &= ok
    print("[2] start both:", ok, "" if ok else str(d)[:180])

    jalan(2.2)

    client.stop("both")
    d = tunggu("stop", 15)
    ok = bool(d and d.get("ok"))
    lulus &= ok
    print("[3] stop both:", ok, "" if ok else str(d)[:180])

    client.files()
    d = tunggu("files", 12)
    files = (d or {}).get("files", [])
    ok = len(files) >= 1
    lulus &= ok
    total = sum(f["size"] for f in files)
    print("[4] files: %d chunk, total %.1f MB" % (len(files), total / 1e6))

    if files:
        rel = files[0]["path"]
        client.transfer(rel, OUT)
        d = tunggu("transfer", 60)
        ok = bool(d and d.get("ok") and d.get("cocok"))
        lulus &= ok
        print("[5] transfer+sha:", ok, (d or {}).get("sha_lokal", "")[:16],
              "vs", (d or {}).get("sha_agen", "")[:16],
              "" if ok else str(d)[:160])

        if ok:
            client.delete([rel])
            d = tunggu("delete", 12)
            ok = bool(d and d.get("ok"))
            lulus &= ok
            print("[6] delete:", ok)

            client.files()
            d = tunggu("files", 12)
            sisa = [f["path"] for f in (d or {}).get("files", [])]
            ok = rel not in sisa
            lulus &= ok
            print("[7] file terhapus dari daftar:", ok)

    print("HASIL-UJI-RAW:", "LULUS" if lulus else "GAGAL")
    return 0 if lulus else 1


if __name__ == "__main__":
    sys.exit(main())
