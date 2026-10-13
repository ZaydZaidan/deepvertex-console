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
import signal
import subprocess
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from PySide6 import QtCore, QtGui  # noqa: E402

RTP_CAPS = "application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload=96"

_init = False


def gst_init():
    global _init
    if not _init:
        Gst.init(None)
        _init = True


def h264parse_tersedia():
    gst_init()
    return Gst.ElementFactory.find("h264parse") is not None


class StreamPlayer(QtCore.QObject):
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
