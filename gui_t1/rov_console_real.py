#!/usr/bin/env python3
"""Konsol Misi ROV — TAHAP 1 (mode ROV, ROV asli via BlueOS).

  - Video  : 3 stream H.264 via GStreamer (depan + stereo L/R dari BlueOS camera manager)
  - MAVLink: telemetri + link ke BlueOS (pola GCS; heartbeat 1 Hz)
  - Capture: rekam compressed di topside + snapshot; RAW onboard via agen DeepVertex
  - Terkunci: AUTONOMOUS dan VIO (Tahap 2 — mode AUV)

Jalankan: ./run_console_real.sh [--mavlink udpin:0.0.0.0:14550] [--outdir ...]
"""
import argparse
import json
import os
import shutil
import sys
import time

from PySide6 import QtCore, QtWidgets

import theme
import widgets as W
from maviface import MavBridge
from rawcap import RawPanel
from video import StreamPlayer, h264parse_tersedia

STREAM_DEF = (
    # nama, judul panel, default port
    ("front", "CAM · FRONT (PILOT)", 5600),
    ("stereo L", "CAM · STEREO L", 5601),
    ("stereo R", "CAM · STEREO R", 5602),
)


def _buat_dir_sesi(root):
    os.makedirs(root, exist_ok=True)
    d = os.path.join(root, time.strftime("%Y%m%d_%H%M%S") + "_dive")
    os.makedirs(d, exist_ok=True)
    return d


