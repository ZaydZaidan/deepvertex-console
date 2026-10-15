#!/usr/bin/env python3
"""DeepVertex raw capture agent — perekam RAW onboard (Delivery, opsi B).

Berjalan di robot (Raspberry Pi 4 / BlueOS). Merekam frame RAW (YUYV) dari
kamera UVC ke file chunk di disk lokal, dikendalikan lewat HTTP dari GUI topside.

Ketergantungan: python3 stdlib + binary `gst-launch-1.0` (sudah ada di BlueOS).

Protokol HTTP (JSON):
  GET  /status                -> status service, disk, capture aktif
  POST /capture/start         -> {"cam": "left"|"right"|"front"|"both", "chunk_mb": 1024}
  POST /capture/stop          -> {"cam": ...}
  GET  /files                 -> daftar chunk (path relatif, ukuran, mtime)
  GET  /sha256/<path>         -> sha256 file (dihitung streaming, di-cache)
  GET  /download/<path>       -> unduh file (streaming)
  POST /delete                -> {"names": [...]}  (path relatif dari /files)

Auth opsional: bila --token diisi, semua request harus menyertakan header
`X-Token` atau query `?token=`.

Contoh uji tanpa hardware (desktop):
  python3 raw_agent.py --test --dir /tmp/dv_raw --chunk-mb 2 --port 8765

Produksi (di Pi, contoh):
  python3 raw_agent.py --device-left /dev/video0 --device-right /dev/video2 \
      --dir /home/pi/raw_captures --chunk-mb 1024 --port 8765 --token RAHASIA
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

VERSI = "1.0.0"
NAMA_SERVICE = "deepvertex-raw-agent"

ARGS = None
LOCK = threading.Lock()
CAPTURES = {}          # cam -> dict(session=..., proc=..., started=..., err=deque)
SHA_CACHE = {}         # path relatif -> sha256


# --------------------------------------------------------------------------- #
# util
# --------------------------------------------------------------------------- #
def _now_tag():
    return time.strftime("%Y%m%d_%H%M%S")


def _dir_free_mb(path):
    st = os.statvfs(path)
    return (st.f_bavail * st.f_frsize) // (1024 * 1024)


def _rel_safe(rel):
    """Tolak path keluar direktori basis."""
    rel = unquote(rel).lstrip("/")
    p = os.path.normpath(os.path.join(ARGS.dir, rel))
    if not p.startswith(os.path.normpath(ARGS.dir) + os.sep):
        return None
    return p


def _files_list():
    out = []
    base = os.path.normpath(ARGS.dir)
    for root, _dirs, files in os.walk(base):
        for f in sorted(files):
            if not f.endswith(".yuv"):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, base)
            try:
                stt = os.stat(full)
            except OSError:
                continue
            out.append({"path": rel, "size": stt.st_size, "mtime": int(stt.st_mtime)})
    out.sort(key=lambda e: e["path"])
    return out


def _sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blok in iter(lambda: f.read(1024 * 1024), b""):
            h.update(blok)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# capture
# --------------------------------------------------------------------------- #
def _gst_cmd(cam, dir_sesi):
    # front (Low-Light USB) via USB2: resolusi RAW dibatasi (default 640x480).
    if cam == "front":
        w, h, fps = ARGS.front_width, ARGS.front_height, ARGS.fps
    else:
        w, h, fps = ARGS.width, ARGS.height, ARGS.fps
    loc = os.path.join(dir_sesi, "chunk_%05d.yuv")
    out = ["multifilesink", "location=%s" % loc,
           "next-file=max-size", "max-file-size=%d" % (ARGS.chunk_mb * 1024 * 1024)]
    if ARGS.test:
        src = ["videotestsrc", "is-live=true"]
    else:
        dev = {"left": ARGS.device_left, "right": ARGS.device_right,
               "front": getattr(ARGS, "device_front", None)}.get(cam)
        if not dev:
            raise RuntimeError("device %s not set (--device-%s)" % (cam, cam))
        src = ["v4l2src", "device=%s" % dev, "do-timestamp=true", "io-mode=2"]
    # PENTING: caps harus SATU token dengan koma (gst-launch menggabung token
    # berspasi tanpa koma menjadi string caps yang gagal di-parse).
    caps = ["video/x-raw,format=YUY2,width=%d,height=%d,framerate=%d/1" % (w, h, fps)]
    return ["gst-launch-1.0", "-q"] + src + ["!"] + caps + ["!"] + out


def capture_start(cam, chunk_mb=None):
    with LOCK:
        if cam in CAPTURES and CAPTURES[cam]["proc"].poll() is None:
            return {"ok": False, "error": "capture %s is already active" % cam}
        if chunk_mb:
            ARGS.chunk_mb = int(chunk_mb)
        sesi = "%s_%s" % (cam, _now_tag())
        dir_sesi = os.path.join(ARGS.dir, sesi)
        os.makedirs(dir_sesi, exist_ok=True)
        cmd = _gst_cmd(cam, dir_sesi)
        log_err = deque(maxlen=200)
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True)
        def _baca_err(p=proc, buf=log_err):
            for baris in p.stderr:
                buf.append(baris.rstrip())
        threading.Thread(target=_baca_err, daemon=True).start()
        CAPTURES[cam] = {"session": sesi, "dir": dir_sesi, "proc": proc,
                         "started": time.time(), "err": log_err,
                         "cmd": " ".join(cmd)}
        time.sleep(0.4)
        if proc.poll() is not None:
            err = "\n".join(list(log_err)[-4:]) or "gst-launch exited (exit=%s)" % proc.returncode
            CAPTURES.pop(cam, None)
            return {"ok": False, "error": err}
        return {"ok": True, "session": sesi, "chunk_mb": ARGS.chunk_mb}


def capture_stop(cam):
    with LOCK:
        ent = CAPTURES.pop(cam, None)
    if not ent:
        return {"ok": False, "error": "no active capture for %s" % cam}
    proc = ent["proc"]
    if proc.poll() is None:
        proc.terminate()                      # RAW: file chunk tak butuh EOS
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    # manifest sesi
    dur = time.time() - ent["started"]
    files = []
    for root, _dirs, fs in os.walk(ent["dir"]):
        for f in sorted(fs):
            if f.endswith(".yuv"):
                full = os.path.join(root, f)
                files.append({"name": f, "size": os.stat(full).st_size})
    manifest = {"service": NAMA_SERVICE, "cam": cam, "session": ent["session"],
                "started": ent["started"], "stopped": time.time(), "durasi_s": round(dur, 1),
                "caps": "YUY2 %dx%d@%d" % (ARGS.width, ARGS.height, ARGS.fps),
                "chunk_mb": ARGS.chunk_mb, "files": files,
                "cmd": ent["cmd"], "test": bool(ARGS.test)}
    mpath = os.path.join(ent["dir"], "manifest.json")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return {"ok": True, "session": ent["session"],
            "bytes": sum(f["size"] for f in files), "files": len(files)}


def _disk_guard():
    """Hentikan semua capture bila disk menyusut di bawah ambang."""
    while True:
        time.sleep(5)
        try:
            if _dir_free_mb(ARGS.dir) < ARGS.min_free_mb:
                aktif = list(CAPTURES.keys())
                for cam in aktif:
                    capture_stop(cam)
                    CAPTURES.setdefault("_guard", deque(maxlen=1))  # penanda
                if aktif:
                    CAPTURES["_disk_full"] = {"at": time.time()}
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = NAMA_SERVICE + "/" + VERSI

    def log_message(self, fmt, *args):        # ringkas ke stderr
        print("[http] %s - %s" % (self.address_string(), fmt % args), flush=True)

    # -------------------------------------------------------------- util
    def _auth_ok(self):
        if not ARGS.token:
            return True
        if self.headers.get("X-Token") == ARGS.token:
            return True
        q = parse_qs(urlparse(self.path).query)
        return q.get("token", [None])[0] == ARGS.token

    def _json(self, obj, kode=200):
        data = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(kode)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8") or "{}")

    def _cek(self):
        if not self._auth_ok():
            self._json({"ok": False, "error": "token salah"}, 403)
            return False
        return True

    # -------------------------------------------------------------- GET
    def do_GET(self):
        if not self._cek():
            return
        u = urlparse(self.path)
        p = u.path.rstrip("/") or "/"

        if p == "/status":
            with LOCK:
                caps = {}
                for cam, ent in CAPTURES.items():
                    if not isinstance(ent, dict):
                        continue
                    proc = ent.get("proc")
                    kap = None
                    if proc is not None:
                        kap = (os.path.getsize(ent["dir"]) if os.path.isdir(ent["dir"]) else None)
                    caps[cam] = {"session": ent.get("session"),
                                 "aktif": bool(proc and proc.poll() is None),
                                 "elapsed_s": round(time.time() - ent.get("started", time.time()), 1)
                                 if proc else None,
                                 "err_tail": list(ent.get("err", []))[-3:] or None}
                disk_full = CAPTURES.pop("_disk_full", None) is not None if "_disk_full" in CAPTURES else False
            return self._json({"ok": True, "service": NAMA_SERVICE, "versi": VERSI,
                               "test": bool(ARGS.test), "dir": ARGS.dir,
                               "chunk_mb": ARGS.chunk_mb,
                               "disk_free_mb": _dir_free_mb(ARGS.dir),
                               "min_free_mb": ARGS.min_free_mb,
                               "disk_full_terakhir": disk_full,
                               "captures": caps})

        if p == "/files":
            return self._json({"ok": True, "files": _files_list()})

        if p.startswith("/sha256/"):
            rel = p[len("/sha256/"):]
            full = _rel_safe(rel)
            if not full or not os.path.isfile(full):
                return self._json({"ok": False, "error": "file tidak ada"}, 404)
            if rel not in SHA_CACHE:
                SHA_CACHE[rel] = _sha256_of(full)
            return self._json({"ok": True, "sha256": SHA_CACHE[rel], "size": os.path.getsize(full)})

        if p.startswith("/download/"):
            rel = p[len("/download/"):]
            full = _rel_safe(rel)
            if not full or not os.path.isfile(full):
                return self._json({"ok": False, "error": "file tidak ada"}, 404)
            uk = os.path.getsize(full)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(uk))
            self.send_header("X-Name", os.path.basename(full))
            self.end_headers()
            with open(full, "rb") as f:
                shutil.copyfileobj(f, self.wfile, 1024 * 1024)
            return

        self._json({"ok": False, "error": "unknown endpoint: %s" % p}, 404)

    # -------------------------------------------------------------- POST
    def do_POST(self):
        if not self._cek():
            return
        p = urlparse(self.path).path.rstrip("/")
        try:
            body = self._body()
        except Exception as exc:                                # noqa: BLE001
            return self._json({"ok": False, "error": "body is not JSON: %s" % exc}, 400)

        if p == "/capture/start":
            cam = str(body.get("cam", "left"))
            cams = ["left", "right"] if cam == "both" else [cam]
            hasil = {c: capture_start(c, body.get("chunk_mb")) for c in cams}
            return self._json({"ok": all(v.get("ok") for v in hasil.values()), "hasil": hasil})

        if p == "/capture/stop":
            cam = str(body.get("cam", "left"))
            cams = ["left", "right"] if cam == "both" else [cam]
            hasil = {c: capture_stop(c) for c in cams if c in CAPTURES}
            return self._json({"ok": bool(hasil), "hasil": hasil})

        if p == "/delete":
            nama = body.get("names") or []
            hasil = {}
            for rel in nama:
                full = _rel_safe(str(rel))
                if full and os.path.isfile(full):
                    os.remove(full)
                    SHA_CACHE.pop(str(rel), None)
                    hasil[str(rel)] = "dihapus"
                else:
                    hasil[str(rel)] = "tidak ada"
            return self._json({"ok": True, "hasil": hasil})

        self._json({"ok": False, "error": "unknown endpoint: %s" % p}, 404)


# --------------------------------------------------------------------------- #
def main():
    global ARGS
    ap = argparse.ArgumentParser(description="DeepVertex raw capture agent ")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--dir", default=os.path.expanduser("~/raw_captures"),
                    help="directory for the RAW chunk files")
    ap.add_argument("--chunk-mb", type=int, default=1024, help="file chunk size (MB)")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--device-left", default=None, help="e.g. /dev/video0")
    ap.add_argument("--device-right", default=None, help="e.g. /dev/video2")
    ap.add_argument("--device-front", default=None,
                    help="e.g. /dev/video4 (front Low-Light USB camera)")
    ap.add_argument("--front-width", type=int, default=640,
                    help="RAW width for the front camera (USB2 limit)")
    ap.add_argument("--front-height", type=int, default=480,
                    help="RAW height for the front camera (USB2 limit)")
    ap.add_argument("--min-free-mb", type=int, default=3000,
                    help="auto-stop capture when free disk drops below this (MB)")
    ap.add_argument("--token", default=None, help="auth token (optional; X-Token header)")
    ap.add_argument("--test", action="store_true",
                    help="hardware-less test mode: videotestsrc (YUY2)")
    ARGS = ap.parse_args()

    os.makedirs(ARGS.dir, exist_ok=True)
    threading.Thread(target=_disk_guard, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", ARGS.port), Handler)
    print("%s v%s — port %d — dir %s (test=%s, chunk=%d MB, %dx%d@%d)" % (
        NAMA_SERVICE, VERSI, ARGS.port, ARGS.dir, ARGS.test, ARGS.chunk_mb,
        ARGS.width, ARGS.height, ARGS.fps), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for cam in list(CAPTURES.keys()):
            if isinstance(CAPTURES.get(cam), dict):
                capture_stop(cam)


if __name__ == "__main__":
    main()
