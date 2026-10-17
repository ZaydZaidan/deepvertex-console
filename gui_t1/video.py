"""Video H.264 via GStreamer untuk konsol nyata  — display, relai, rekam, snapshot.

Resep yang sudah TERUJI (12 Okt 2026, GStreamer 1.24.2):
  Display (in-proses):  udpsrc port=P caps=<RTP H264> ! rtpjitterbuffer latency=200
    ! tee name=t
      t. ! queue ! rtph264depay ! avdec_h264 ! videoconvert ! video/x-raw,format=RGB
         ! fakesink name=sink sync=false async=false     <- frame diambil via pad probe
      t. ! queue ! valve name=relay drop=true ! udpsink host=127.0.0.1 port=P+100
         sync=false async=false      <- udpsink WAJIB async=false; kalau tidak,
         pipeline macet di PAUSED selama valve tertutup.
  Rekam (subproses `gst-launch-1.0 -q -e`, argv token — BUKAN satu string kutipan):
    udpsrc port=P+100 caps=<RTP H264> ! rtpjitterbuffer latency=120 ! rtph264depay
      ! h264parse ! matroskamux ! filesink location=FILE.mkv   (butuh plugins-bad)
      fallback tanpa h264parse:
      ! video/x-h264,stream-format=byte-stream,alignment=au ! queue ! filesink FILE.h264
    Stop: valve ditutup -> SIGINT ke subproses (-e => EOS => file final).

Catatan: GstApp typelib TIDAK dipakai (tidak tersedia di sistem ini) — frame display
diambil lewat pad probe pada fakesink.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

# PyGObject/GStreamer are Linux-only (field machines). The Windows .EXE runs
# the console WITHOUT preview (gi absent): telemetry + recording (gst-launch
# subprocess, when the GStreamer runtime is installed) stay available.
try:
    import gi  # noqa: N811

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402
    _HAS_GI = True
except Exception:                                      # noqa: BLE001
    _HAS_GI = False

from PySide6 import QtCore, QtGui  # noqa: E402

RTP_CAPS = "application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload=96"

_init = False


def gst_init():
    global _init
    if _HAS_GI and not _init:
        Gst.init(None)
        _init = True


def h264parse_tersedia():
    # Windows (.EXE): PyGObject tidak ada — probe via SUBPROSES runtime
    # GStreamer (gst-inspect-1.0 di PATH) supaya rekaman tetap jujur.
    if not _HAS_GI:
        try:
            return subprocess.run(["gst-inspect-1.0", "h264parse"],
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL,
                                  timeout=5).returncode == 0
        except Exception:                              # noqa: BLE001
            return False
    gst_init()
    return Gst.ElementFactory.find("h264parse") is not None


class StreamPlayerGst(QtCore.QObject):
    """Satu stream kamera: tayang di Qt, relai lokal, bisa direkam/di-snapshot."""

    sig_frame = QtCore.Signal(object, float)      # QImage, fps terakhir
    sig_status = QtCore.Signal(str)
    sig_error = QtCore.Signal(str)
    sig_recording = QtCore.Signal(bool, str)      # aktif, path

    def __init__(self, nama, port, relay_port=None, parent=None):
        super().__init__(parent)
        gst_init()
        self.nama = nama
        self.port = int(port)
        self.relay_port = int(relay_port if relay_port else self.port + 100)
        self.pipeline = None
        self.valve = None
        self._sink = None
        self._bus = None
        self._qimg = None
        self._n = 0
        self._t0 = 0.0
        self._fps = 0.0
        self._rec_proc = None
        self._rec_path = None
        self._rec_container = None
        self._rec_tick = None
        self._bus_timer = None

    # ------------------------------------------------------------------ hidup
    def start(self):
        if not _HAS_GI:
            # Windows (.EXE): tidak ada PyGObject -> preview tidak tersedia.
            self.sig_status.emit(
                f"{self.nama}: preview unavailable (no GStreamer/PyGObject on "
                "this platform \u2014 telemetry & recording still work)")
            return
        if self.pipeline is not None:
            return
        pipe = (
            f"udpsrc port={self.port} caps={RTP_CAPS} ! "
            f"rtpjitterbuffer name=jb latency=200 ! "
            f"tee name=t "
            f"t. ! queue ! rtph264depay ! avdec_h264 ! videoconvert ! "
            f"video/x-raw,format=RGB ! "
            f"fakesink name=sink sync=false async=false "
            f"t. ! queue ! valve name=relay drop=true ! "
            f"udpsink name=relay_out host=127.0.0.1 port={self.relay_port} sync=false async=false"
        )
        self.pipeline = Gst.parse_launch(pipe)
        self.valve = self.pipeline.get_by_name("relay")
        self._sink = self.pipeline.get_by_name("sink")
        self._sink.get_static_pad("sink").add_probe(
            Gst.PadProbeType.BUFFER, self._on_buffer)
        self._bus = self.pipeline.get_bus()
        self.pipeline.set_state(Gst.State.PLAYING)
        if self._bus_timer is None:
            self._bus_timer = QtCore.QTimer(self)
            self._bus_timer.timeout.connect(self._poll_bus)
            self._bus_timer.start(250)
        self.sig_status.emit(f"{self.nama}: started (port {self.port})")

    def stop(self):
        if self._rec_proc is not None:
            self.stop_record()
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            self.valve = None
            self._sink = None
        if self._bus_timer is not None:
            self._bus_timer.stop()
            self._bus_timer = None
        self.sig_status.emit(f"{self.nama}: stopped")

    def restart(self, tunda_ms=1500):
        self.stop()
        self.sig_status.emit(f"{self.nama}: restart dalam {tunda_ms} ms …")
        QtCore.QTimer.singleShot(tunda_ms, self.start)

    # ------------------------------------------------------------- frame datang
    def _on_buffer(self, pad, info, _user=None):
        buf = info.get_buffer()
        ok, mi = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.PadProbeReturn.OK
        img = None
        try:
            caps = pad.get_current_caps()
            st = caps.get_structure(0) if caps is not None else None
            if st is not None and st.get_name() == "video/x-raw":
                w = int(st.get_value("width"))
                h = int(st.get_value("height"))
                bpl = mi.size // h if h else w * 3
                data = mi.data
                if not isinstance(data, (bytes, bytearray)):
                    data = bytes(data)
                img = QtGui.QImage(data, w, h, bpl,
                                   QtGui.QImage.Format.Format_RGB888).copy()
        except Exception as exc:                       # noqa: BLE001
            if not getattr(self, "_err_frame", False):
                self._err_frame = True
                self.sig_error.emit(f"{self.nama}: frame error: {exc}")
        finally:
            buf.unmap(mi)
        if img is not None:
            self._qimg = img
            self._n += 1
            mono = time.monotonic()
            if self._t0 == 0.0:
                self._t0 = mono
            elif mono - self._t0 >= 1.0:
                self._fps = self._n / (mono - self._t0)
                self._n = 0
                self._t0 = mono
            self.sig_frame.emit(img, self._fps)
        return Gst.PadProbeReturn.OK

    def _poll_bus(self):
        if self._bus is None:
            return
        while True:
            msg = self._bus.pop()
            if msg is None:
                return
            if msg.type == Gst.MessageType.ERROR:
                err, dbg = msg.parse_error()
                self.sig_error.emit(f"{self.nama}: {err} ({(dbg or '')[:120]})")
                self.restart()

    # ------------------------------------------------------------------ rekam
    def rekam_bisa(self):
        """(bisa, container) — mkv bila h264parse ada; kalau tidak, .h264 elementer."""
        if h264parse_tersedia():
            return True, "mkv"
        return True, "h264"

    def start_record(self, path):
        if self.pipeline is None:
            return False
        if self._rec_proc is not None:
            return False
        _, container = self.rekam_bisa()
        if container == "mkv" and not path.endswith(".mkv"):
            path += ".mkv"
        if container == "h264" and not path.endswith(".h264"):
            path += ".h264"
        cmd = ["gst-launch-1.0", "-q", "-e",
               "udpsrc", f"port={self.relay_port}", f"caps={RTP_CAPS}", "!",
               "rtpjitterbuffer", "latency=120", "!", "rtph264depay", "!"]
        if container == "mkv":
            cmd += ["h264parse", "!", "matroskamux", "!"]
        else:
            cmd += ["video/x-h264,stream-format=byte-stream,alignment=au", "!", "queue", "!"]
        cmd += ["filesink", f"location={path}"]
        self._rec_proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, text=True)
        self._rec_path = path
        self._rec_container = container
        # beri waktu perekam bind + parse, baru buka keran relai
        QtCore.QTimer.singleShot(500, self._buka_relai)
        self.sig_status.emit(f"{self.nama}: recording started → {os.path.basename(path)}")
        return True

    def _buka_relai(self):
        if self.valve is not None and self._rec_proc is not None:
            self.valve.set_property("drop", False)
        if self._rec_tick is None:
            self._rec_tick = QtCore.QTimer(self)
            self._rec_tick.timeout.connect(self._cek_perekam)
            self._rec_tick.start(500)

    def _cek_perekam(self):
        if self._rec_proc is None:
            if self._rec_tick is not None:
                self._rec_tick.stop()
                self._rec_tick = None
            return
        rc = self._rec_proc.poll()
        if rc is not None:                       # mati mendadak
            err = ""
            try:
                err = (self._rec_proc.stderr.read() or "").strip()[-200:]
            except Exception:                     # noqa: BLE001
                pass
            self.sig_error.emit(f"{self.nama}: recorder stopped unexpectedly (rc={rc}) {err}")
            self._bersihkan_perekam()

    def stop_record(self):
        if self._rec_proc is None:
            return None
        if self.valve is not None:
            self.valve.set_property("drop", True)
        QtCore.QTimer.singleShot(250, self._hentikan_perekam)
        return self._rec_path

    def _hentikan_perekam(self):
        proc = self._rec_proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        path = self._rec_path
        container = self._rec_container
        self._bersihkan_perekam()
        ukuran = os.path.getsize(path) if path and os.path.exists(path) else 0
        ok = ukuran > 1000
        self.sig_recording.emit(False, path if ok else "")
        self.sig_status.emit(
            f"{self.nama}: recording {'saved' if ok else 'FAILED'} "
            f"({container}, {ukuran / 1e6:.1f} MB) → {path}")

    def _bersihkan_perekam(self):
        if self._rec_tick is not None:
            self._rec_tick.stop()
            self._rec_tick = None
        self._rec_proc = None
        self._rec_path = None
        self._rec_container = None

    def is_recording(self):
        return self._rec_proc is not None

    # --------------------------------------------------------------- snapshot
    def snapshot(self, path):
        if self._qimg is None:
            return False
        if not path.lower().endswith((".png", ".jpg", ".jpeg")):
            path += ".png"
        return bool(self._qimg.save(path))


# =========================================================================== #
# Backend ffmpeg — dipakai saat PyGObject/GStreamer tidak ada (Windows .EXE).
# RTP H.264 dari MCM dibaca lewat berkas SDP (recvonly), decode ke MJPEG pada
# pipe stdout (ringan & ukuran frame mengikuti sumber — modular utk kamera apa
# pun), lalu tiap frame JPEG menjadi QImage. Rekam = ffmpeg kedua (-c copy,
# matroska) yang membaca RELAI lokal; forwarder UDP meneruskan tiap datagram
# dari port utama ke port preview + port perekam (pola tee milik jalur gst).
# =========================================================================== #
_FF = None          # cache path ffmpeg (False = sudah dicari, tak ada)


def ffmpeg_path():
    """Path ffmpeg yang tersedia: env DV_FFMPEG, PATH, folder app, tools data."""
    global _FF
    if _FF is not None:
        return _FF or None
    cands = []
    e = os.environ.get("DV_FFMPEG")
    if e:
        cands.append(e)
    try:
        w = shutil.which("ffmpeg")
        if w:
            cands.append(w)
    except Exception:                                        # noqa: BLE001
        pass
    base = os.path.dirname(os.path.abspath(__file__))
    cands += [os.path.join(base, "ffmpeg.exe"), os.path.join(base, "ffmpeg")]
    # APLIKASI TERPASANG (one-file): ffmpeg.exe ada di sebelah EXE, bukan di
    # folder bundle sementara (_MEI...) — laporan audit Windows 17 Okt.
    try:
        d_exe = os.path.dirname(os.path.abspath(sys.executable))
        cands += [os.path.join(d_exe, "ffmpeg.exe"),
                  os.path.join(d_exe, "ffmpeg")]
    except Exception:                                        # noqa: BLE001
        pass
    try:
        import paths
        d = paths.data_dir()
        cands += [os.path.join(d, "tools", "ffmpeg.exe"),
                  os.path.join(d, "tools", "ffmpeg")]
    except Exception:                                        # noqa: BLE001
        pass
    for c in cands:
        try:
            if c and os.path.isfile(c):
                _FF = c
                return c
        except Exception:                                    # noqa: BLE001
            pass
    _FF = False
    return None


def sdp_untuk(port, payload=96):
    """Isi berkas SDP recvonly untuk RTP H.264 pada port tertentu (murni)."""
    return ("v=0\no=- 0 0 IN IP4 127.0.0.1\ns=DeepVertex\n"
            "c=IN IP4 127.0.0.1\nt=0 0\n"
            "m=video %d RTP/AVP %d\n"
            "a=rtpmap:%d H264/90000\na=recvonly\n" % (int(port), int(payload),
                                                        int(payload)))


def baca_mjpeg(buf):
    """Pisahkan frame JPEG dari buffer streaming (murni).

    Mengembalikan (daftar_frame_jpeg, sisa_buffer). Penanda SOI FFD8 ... EOI FFD9.
    """
    frames = []
    data = bytes(buf)
    pos = 0
    while True:
        a = data.find(b"\xff\xd8", pos)
        if a < 0:
            # simpan ekor 1 byte (bisa jadi awal FFD8 terbelah)
            sisa = data[-1:] if data else b""
            return frames, bytearray(sisa)
        b = data.find(b"\xff\xd9", a + 2)
        if b < 0:
            return frames, bytearray(data[a:])
        frames.append(data[a:b + 2])
        pos = b + 2


class StreamPlayerFf(QtCore.QObject):
    """Stream kamera via ffmpeg (Windows/Linux tanpa GStreamer). API sama."""

    sig_frame = QtCore.Signal(object, float)
    sig_status = QtCore.Signal(str)
    sig_error = QtCore.Signal(str)
    sig_recording = QtCore.Signal(bool, str)

    def __init__(self, nama, port, relay_port=None, parent=None):
        super().__init__(parent)
        self.nama = nama
        self.port = int(port)
        self.relay_port = int(relay_port if relay_port else self.port + 100)
        self._proc = None
        self._rec = None
        self._rec_path = None
        self._stop = threading.Event()
        self._t_reader = None
        self._t_forward = None
        self._sock = None
        self._forward_aktif = False       # teruskan juga ke port perekam?
        self._qimg = None
        self._n = 0
        self._t0 = 0.0
        self._fps = 0.0
        self._err = []
        self._timer_cek = None

    # ------------------------------------------------------------- internal
    def _sdp(self, port):
        p = os.path.join(tempfile.gettempdir(), "dv_rtp_%d.sdp" % int(port))
        try:
            with open(p, "w", encoding="ascii") as f:
                f.write(sdp_untuk(port))
        except OSError:
            pass
        return p

    def _cmd_preview(self):
        ff = ffmpeg_path()
        # v1.1.4 (laporan glitch pemilik): tanpa nobuffer/low_delay, jitter
        # RTP ditahan max_delay, kualitas MJPEG dinaikkan (q3, bicubic).
        return [ff, "-hide_banner", "-loglevel", "error",
                "-protocol_whitelist", "file,udp,rtp",
                "-max_delay", "300000", "-reorder_queue_size", "500",
                "-i", self._sdp(self.relay_port), "-an",
                "-sws_flags", "bicubic",
                "-vf", "scale=min(1280\\,iw):-2",
                "-f", "mjpeg", "-q:v", "3", "pipe:1"]

    def _forward_loop(self):
        """Bind port utama; teruskan datagram ke preview (+ recorder)."""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", self.port))
            s.settimeout(0.5)
            self._sock = s
        except OSError as exc:
            self.sig_error.emit("%s: port %d bind gagal: %s"
                                % (self.nama, self.port, exc))
            return
        while not self._stop.is_set():
            try:
                d, _a = s.recvfrom(65536)
            except Exception:                                # noqa: BLE001
                continue
            for tujuan in ([self.relay_port]
                           + ([self.port + 200] if self._forward_aktif else [])):
                try:
                    s.sendto(d, ("127.0.0.1", int(tujuan)))
                except Exception:                            # noqa: BLE001
                    pass
        try:
            s.close()
        except Exception:                                    # noqa: BLE001
            pass

    def _reader_loop(self, proc):
        buf = bytearray()
        try:
            fd = proc.stdout
            while not self._stop.is_set():
                blok = fd.read(65536)
                if not blok:
                    break
                buf += blok
                frames, buf = baca_mjpeg(buf)
                for jp in frames:
                    img = QtGui.QImage()
                    if not img.loadFromData(jp, "JPG"):
                        continue
                    self._qimg = img
                    self._n += 1
                    mono = time.monotonic()
                    if self._t0 == 0.0:
                        self._t0 = mono
                    elif mono - self._t0 >= 1.0:
                        self._fps = self._n / (mono - self._t0)
                        self._n = 0
                        self._t0 = mono
                    self.sig_frame.emit(img, self._fps)
        except Exception as exc:                             # noqa: BLE001
            if not self._stop.is_set():
                self.sig_error.emit("%s: reader error: %r" % (self.nama, exc))

    def _err_loop(self, proc):
        try:
            for baris in proc.stderr:
                self._err = (self._err + [baris.rstrip()])[-40:]
        except Exception:                                    # noqa: BLE001
            pass

    # ----------------------------------------------------------------- hidup
    def start(self):
        if self._proc is not None:
            return
        ff = ffmpeg_path()
        if not ff:
            self.sig_status.emit(
                "%s: preview unavailable (ffmpeg not found \u2014 put ffmpeg.exe "
                "next to the app or in %s/tools)" % (self.nama, "data"))
            return
        self._stop.clear()
        self._t_forward = threading.Thread(target=self._forward_loop, daemon=True)
        self._t_forward.start()
        try:
            self._proc = subprocess.Popen(
                self._cmd_preview(), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0)
        except Exception as exc:                             # noqa: BLE001
            self.sig_error.emit("%s: ffmpeg gagal start: %r" % (self.nama, exc))
            self._proc = None
            return
        self._t_reader = threading.Thread(target=self._reader_loop,
                                          args=(self._proc,), daemon=True)
        self._t_reader.start()
        threading.Thread(target=self._err_loop, args=(self._proc,),
                         daemon=True).start()
        if self._timer_cek is None:
            self._timer_cek = QtCore.QTimer(self)
            self._timer_cek.timeout.connect(self._cek_proc)
            self._timer_cek.start(1000)
        self.sig_status.emit("%s: started (ffmpeg, port %d)" % (self.nama,
                                                                self.port))

    def _cek_proc(self):
        if self._proc is None:
            return
        rc = self._proc.poll()
        if rc is not None and not self._stop.is_set():
            err = "\n".join(self._err[-3:])[-200:]
            self.sig_error.emit("%s: ffmpeg exit %s %s (restart)" %
                                (self.nama, rc, err))
            self.stop()
            QtCore.QTimer.singleShot(2000, self.start)

    def stop(self):
        if self._rec is not None:
            self.stop_record()
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:                                # noqa: BLE001
                try:
                    self._proc.kill()
                except Exception:                            # noqa: BLE001
                    pass
            self._proc = None
        if self._timer_cek is not None:
            self._timer_cek.stop()
            self._timer_cek = None
        self.sig_status.emit("%s: stopped" % self.nama)

    def restart(self, tunda_ms=1500):
        self.stop()
        QtCore.QTimer.singleShot(tunda_ms, self.start)

    # ------------------------------------------------------------------ rekam
    def rekam_bisa(self):
        return True, "mkv"

    def start_record(self, path):
        if self._proc is None or self._rec is not None:
            return False
        ff = ffmpeg_path()
        if not path.endswith(".mkv"):
            path += ".mkv"
        cmd = [ff, "-hide_banner", "-loglevel", "error",
               "-protocol_whitelist", "file,udp,rtp",
               "-i", self._sdp(self.port + 200), "-c", "copy",
               "-f", "matroska", path]
        try:
            kw = {}
            if os.name == "nt":
                kw["creationflags"] = getattr(subprocess,
                                              "CREATE_NEW_PROCESS_GROUP", 0)
            self._rec = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, text=True,
                                         **kw)
        except Exception as exc:                             # noqa: BLE001
            self.sig_error.emit("%s: recorder gagal start: %r" % (self.nama, exc))
            self._rec = None
            return False
        self._rec_path = path
        self._forward_aktif = True
        self.sig_status.emit("%s: recording started \u2192 %s"
                             % (self.nama, os.path.basename(path)))
        return True

    def stop_record(self):
        if self._rec is None:
            return None
        self._forward_aktif = False
        proc, path = self._rec, self._rec_path
        self._rec = None
        try:
            if proc.poll() is None:
                if os.name == "nt":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        except Exception:                                    # noqa: BLE001
            pass
        ukuran = os.path.getsize(path) if path and os.path.exists(path) else 0
        ok = ukuran > 1000
        self.sig_recording.emit(False, path if ok else "")
        self.sig_status.emit("%s: recording %s (%s, %.1f MB)"
                             % (self.nama, "saved" if ok else "FAILED",
                                "mkv", ukuran / 1e6))
        return path

    def is_recording(self):
        return self._rec is not None

    # --------------------------------------------------------------- snapshot
    def snapshot(self, path):
        if self._qimg is None:
            return False
        if not path.lower().endswith((".png", ".jpg", ".jpeg")):
            path += ".png"
        return bool(self._qimg.save(path))


def StreamPlayer(nama, port, relay_port=None, parent=None):
    """Factory: GStreamer bila tersedia; selain itu ffmpeg; selain itu jalur Gst
    (yang akan memberi pesan jelas bahwa preview tidak tersedia)."""
    if _HAS_GI:
        return StreamPlayerGst(nama, port, relay_port, parent)
    if ffmpeg_path():
        return StreamPlayerFf(nama, port, relay_port, parent)
    return StreamPlayerGst(nama, port, relay_port, parent)