class RovConsole(QtWidgets.QMainWindow):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.setWindowTitle("DeepVertex · ROV Mission Console — TAHAP 1 (mode ROV)")
        self.resize(1560, 920)
        self.sesi = _buat_dir_sesi(os.path.expanduser(args.outdir))
        self.last = {}                     # state MAVLink terakhir (dipakai uji)

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        v = QtWidgets.QVBoxLayout(root)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(6)
        v.addWidget(self._header())
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(6)
        v.addLayout(body, 1)
        self._bangun_kolom(body)
        self.lbl_status = QtWidgets.QLabel("siap — menunggu stream & telemetri …")
        self.lbl_status.setObjectName("MicroLabel")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

        # ---------------- video ----------------
        self.players = {}
        for nama, judul, port in STREAM_DEF:
            port = getattr(args, "port_" + nama.replace(" ", "_"), port)
            p = StreamPlayer(nama, port)
            p.sig_frame.connect(self.panels_cam[nama].tile.set_qimage)
            p.sig_status.connect(self._catat)
            p.sig_error.connect(self._catat)
            p.sig_recording.connect(self._rec_selesai)
            self.players[nama] = p

        # ---------------- MAVLink ----------------
        self.bridge = MavBridge(args.mavlink)
        self.bridge.sig_vehicle.connect(self._vehicle)
        self.bridge.sig_attitude.connect(self._attitude)
        self.bridge.sig_text.connect(self._text)
        self.bridge.sig_status.connect(self._catat)
        self.bridge.start()

        for p in self.players.values():
            p.start()

        with open(os.path.join(self.sesi, "session.json"), "w", encoding="utf-8") as f:
            json.dump({"mulai": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "mavlink": args.mavlink,
                       "streams": {n: getattr(args, "port_" + n.replace(" ", "_"), d)
                                   for n, _j, d in STREAM_DEF},
                       "raw_agent": args.raw_agent,
                       "h264parse": h264parse_tersedia(),
                       "versi": "T1-0.1"}, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ UI
    def _header(self):
        h = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(h)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(8)
        t = QtWidgets.QLabel("DEEPVERTEX")
        t.setObjectName("PanelTitle")
        lay.addWidget(t)
        s = QtWidgets.QLabel("ROV MISSION CONSOLE · TAHAP 1 · MODE ROV")
        s.setObjectName("MicroLabel")
        lay.addWidget(s)
        lay.addStretch(1)
        self.chip_link = W.Chip("LINK")
        self.chip_mode = W.Chip("MODE")
        self.chip_armed = W.Chip("ARM")
        for c, d in ((self.chip_link, "—"), (self.chip_mode, "—"), (self.chip_armed, "—")):
            self._chip(c, "ink_faint", d)
            lay.addWidget(c)
        self.lbl_jam = QtWidgets.QLabel("--:--:--")
        self.lbl_jam.setObjectName("MicroLabel")
        lay.addWidget(self.lbl_jam)
        tm = QtCore.QTimer(self)
        tm.timeout.connect(lambda: self.lbl_jam.setText(time.strftime("%H:%M:%S")))
        tm.start(1000)
        return h

    def _chip(self, chip, warna, teks):
        try:
            chip.set_state(warna, teks)
        except Exception:                                  # noqa: BLE001
            try:
                chip.set_state("ink_faint", teks)
            except Exception:                              # noqa: BLE001
                pass

    def _bangun_kolom(self, body):
        # ---------------- kiri: kamera ----------------
        kiri = QtWidgets.QVBoxLayout()
        kiri.setSpacing(6)
        self.panels_cam = {}
        for nama, judul, _port in STREAM_DEF:
            tinggi = 280 if nama == "front" else 230
            p = W.Panel(judul, "H.264 · BlueOS", domain="field")
            tile = W.CameraTile(nama, judul=False)
            tile.tinggi_maks = tinggi
            tile.set_status("menunggu stream …")
            p.body_layout.addWidget(tile, 1)
            p.tile = tile                      # dipakai wiring sig_frame → set_qimage
            self.panels_cam[nama] = p
            kiri.addWidget(p)
        kiri.addStretch(1)
        body.addLayout(kiri, 2)

        # ---------------- tengah: instrumen ----------------
        tengah = QtWidgets.QVBoxLayout()
        tengah.setSpacing(6)

        pa = W.Panel("ATTITUDE & DEPTH", "roll/pitch · depth", domain="neutral")
        self.horizon = W.Horizon()
        pa.body_layout.addWidget(self.horizon, 1)
        baris = QtWidgets.QHBoxLayout()
        baris.setSpacing(12)
        self.ro_depth = W.Readout("DEPTH", "m", size=18, peran="accent")
        self.ro_head = W.Readout("HEADING", "°", size=18)
        self.ro_temp = W.Readout("TEMP", "°C", size=14)
        for r in (self.ro_depth, self.ro_head, self.ro_temp):
            baris.addWidget(r)
        baris.addStretch(1)
        pa.body_layout.addLayout(baris)
        tengah.addWidget(pa)

        pv = W.Panel("VEHICLE (PILOT)", "mode · arm — uji kolam dulu", domain="mission")
        rowv = QtWidgets.QHBoxLayout()
        rowv.setSpacing(4)
        self.cb_mode = QtWidgets.QComboBox()
        self.cb_mode.addItems(("MANUAL", "STABILIZE", "ALT_HOLD", "SURFACE"))
        self.cb_mode.setToolTip("mode ArduSub aktif T1 (mode berbasis posisi/otonom = coming soon)")
        b_go = QtWidgets.QPushButton("SET")
        b_go.setObjectName("BtnTiny")
        b_go.setToolTip("kirim ganti mode ke autopilot")
        b_go.clicked.connect(lambda: self._buka_mode(self.cb_mode.currentText()))
        b_arm = QtWidgets.QPushButton("ARM")
        b_arm.setObjectName("BtnTiny")
        b_arm.setToolTip("ARM — uji kolam dulu, pastikan propeller aman")
        b_arm.clicked.connect(lambda: self._arm(True))
        b_dis = QtWidgets.QPushButton("DISARM")
        b_dis.setObjectName("BtnTiny")
        b_dis.clicked.connect(lambda: self._arm(False))
        b_surf = QtWidgets.QPushButton("SURFACE")
        b_surf.setObjectName("BtnTiny")
        b_surf.setToolTip("mode SURFACE — bawa ROV ke permukaan (aksi darurat)")
        b_surf.clicked.connect(lambda: self._buka_mode("SURFACE"))
        for wdg in (self.cb_mode, b_go, b_arm, b_dis, b_surf):
            rowv.addWidget(wdg)
        rowv.addStretch(1)
        pv.body_layout.addLayout(rowv)
        tengah.addWidget(pv)

        pp = W.Panel("POWER & SYSTEM", "baterai · link", domain="neutral")
        baris2 = QtWidgets.QHBoxLayout()
        baris2.setSpacing(12)
        self.ro_volt = W.Readout("VOLT", "V", size=16)
        self.ro_curr = W.Readout("CURRENT", "A", size=16)
        self.ro_batt = W.Readout("BATTERY", "%", size=16)
        for r in (self.ro_volt, self.ro_curr, self.ro_batt):
            baris2.addWidget(r)
        baris2.addStretch(1)
        pp.body_layout.addLayout(baris2)
        tengah.addWidget(pp)

        pl = W.Panel("NOTIFICATIONS", "STATUSTEXT + status", domain="neutral")
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        pl.body_layout.addWidget(self.log, 1)
        tengah.addWidget(pl, 1)
        body.addLayout(tengah, 2)

        # ---------------- kanan: capture + raw + coming soon ----------------
        kanan = QtWidgets.QVBoxLayout()
        kanan.setSpacing(6)

        pc = W.Panel("CAPTURE", "rekam compressed · snapshot", domain="mission")
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(4)
        self.btn_rec = {}
        self.btn_snap = {}
        for i, (nama, _j, _p) in enumerate(STREAM_DEF):
            lab = QtWidgets.QLabel(nama.upper())
            lab.setObjectName("MicroLabel")
            b1 = QtWidgets.QPushButton("● REC")
            b1.setObjectName("BtnTiny")
            b1.setCheckable(True)
            b1.setToolTip("mulai/hentikan rekaman stream %s (simpan: %s)" % (nama, self.sesi))
            b1.toggled.connect(lambda on, n=nama: self._toggle_rec(n, on))
            b2 = QtWidgets.QPushButton("SNAP")
            b2.setObjectName("BtnTiny")
            b2.setToolTip("simpan frame terakhir %s sebagai PNG" % nama)
            b2.clicked.connect(lambda _, n=nama: self._snap(n))
            grid.addWidget(lab, i, 0)
            grid.addWidget(b1, i, 1)
            grid.addWidget(b2, i, 2)
            self.btn_rec[nama] = b1
            self.btn_snap[nama] = b2
        pc.body_layout.addLayout(grid)
        self.lbl_sesi = QtWidgets.QLabel("sesi: " + os.path.basename(self.sesi))
        self.lbl_sesi.setObjectName("Faint")
        self.lbl_sesi.setWordWrap(True)
        pc.body_layout.addWidget(self.lbl_sesi)
        self.lbl_disk = QtWidgets.QLabel("disk topside: —")
        self.lbl_disk.setObjectName("Faint")
        pc.body_layout.addWidget(self.lbl_disk)
        td = QtCore.QTimer(self)
        td.timeout.connect(self._update_disk)
        td.start(5000)
        self._update_disk()
        kanan.addWidget(pc)

        pr = W.Panel("RAW (ONBOARD)", "chunk YUYV · transfer + verifikasi", domain="mission")
        self.rawpanel = RawPanel(self.args.raw_agent, outdir=self.sesi)
        pr.body_layout.addWidget(self.rawpanel)
        kanan.addWidget(pr, 1)

        pk = W.Panel("AUTONOMOUS · VIO", "TAHAP 2 — mode AUV (coming soon)", domain="mission")
        pk.body_layout.addWidget(W.Placeholder(
            "COMING SOON",
            "Autonomous, VIO, dan pemetaan berjalan pada Tahap 2 (mode AUV, Jetson Orin Nano). "
            "TAHAP 1 hanya mode ROV: pilot manual + capture."))
        kanan.addWidget(pk, 1)
        body.addLayout(kanan, 2)

    # ------------------------------------------------------------------ aksi
    def _toggle_rec(self, nama, on):
        p = self.players[nama]
        if on:
            path = os.path.join(self.sesi, "%s_%s" % (nama.replace(" ", "_"),
                                                      time.strftime("%H%M%S")))
            if not p.start_record(path):
                self.btn_rec[nama].setChecked(False)
        else:
            p.stop_record()

    # ------------------------------------------------- kendali kendaraan ---------
    def _arm(self, on):
        self.bridge.queue_arm(bool(on))
        self._catat("perintah %s dikirim (uji kolam dulu)" % ("ARM" if on else "DISARM"))

    def _buka_mode(self, nama):
        angka = {"MANUAL": 19, "STABILIZE": 0, "ALT_HOLD": 2, "SURFACE": 9}.get(nama)
        if angka is None:
            return
        self.bridge.queue_mode(angka)
        self._catat("perintah mode %s (%d) dikirim" % (nama, angka))

    def _snap(self, nama):
        path = os.path.join(self.sesi, "snap_%s_%s.png" % (
            nama.replace(" ", "_"), time.strftime("%H%M%S_") + "%03d" % (int(time.time() * 1000) % 1000)))
        ok = self.players[nama].snapshot(path)
        self._catat("snapshot %s → %s" % (nama, path if ok else "GAGAL (belum ada frame)"))

    def _rec_selesai(self, aktif, path):
        if not aktif and path:
            self._catat("rekaman tersimpan: " + path)

    def _update_disk(self):
        try:
            du = shutil.disk_usage(self.sesi)
            self.lbl_disk.setText("disk topside: %.1f GB bebas" % (du.free / 1e9))
        except OSError:
            pass

    # ------------------------------------------------------------------ MAVLink
    def _vehicle(self, s):
        self.last = dict(s)
        try:
            if s.get("link"):
                self._chip(self.chip_link, "ok", "terhubung")
            else:
                self._chip(self.chip_link, "warn", "menunggu")
            self._chip(self.chip_mode, "ink_faint", str(s.get("mode") or "—"))
            if s.get("armed"):
                self._chip(self.chip_armed, "warn", "ARMED")
            else:
                self._chip(self.chip_armed, "ok", "disarmed")
            self.ro_depth.set_value(s.get("depth"), "{:.2f}")
            self.ro_head.set_value(s.get("heading"), "{:.0f}")
            self.ro_temp.set_value(s.get("temp"), "{:.1f}")
            self.ro_volt.set_value(s.get("volt"), "{:.2f}")
            self.ro_curr.set_value(s.get("curr"), "{:.2f}")
            self.ro_batt.set_value(s.get("remaining"), "{:.0f}")
        except Exception:                                  # noqa: BLE001
            pass

    def _attitude(self, roll, pitch, _yaw):
        self.horizon.set_attitude(roll, pitch)

    def _text(self, teks, sev):
        self._catat(("⚠ " if sev <= 4 else "") + teks)

    def _catat(self, teks):
        try:
            self.lbl_status.setText(teks)
            self.log.appendPlainText(time.strftime("[%H:%M:%S] ") + str(teks))
        except Exception:                                  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ tutup
    def closeEvent(self, ev):
        for p in getattr(self, "players", {}).values():
            try:
                p.stop()
            except Exception:                              # noqa: BLE001
                pass
        try:
            self.bridge.stop()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            with open(os.path.join(self.sesi, "session_end.json"), "w", encoding="utf-8") as f:
                json.dump({"selesai": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        except OSError:
            pass
        super().closeEvent(ev)


def _terapkan_tema(app):
    """Terapkan font + QSS tema (API: theme.muat_font(), theme.apply(nama, app))."""
    try:
        theme.muat_font()
    except Exception:                                  # noqa: BLE001
        pass
    try:
        theme.apply(theme.aktif(), app)
    except Exception:                                  # noqa: BLE001
        pass


def main():
    ap = argparse.ArgumentParser(description="Konsol Misi ROV — Tahap 1 (mode ROV)")
    ap.add_argument("--mavlink", default="udpin:0.0.0.0:14550",
                    help="endpoint MAVLink (mis. udpin:0.0.0.0:14550 / udp:192.168.2.2:14550)")
    ap.add_argument("--front-port", type=int, default=5600)
    ap.add_argument("--left-port", type=int, default=5601)
    ap.add_argument("--right-port", type=int, default=5602)
    ap.add_argument("--raw-agent", default="http://127.0.0.1:8765")
    ap.add_argument("--outdir", default=os.path.expanduser("~/DeepVertex-ROV-Captures"))
    ap.add_argument("--shot", default=None, help="simpan screenshot jendela ke PATH (uji tampilan)")
    ap.add_argument("--shot-after", type=float, default=6.0, help="detik sebelum screenshot")
    # nama atribut dipakai konsol: port_front / port_stereo_L / port_stereo_R
    args = ap.parse_args()
    args.port_front = args.front_port
    args.port_stereo_L = args.left_port
    args.port_stereo_R = args.right_port

    app = QtWidgets.QApplication(sys.argv)
    _terapkan_tema(app)
    w = RovConsole(args)
    w.show()
    if args.shot:
        def _ambil_shot():
            ok = w.grab().save(args.shot)
            print("shot:", args.shot, "ok" if ok else "GAGAL", flush=True)
        QtCore.QTimer.singleShot(int(args.shot_after * 1000), _ambil_shot)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
