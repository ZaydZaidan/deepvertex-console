#!/usr/bin/env python3
"""Konsol Misi ROV — TAHAP 1 (mode ROV, BlueROV2 via BlueOS) — konsol lengkap.

TAMPILAN: kembar konsol sim (rov_dashboard.py) — komponen sama (Segmen/FlowBox/
StatusBar di ui_common.py), rel kiri & kanan yang scroll, kolom tengah + baris
bawah, menu bar, chip status. Preset: Pilot Focus / Setup & Preflight / Capture.

FUNGSI (gaya QGC): teleop gamepad (MANUAL_CONTROL + deadman), ARM/DISARM/mode,
3 stream kamera + rekam/snapshot, RAW onboard (agen), KALIBRASI (gyro/accel/
compass/level/baro), MOTOR TEST, FAILSAFE+BATERAI, browser PARAMETER,
PRE-FLIGHT CHECK (status hidup + checklist pre-dive resmi Blue Robotics).

Jalankan: ./run_console_real.sh [opsi]   (lihat --help)
"""
import argparse
import json
import math
import os
import sys
import time

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

import theme
import ui_common as U
import widgets as W
import vehicleops as VO
from gamepad import Gamepad
from maviface import MavBridge
from peta_misi import PlotMisi
import padmap
from peta_panel import PetaPanel
from rawcap import RawPanel
from video import StreamPlayer, h264parse_tersedia

STREAM_DEF = (
    ("front", "CAM · FRONT (PILOT)", 5600),
    ("stereo L", "CAM · STEREO L", 5601),
    ("stereo R", "CAM · STEREO R", 5602),
)
MODE_LIST = (("MANUAL", VO.MODE_MANUAL), ("STABILIZE", VO.MODE_STABILIZE),
             ("ALT_HOLD (depth hold)", VO.MODE_ALT_HOLD), ("SURFACE", VO.MODE_SURFACE))


def _buat_dir_sesi(root):
    os.makedirs(root, exist_ok=True)
    d = os.path.join(root, time.strftime("%Y%m%d_%H%M%S") + "_dive")
    os.makedirs(d, exist_ok=True)
    return d


class RovConsole(QtWidgets.QMainWindow):
    PRESET_BAWAAN = "pilot-focus"
    NAMA_PRESET = {"pilot-focus": "Pilot Focus", "mapping": "Mapping",
                   "setup": "Setup", "capture": "Capture"}
    SPESIFIKASI = {
        "pilot-focus": {
            "lebar_kiri": 350,
            # urutan PRIORITAS (audit 13 Okt): kontrol & baterai di atas; checklist
            # (sekunder) di bawah — rel bisa discroll.
            "left": ["flight", "vehicle", "power", "instruments", "preflight"],
            # kamera TANPA stretch: panel mengunci pas rasio frame (gambar penuh)
            "mid": [("cam_front", 0), ("peta", 1)],
            # sekunder turun: notifikasi BUKAN di atas; capture & teleop yang penting
            "right": ["capture", "teleop", "cam_left", "cam_right", "raw",
                      "notif", "soon"],
        },
        "mapping": {
            "lebar_kiri": 350,
            "left": ["flight", "instruments", "power"],
            "mid": [("peta", 1), "notif"],
            "right": ["capture", "raw", "preflight"],
        },
        "setup": {
            "lebar_kiri": 350,
            # panduan awam paling atas (langkah 1-7), lalu panel teknis
            "left": ["guide", "setup", "motors", "failsafe"],
            "mid": ["params", "notif"],
            "right": ["preflight", "vehicle", "power"],
        },
        "capture": {
            "lebar_kiri": 350,
            "left": ["flight", "power", "preflight"],
            "mid": [("cam_front", 0), ("peta", 1)],
            "right": ["capture", "teleop", "cam_left", "cam_right", "raw",
                      "notif"],
        },
    }

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.setWindowTitle("DeepVertex · ROV Mission Console — TAHAP 1 (mode ROV)")
        self.resize(1620, 1000)
        self.sesi = _buat_dir_sesi(os.path.expanduser(args.outdir))
        self.last = {}
        self._preset = args.preset if args.preset in self.SPESIFIKASI else self.PRESET_BAWAAN
        self._tilt = 1500
        self.vis = {}
        self.P = {}

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        v = QtWidgets.QVBoxLayout(root)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(6)
        v.addWidget(self._build_header())
        v.addWidget(self._buat_strip_alert())
        body = QtWidgets.QHBoxLayout()
        body.setSpacing(6)
        v.addLayout(body, 1)
        self._bangun_kolom(body)
        self.sb = U.StatusBar()
        v.addWidget(self.sb)
        self._bangun_statusbar()

        # ---------------- MAVLink (dibuat sebelum panel: panel2 mengikat sinyalnya)
        self.bridge = MavBridge(args.mavlink)
        _tlog = getattr(args, "tlog", "auto")
        if _tlog != "off":
            self.bridge.tlog_path = (os.path.join(self.sesi, "mavlink.tlog")
                                     if _tlog == "auto" else _tlog)
        self.bridge.sig_vehicle.connect(self._vehicle)
        self.bridge.sig_attitude.connect(self._attitude)
        self.bridge.sig_text.connect(self._text)
        self.bridge.sig_status.connect(self._catat)

        self._build_panels()
        self._build_menubar()

        # ---------------- layanan ----------------
        self.players = {}
        for nama, _judul, port in STREAM_DEF:
            port = getattr(args, "port_" + nama.replace(" ", "_"), port)
            p = StreamPlayer(nama, port)
            key = {"front": "cam_front", "stereo L": "cam_left",
                   "stereo R": "cam_right"}[nama]
            p.sig_frame.connect(self.P[key].tile.set_qimage)
            p.sig_status.connect(self._catat)
            p.sig_error.connect(self._catat)
            p.sig_recording.connect(self._rec_selesai)
            self.players[nama] = p

        self.gamepad = Gamepad(self.bridge)
        self._rc_keep = {}          # kanal RC override yang harus dipertahankan (tanpa pad)
        self._center_aktif = False  # routine RESET tilt (kembali ke 50%) berjalan?
        self._perlu_center_awal = True   # auto-reset tilt di awal sesi (permintaan pemilik)
        self._rc_timer = QtCore.QTimer(self)
        self._rc_timer.timeout.connect(self._rc_tick)
        self._rc_timer.start(1500)
        self._pad_timer = QtCore.QTimer(self)     # hotplug: pad dicolok belakangan
        self._pad_timer.timeout.connect(self._pad_coba)
        self._pad_timer.start(3000)
        self._tiles_timer = QtCore.QTimer(self)   # jaga rasio tile kamera selalu pas
        self._tiles_timer.timeout.connect(self._sync_tiles)
        self._tiles_timer.start(600)
        self.gamepad.sig_status.connect(self._catat)
        self.gamepad.sig_live.connect(self._teleop_update)

        # integrator peta (estimasi dead-reckoning; label jelas di panel)
        self.plot_misi.ancor()
        self._peta_t = time.monotonic()
        self._peta_trail_t = 0.0
        self._peta_timer = QtCore.QTimer(self)
        self._peta_timer.timeout.connect(self._peta_tick)
        self._peta_timer.start(200)

        self.rawpanel = self.P["raw"].panel_raw
        self.pf = self.P["preflight"].panel_preflight
        self.pf.sumber = {"players": self.players, "gamepad": self.gamepad,
                          "rawpanel": self.rawpanel, "sesi": self.sesi}
        self.pf.muat_ck()
        def _raw_pesan(tag, d):
            if tag == "status":
                self._catat("RAW agen: %s" % ("OK, disk %s MB" % d.get("disk_free_mb")
                                              if d.get("ok") else str(d.get("error"))[:80]))
            elif tag == "files":
                self._catat("RAW: %d chunk" % len(d.get("files", [])))
            else:
                self._catat("RAW %s: %s" % (tag, str(d)[:80]))
        self.rawpanel.client.sig_result.connect(_raw_pesan)

        self.bridge.start()
        for p in self.players.values():
            p.start()
        if not args.no_pad:
            self.gamepad.start()

        self._timer_sb = QtCore.QTimer(self)
        self._timer_sb.timeout.connect(self._perbarui_statusbar)
        self._timer_sb.start(1000)

        with open(os.path.join(self.sesi, "session.json"), "w", encoding="utf-8") as f:
            json.dump({"mulai": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "mavlink": args.mavlink,
                       "streams": {n: getattr(args, "port_" + n.replace(" ", "_"), d)
                                   for n, _j, d in STREAM_DEF},
                       "raw_agent": args.raw_agent,
                       "h264parse": h264parse_tersedia(), "versi": "T1-0.2"}, f,
                      indent=2, ensure_ascii=False)
        self._apply_preset(self._preset)

    # ================================================================ header
    def _build_header(self):
        h = QtWidgets.QWidget()
        h.setObjectName("Header")
        h.setAttribute(Qt.WA_StyledBackground, True)
        baris = QtWidgets.QHBoxLayout(h)
        baris.setContentsMargins(theme.px(12), theme.px(6),
                                 theme.px(12), theme.px(6))
        baris.setSpacing(theme.px(12))
        # LOGO MONO resmi (lockup, GAMBAR — bukan teks), sama seperti navbar web.
        # Brand guide: lockup horizontal minimal 240 px lebar -> tinggi ≈ 65 px.
        self.brand_mark = QtWidgets.QLabel()
        self.brand_mark.setFixedSize(240, 65)
        baris.addWidget(self.brand_mark)
        sep = QtWidgets.QFrame()
        sep.setObjectName("StatusSep")
        sep.setFixedSize(1, 26)
        baris.addWidget(sep)
        judul = QtWidgets.QWidget()
        jl = QtWidgets.QVBoxLayout(judul)
        jl.setContentsMargins(0, 0, 0, 0)
        jl.setSpacing(1)
        t = QtWidgets.QLabel("MISSION CONSOLE")
        t.setObjectName("HeaderTitle")
        s = QtWidgets.QLabel("BlueROV2 · BlueOS · MAVLink — TAHAP 1 (mode ROV)")
        s.setObjectName("HeaderSub")
        theme.set_peran(s, "faint")
        s.setWordWrap(True)
        s.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        jl.addWidget(t)
        jl.addWidget(s)
        baris.addWidget(judul, 1)
        lb = QtWidgets.QLabel("LAYOUT")
        lb.setObjectName("CtlLabel")
        theme.set_peran(lb, "faint")
        baris.addWidget(lb)
        self.seg_layout = U.Segmen([(k, self.NAMA_PRESET[k],
                                     "apply the %s layout (applies immediately)"
                                     % self.NAMA_PRESET[k]) for k in self.SPESIFIKASI])
        self.seg_layout.set_dipilih(self._preset)
        self.seg_layout.dipilih.connect(self._apply_preset)
        baris.addWidget(self.seg_layout)
        _sep1 = QtWidgets.QFrame()
        _sep1.setObjectName("StatusSep")
        _sep1.setFixedSize(1, theme.px(26))
        baris.addWidget(_sep1)
        self.chip_bar = U.FlowBox(spacing=4)
        self.chip_link = W.Chip("link")
        self.chip_mode = W.Chip("mode")
        self.chip_arm = W.Chip("arm")
        self.chip_pad = W.Chip("pad")
        self.chip_rec = W.Chip("rec")
        self.chip_fs = W.Chip("fs")          # failsafe sedang aktif?
        self.chip_ekf = W.Chip("ekf")        # kesehatan EKF
        # audit UI 13 Okt: chip MODE/ARM/PAD tidak lagi ditempel di baris chip —
        # informasinya sudah dibawa BANNER BESAR (hindari duplikasi; sisa: LINK/REC/FS/EKF)
        for c, teks in ((self.chip_link, "menunggu"), (self.chip_mode, "—"),
                        (self.chip_arm, "—"), (self.chip_pad, "off"), (self.chip_rec, "—"),
                        (self.chip_fs, "—"), (self.chip_ekf, "—")):
            self._chip(c, "ink_faint", teks)
        for c in (self.chip_link, self.chip_rec, self.chip_fs, self.chip_ekf):
            self.chip_bar.tambah(c)
        # BANNER STATUS BESAR (permintaan pemilik 13 Okt): MODE & ARMED/DISARMED
        # harus langsung terbaca — pilot TIDAK boleh "buta" soal status kendaraan.
        self.lbl_mode_big = QtWidgets.QLabel("MODE · —")
        self.lbl_arm_big = QtWidgets.QLabel("—")
        self.lbl_pad_big = QtWidgets.QLabel("PAD · NOT FOUND")
        for lb in (self.lbl_mode_big, self.lbl_arm_big, self.lbl_pad_big):
            lb.setAlignment(Qt.AlignCenter)
            lb.setTextFormat(Qt.RichText)
        klaster = QtWidgets.QWidget()
        kl = QtWidgets.QHBoxLayout(klaster)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(theme.px(6))
        for lb in (self.lbl_mode_big, self.lbl_arm_big, self.lbl_pad_big):
            kl.addWidget(lb)
        baris.addWidget(klaster)
        self._gaya_banner(None, None)
        self._gaya_pad(False)
        _sep2 = QtWidgets.QFrame()
        _sep2.setObjectName("StatusSep")
        _sep2.setFixedSize(1, theme.px(26))
        baris.addWidget(_sep2)
        baris.addWidget(self.chip_bar)
        self._set_mark_merek()
        return h

    def _buat_strip_alert(self):
        """Strip ALERT KESELAMATAN (permintaan pemilik 13 Okt): leak / baterai /
        failsafe / link. Teks pendek & langsung; detail penuh masuk log notifikasi."""
        self.lbl_alert = QtWidgets.QLabel("")
        self.lbl_alert.setAlignment(Qt.AlignCenter)
        self.lbl_alert.setVisible(False)
        self._alert_kini = None
        self._leak_latch = False
        return self.lbl_alert

    def _alert_show(self, sev, teks):
        key = (sev, teks)
        if self._alert_kini == key:
            return
        self._alert_kini = key
        latar = theme.warna("err") if sev == "krit" else theme.warna("warn")
        self.lbl_alert.setText(("⛔ " if sev == "krit" else "⚠ ") + teks)
        self.lbl_alert.setStyleSheet(
            "background:%s; color:%s; font-size:%dpx; font-weight:800;"
            " letter-spacing:1.2px; padding:%dpx;"
            % (latar, theme.warna("on_accent"), theme.px(12), theme.px(5)))
        self.lbl_alert.setVisible(True)
        try:
            if sev == "krit":
                QtWidgets.QApplication.beep()
        except Exception:                                  # noqa: BLE001
            pass
        self._catat("[ALERT] " + teks)

    def _alert_clear(self):
        if self._alert_kini is None:
            return
        self._alert_kini = None
        self.lbl_alert.setVisible(False)

    def _safety_check(self, s):
        """Monitor keselamatan berjalan (leak latch · baterai · failsafe · link)."""
        try:
            txt = (s.get("last_text") or "").lower()
            if ("leak" in txt or "bocor" in txt) and s.get("last_text_sev", 6) <= 4:
                self._leak_latch = True
            issues = []
            if self._leak_latch:
                issues.append(("krit", "LEAK DETECTED — surface the ROV"))
            v = s.get("volt")
            rem = s.get("remaining")
            low_v = float(self.bridge.params.get("BATT_LOW_VOLT") or 14.0)
            crt_v = float(self.bridge.params.get("BATT_CRT_VOLT") or 13.2)
            if v is not None and v <= crt_v:
                issues.append(("krit", "Battery CRITICAL %.1f V" % v))
            elif rem is not None and rem <= 12:
                issues.append(("krit", "Battery CRITICAL %d%%" % int(rem)))
            elif (v is not None and v <= low_v) or (rem is not None and rem <= 25):
                issues.append(("warn", "Battery low %s" %
                               (("%.1f V" % v) if (v is not None and v <= low_v)
                                else ("%d%%" % int(rem)))))
            if ("failsafe" in txt or "e-stop" in txt or "estop" in txt) \
                    and s.get("last_text_sev", 6) <= 4:
                issues.append(("krit" if s.get("last_text_sev", 6) <= 3 else "warn",
                               "FAILSAFE — check vehicle"))
            try:
                _temp = s.get("temp")
                if _temp is not None:
                    _tw = float(self.sp_temp_warn.value())
                    _tc = float(self.sp_temp_crit.value())
                    if float(_temp) >= _tc:
                        issues.append(("krit", "Water temp CRITICAL %.1f C"
                                       % float(_temp)))
                    elif float(_temp) >= _tw:
                        issues.append(("warn", "Water temp high %.1f C"
                                       % float(_temp)))
            except Exception:                              # noqa: BLE001
                pass
            if not s.get("link", True):
                issues.append(("warn", "MAVLink lost — reconnecting"))
            if issues:
                krit = [t for k, t in issues if k == "krit"]
                teks = krit[0] if krit else issues[0][1]
                sev = "krit" if krit else "warn"
                if len(issues) > 1:
                    teks = "%s  (+%d)" % (teks, len(issues) - 1)
                self._alert_show(sev, teks)
            else:
                self._alert_clear()
        except Exception:                                  # noqa: BLE001
            pass

    def _chip(self, chip, warna, teks):
        try:
            chip.set_state(warna, teks)
        except Exception:                                  # noqa: BLE001
            try:
                chip.set_state("ink_faint", teks)
            except Exception:                              # noqa: BLE001
                pass

    def _set_mark_merek(self):
        """Pasang LOCKUP MONO resmi sesuai terang/gelapnya bidang — sama seperti web.

        (Bone di bidang ink, Ink di bidang ivory; file mono dari brand kit/web.)
        """
        gelap = True
        try:
            bg = theme.hexa("bg")
            r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
            gelap = (0.299 * r + 0.587 * g + 0.114 * b) < 128
        except Exception:                                  # noqa: BLE001
            pass
        nama = ("lockup-dark-mono-tight.svg" if gelap
                else "lockup-light-mono-tight.svg")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ikon", nama)
        try:
            from PySide6.QtSvg import QSvgRenderer
            rnd = QSvgRenderer(path)
            pix = QtGui.QPixmap(240, 65)
            pix.fill(QtGui.QColor(0, 0, 0, 0))
            p = QtGui.QPainter(pix)
            rnd.render(p)
            p.end()
            self.brand_mark.setPixmap(pix)
        except Exception:                                  # noqa: BLE001
            pass

    # ================================================================ statusbar
    def _bangun_statusbar(self):
        def buat(teks=""):
            l = QtWidgets.QLabel(teks)
            l.setObjectName("MicroLabel")
            theme.set_peran(l, "dim")
            return l
        self.sb_msg = buat("siap — menunggu stream & telemetri …")
        self.sb_link = buat("MAVLink: —")
        self.sb_batt = buat("BATT: —")
        self.sb_raw = buat("RAW: —")
        self.sb_pad = buat("PAD: —")
        self.sb.lay.addWidget(self.sb_msg, 1)
        self.sb.tambah_pemisah()
        for w in (self.sb_link, self.sb_batt, self.sb_raw, self.sb_pad):
            self.sb.lay.addWidget(w)
            self.sb.tambah_pemisah()
        self.sb_sesi = buat("sesi: " + os.path.basename(self.sesi))
        self.sb.lay.addWidget(self.sb_sesi)
        self.sb.tambah_pemisah()
        self.sb_jam = buat("--:--:--")
        self.sb.lay.addWidget(self.sb_jam)

    def _perbarui_statusbar(self):
        s = self.bridge.state
        self.sb_link.setText("MAVLink: %s" % ("terhubung" if s.get("link") else "menunggu"))
        v = s.get("volt")
        rem = s.get("remaining")
        self.sb_batt.setText("BATT: %s%s" % ("%.2f V" % v if v else "—",
                                             (" / %d%%" % rem) if rem is not None else ""))
        self.sb_pad.setText("PAD: %s" % ("ready" if self.gamepad._konek else "none"))
        try:
            d = self.rawpanel.files_info
            total = sum(d.values()) / 1e6
            self.sb_raw.setText("RAW: %d chunk (%.0f MB)" % (len(d), total))
        except Exception:                                  # noqa: BLE001
            pass
        self.sb_jam.setText(time.strftime("%H:%M:%S"))

    # ================================================================ kolom
    def _bangun_kolom(self, body):
        self._baris_row = None
        self.col_left, self._lay_left = U.kolom_rel(340, 370)
        self.col_mid = QtWidgets.QWidget()
        self._lay_mid = QtWidgets.QVBoxLayout(self.col_mid)
        self._lay_mid.setContentsMargins(0, 0, 0, 0)
        self._lay_mid.setSpacing(6)
        self.col_right, self._lay_right = U.kolom_rel(340, 370)
        body.addWidget(self.col_left, 0)
        body.addWidget(self.col_mid, 1)
        body.addWidget(self.col_right, 0)

    def _apply_preset(self, name):
        if name not in self.SPESIFIKASI:
            name = self.PRESET_BAWAAN
        self._preset = name
        spec = self.SPESIFIKASI[name]
        try:
            self.seg_layout.set_dipilih(name)
        except Exception:                                  # noqa: BLE001
            pass
        lb = theme.px(spec.get("lebar_kiri", 350))
        # rail KIRI dan KANAN dibuat SAMA LEBAR PERSIS (proporsional & simetris)
        for kol in (self.col_left, self.col_right):
            kol.setFixedWidth(lb)
        self.vis = {}
        # AUDIT 13 Okt (bug "window nyangkut" saat ganti-ganti preset cepat):
        # takeAt() hanya mengeluarkan item dari layout — widget TIDAK di-parent-ulang,
        # sehingga panel yang tidak dipakai preset baru tetap MELAYANG & TAMPIL di
        # atas konten (panel "salah topik"). Sembunyikan SEMUA dulu; di bawah hanya
        # yang benar-benar ditempatkan yang di-show() kembali.
        for _w in self.P.values():
            try:
                _w.hide()
            except Exception:                              # noqa: BLE001
                pass
        if getattr(self, "_baris_row", None) is not None:
            try:
                self._baris_row.hide()
            except Exception:                              # noqa: BLE001
                pass
        U.kosongkan(self._lay_left)
        U.kosongkan(self._lay_right)
        U.kosongkan(self._lay_mid)
        for key in spec.get("left", []):
            if key in self.P:
                self._lay_left.addWidget(self.P[key])
                self.vis[key] = self.P[key]
                self.P[key].show()
        self._lay_left.addStretch(1)
        for key in spec.get("right", []):
            if key in self.P:
                self._lay_right.addWidget(self.P[key])
                self.vis[key] = self.P[key]
                self.P[key].show()
        self._lay_right.addStretch(1)
        row_keys = spec.get("mid_row") or []
        row_di = int(spec.get("row_di", len(spec.get("mid", []))))
        mid = spec.get("mid", [])
        for i, item in enumerate(mid):
            key, st = item if isinstance(item, tuple) else (item, None)
            if row_keys and i == row_di:
                self._lay_mid.addWidget(self._baris_bawah(row_keys))
            stretch = st if st is not None else (1 if key in ("cam_front", "params") else 0)
            self._lay_mid.addWidget(self.P[key], stretch)
            self.vis[key] = self.P[key]
            self.P[key].show()
        if row_keys and row_di >= len(mid):
            self._lay_mid.addWidget(self._baris_bawah(row_keys))
        # tanpa panel ber-stretch di kolom tengah (mode kamera rasio-penuh):
        # sisa ruang ke bawah supaya panel mengunci (hug) kontennya
        if not any((item[1] if isinstance(item, tuple) else None) or
                   (isinstance(item, str) and item in ("cam_front", "params"))
                   for item in mid):
            self._lay_mid.addStretch(1)
        self._fade_masuk()

    def _fade_masuk(self):
        """Apple-feel: konten muncul dengan fade 160 ms ease-out saat preset berganti
        (bukan 'jeglek'). Efek dihapus setelah selesai — tidak permanen (hemat render)."""
        try:
            eff = QtWidgets.QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(eff)
            ani = QtCore.QPropertyAnimation(eff, b"opacity", self)
            ani.setDuration(160)
            ani.setStartValue(0.55)
            ani.setEndValue(1.0)
            ani.setEasingCurve(QtCore.QEasingCurve.OutCubic)

            def _selesai():
                try:
                    self.setGraphicsEffect(None)
                except Exception:                          # noqa: BLE001
                    pass

            ani.finished.connect(_selesai)
            self._anim_fade = ani                          # cegah GC
            ani.start(QtCore.QAbstractAnimation.DeleteWhenStopped)
        except Exception:                                  # noqa: BLE001
            pass

    def _baris_bawah(self, keys):
        if self._baris_row is None:
            self._baris_row = QtWidgets.QWidget(self.col_mid)
            rl = QtWidgets.QHBoxLayout(self._baris_row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(theme.px(6))
            self._baris_lay = rl
        U.kosongkan(self._baris_lay)
        self._baris_row.show()
        self._baris_row.setMinimumHeight(150)
        self._baris_row.setMaximumHeight(300)
        for k in keys:
            if k in self.P:
                self._baris_lay.addWidget(self.P[k], 1)
                self.vis[k] = self.P[k]
        return self._baris_row

    # ================================================================ panel
    def _panel_kamera(self, key, judul, tinggi):
        p = W.Panel(judul, "H.264 · BlueOS", domain="field")
        tile = W.CameraTile(key.replace("cam_", ""), judul=False)
        tile.mode_gambar = "contain"     # T1 nyata: frame kamera ditampilkan UTUH
        tile.tinggi_maks = tinggi
        tile.set_status("waiting for stream …")
        p.body_layout.addWidget(tile)      # hug: tinggi = rasio frame (tanpa stretch)
        p.tile = tile
        return p

    def _build_panels(self):
        P = self.P
        # ---- kamera
        P["cam_front"] = self._panel_kamera("cam_front", "CAM · FRONT (PILOT)", None)
        P["cam_left"] = self._panel_kamera("cam_left", "CAM · STEREO L", None)
        P["cam_right"] = self._panel_kamera("cam_right", "CAM · STEREO R", None)

        # ---- flight
        p = W.Panel("FLIGHT · ATTITUDE & DEPTH", "roll/pitch · heading", domain="field")
        dial = QtWidgets.QHBoxLayout()
        dial.setSpacing(8)
        cell_a = QtWidgets.QVBoxLayout()
        cell_a.setSpacing(2)
        self.horizon = W.Horizon()
        self.horizon.setFixedSize(118, 118)
        lab_a = QtWidgets.QLabel("ATTITUDE")
        lab_a.setObjectName("MicroLabel")
        lab_a.setAlignment(Qt.AlignHCenter)
        lab_a.setMinimumWidth(theme.px(86))
        cell_a.addWidget(self.horizon, 0, Qt.AlignHCenter)
        cell_a.addWidget(lab_a, 0, Qt.AlignHCenter)
        cell_b = QtWidgets.QVBoxLayout()
        cell_b.setSpacing(2)
        self.kompas = W.Kompas()
        self.kompas.setFixedSize(118, 118)
        lab_b = QtWidgets.QLabel("COMPASS")
        lab_b.setObjectName("MicroLabel")
        lab_b.setAlignment(Qt.AlignHCenter)
        lab_b.setMinimumWidth(theme.px(86))
        cell_b.addWidget(self.kompas, 0, Qt.AlignHCenter)
        cell_b.addWidget(lab_b, 0, Qt.AlignHCenter)
        dial.addLayout(cell_a, 1)
        dial.addLayout(cell_b, 1)
        p.body_layout.addLayout(dial)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self.ro_depth = W.Readout("Depth", "m", size=14, peran="accent")
        self.ro_head = W.Readout("HDG", "°", size=14)
        self.ro_temp = W.Readout("Temp", "°C", size=14)
        for r in (self.ro_depth, self.ro_head, self.ro_temp):
            row.addWidget(r, 1)
        p.body_layout.addLayout(row)
        P["flight"] = p

        # ---- instruments ala QGC (NAMED_VALUE_FLOAT / SERVO_OUTPUT_RAW)
        p = W.Panel("INSTRUMENTS · SUB (QGC)", "sub values", domain="field")
        grid2 = QtWidgets.QGridLayout()
        grid2.setSpacing(6)
        # AUDIT FUNGSI (13 Okt): hanya yang BENAR-BENAR punya data dari robot ini.
        # RANGEFINDER dibuang (0 pesan DISTANCE_SENSOR — tidak ada sensor);
        # TETHER dibuang (TetherTrn konstan 0 — tidak ada mesin tether pada ROV ini).
        self.ro_camtilt = W.Readout("Cam Tilt", "%", size=14)
        self.ro_lights = W.Readout("Lights1", "%", size=14)
        self.ro_gain = W.Readout("Pilot Gain", "%", size=14)
        self.ro_water = W.Readout("Water", "°C", size=14)
        for i, r in enumerate((self.ro_camtilt, self.ro_lights, self.ro_gain,
                               self.ro_water)):
            grid2.addWidget(r, i // 2, i % 2)
        p.body_layout.addLayout(grid2)
        P["instruments"] = p

        # ---- power
        p = W.Panel("POWER & SYSTEM", "battery · link", domain="field")
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self.ro_volt = W.Readout("Volt", "V", size=16)
        self.ro_curr = W.Readout("Current", "A", size=16)
        self.ro_batt = W.Readout("Battery", "%", size=16)
        for r in (self.ro_volt, self.ro_curr, self.ro_batt):
            row.addWidget(r, 1)
        p.body_layout.addLayout(row)
        P["power"] = p

        # ---- vehicle (pilot)
        p = W.Panel("VEHICLE (PILOT)", "arm · lights · tilt", domain="mission")
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        self.cb_mode = QtWidgets.QComboBox()
        self.cb_mode.addItems([n for n, _m in MODE_LIST])
        self.cb_mode.setToolTip("mode ArduSub aktif T1 (otonom/posisi = coming soon)")
        b_go = QtWidgets.QPushButton("SET")
        b_go.setObjectName("BtnTiny")
        b_go.clicked.connect(lambda: self._buka_mode_nama(self.cb_mode.currentIndex()))
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
        b_surf.clicked.connect(lambda: self._buka_mode_nama(3))
        _fm = QtGui.QFontMetrics(self.font())
        for _b in (b_go, b_arm, b_dis, b_surf):
            _b.setMinimumWidth(_fm.horizontalAdvance(_b.text()) + 22)
        row.addWidget(self.cb_mode, 1)
        row.addWidget(b_go)
        row.addStretch(1)
        p.body_layout.addLayout(row)
        rowb = QtWidgets.QHBoxLayout()
        rowb.setSpacing(4)
        for wdg in (b_arm, b_dis, b_surf):
            rowb.addWidget(wdg, 1)
        p.body_layout.addLayout(rowb)
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(4)
        lab = QtWidgets.QLabel("LIGHTS 0%")
        lab.setObjectName("MicroLabel")
        lab.setFixedWidth(theme.px(80))
        self.lab_lights = lab
        self.sl_lights = QtWidgets.QSlider(Qt.Horizontal)
        self.sl_lights.setRange(0, 100)
        self.sl_lights.setToolTip("intensitas lampu (kanal RC9 — VERIFY kolam)")
        self.sl_lights.valueChanged.connect(self._set_lights)
        lab2 = QtWidgets.QLabel("TILT 50%")
        lab2.setObjectName("MicroLabel")
        lab2.setFixedWidth(theme.px(68))
        self.lab_tilt = lab2
        b_tc = QtWidgets.QPushButton("RESET")
        b_tc.setObjectName("BtnTiny")
        b_tc.setToolTip("kembalikan kamera tilt ke posisi 50% — otomatis (baca balik CamTilt)")
        b_tc.clicked.connect(self._reset_tilt)
        self.sl_tilt = QtWidgets.QSlider(Qt.Horizontal)
        self.sl_tilt.setRange(1100, 1900)
        self.sl_tilt.setValue(1500)
        self.sl_tilt.setToolTip("tilt kamera depan (kanal RC8 — VERIFY kolam)")
        self.sl_tilt.valueChanged.connect(self._set_tilt)
        self.sp_lights = QtWidgets.QSpinBox()
        self.sp_lights.setRange(0, 100)
        self.sp_lights.setSuffix("%")
        self.sp_lights.setFixedWidth(theme.px(64))
        self.sp_lights.setToolTip("ketik 0-100 lalu Enter (sama dengan slider)")
        self.sp_lights.valueChanged.connect(self._spin_lights)
        row2.addWidget(lab)
        row2.addWidget(self.sl_lights, 1)
        row2.addWidget(self.sp_lights)
        p.body_layout.addLayout(row2)
        row3x = QtWidgets.QHBoxLayout()
        row3x.setSpacing(theme.px(4))
        row3x.addWidget(lab2)
        row3x.addWidget(self.sl_tilt, 1)
        self.sp_tilt = QtWidgets.QSpinBox()
        self.sp_tilt.setRange(0, 100)
        self.sp_tilt.setSuffix("%")
        self.sp_tilt.setFixedWidth(theme.px(64))
        self.sp_tilt.setValue(50)
        self.sp_tilt.setToolTip("ketik 0-100 lalu Enter. 50 = berhenti; "
                                ">50 tilt turun, <50 tilt naik (RC8 = komando laju)")
        self.sp_tilt.valueChanged.connect(self._spin_tilt)
        row3x.addWidget(self.sp_tilt)
        row3x.addWidget(b_tc)
        p.body_layout.addLayout(row3x)
        row3 = QtWidgets.QHBoxLayout()
        row3.setSpacing(6)
        self.lbl_gain = QtWidgets.QLabel("gain: —")
        self.lbl_gain.setToolTip("pilot gain (param JS_GAIN_DEFAULT) — 100% = penuh")
        self.lbl_gain.setObjectName("Faint")
        b_gm = QtWidgets.QPushButton("GAIN −")
        b_gm.setObjectName("BtnTiny")
        b_gm.setToolTip("kurangi pilot gain (JS_GAIN_DEFAULT) 0.05")
        b_gm.clicked.connect(lambda: self._gain_step(-0.05))
        b_gp = QtWidgets.QPushButton("GAIN +")
        b_gp.setObjectName("BtnTiny")
        b_gp.setToolTip("tambah pilot gain (JS_GAIN_DEFAULT) 0.05")
        b_gp.clicked.connect(lambda: self._gain_step(0.05))
        row3.addWidget(b_gm)
        row3.addWidget(b_gp)
        row3.addWidget(self.lbl_gain, 1)
        p.body_layout.addLayout(row3)
        row4 = QtWidgets.QHBoxLayout()
        row4.setSpacing(6)
        lab4 = QtWidgets.QLabel("MAX SPEED")
        lab4.setObjectName("MicroLabel")
        self.cb_cap = QtWidgets.QComboBox()
        for teks, val in (("25%", 0.25), ("50%", 0.5), ("75%", 0.75), ("100%", 1.0)):
            self.cb_cap.addItem(teks, val)
        self.cb_cap.setCurrentIndex(3)
        self.cb_cap.setToolTip("batas kecepatan perintah pilot (skala stick → MANUAL_CONTROL).\n"
                               "100% = penuh. Setel dari GUI; tidak menulis param kendaraan.")
        self.cb_cap.currentIndexChanged.connect(self._set_cap)
        row4.addWidget(lab4)
        row4.addWidget(self.cb_cap, 1)
        p.body_layout.addLayout(row4)
        P["vehicle"] = p

        # ---- setup (kalibrasi)
        p = W.Panel("SETUP · KALIBRASI", "ala QGC/BlueOS Vehicle Setup", domain="mission")
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(4)
        tombol = (("GYRO", lambda: self._cal("gyro")),
                  ("ACCEL (6-POS)…", self._cal_accel),
                  ("COMPASS", lambda: self._cal("mag")),
                  ("LEVEL", lambda: self._cal("level")),
                  ("BARO", lambda: self._cal("baro")),
                  ("PARAMS", lambda: self.bridge.queue_param_list()))
        for i, (nama, fn) in enumerate(tombol):
            b = QtWidgets.QPushButton(nama)
            b.setObjectName("BtnTiny")
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        p.body_layout.addLayout(grid)
        info = QtWidgets.QLabel("Urutan disarankan: gyro → level → accel → compass → baro "
                                "(baro setiap awal penyelaman). Baca instruksi tiap dialog.")
        info.setObjectName("MicroLabel")
        info.setWordWrap(True)
        p.body_layout.addWidget(info)
        P["setup"] = p

        # ---- motors
        p = W.Panel("MOTOR TEST", "per-thruster · MOTOR DETECT", domain="mission")
        b = QtWidgets.QPushButton("MOTOR TEST…")
        b.setObjectName("BtnTiny")
        b.clicked.connect(self._motor_dialog)
        p.body_layout.addWidget(b)
        b2 = QtWidgets.QPushButton("MODE MOTOR DETECT (arah putaran, di air)")
        b2.setObjectName("BtnTiny")
        b2.clicked.connect(lambda: self.bridge.queue_mode(VO.MODE_MOTOR_DETECT))
        p.body_layout.addWidget(b2)
        ket = QtWidgets.QLabel("Uji darat: LEPAS propeller. Uji air: mode MOTOR DETECT "
                               "menjalankan rutin otomatis (ROV mengapung bebas).")
        ket.setObjectName("MicroLabel")
        ket.setWordWrap(True)
        p.body_layout.addWidget(ket)
        P["motors"] = p

        # ---- failsafe
        p = W.Panel("FAILSAFE & BATERAI", "param FS_/BATT_ (gaya QGC Safety)", domain="mission")
        p.body_layout.addWidget(VO.FailsafePanel(self.bridge))
        b_fs = QtWidgets.QPushButton("APPLY RECOMMENDED (4S 10000 mAh)")
        b_fs.setObjectName("BtnTiny")
        b_fs.setToolTip("tulis setelan default aman untuk BlueROV2 + Tattu 4S 10Ah\n"
                        "(BATT_* + FS_*). Setelah ini SEMUA masih bisa diubah manual.")
        b_fs.clicked.connect(self._apply_failsafe_defaults)
        p.body_layout.addWidget(b_fs)
        rowt = QtWidgets.QHBoxLayout()
        rowt.setSpacing(theme.px(4))
        _lt1 = QtWidgets.QLabel("TEMP warn")
        _lt1.setObjectName("MicroLabel")
        _lt2 = QtWidgets.QLabel("crit")
        _lt2.setObjectName("MicroLabel")
        self.sp_temp_warn = QtWidgets.QSpinBox()
        self.sp_temp_warn.setRange(0, 80)
        self.sp_temp_warn.setValue(38)
        self.sp_temp_warn.setSuffix(" °C")
        self.sp_temp_warn.setFixedWidth(theme.px(70))
        self.sp_temp_warn.setToolTip("water-temp warning in the GUI (not an autopilot "
                                     "param) — default 38 °C")
        self.sp_temp_crit = QtWidgets.QSpinBox()
        self.sp_temp_crit.setRange(0, 90)
        self.sp_temp_crit.setValue(45)
        self.sp_temp_crit.setSuffix(" °C")
        self.sp_temp_crit.setFixedWidth(theme.px(70))
        self.sp_temp_crit.setToolTip("critical water-temp threshold in the GUI — "
                                     "default 45 °C")
        rowt.addWidget(_lt1)
        rowt.addWidget(self.sp_temp_warn)
        rowt.addWidget(_lt2)
        rowt.addWidget(self.sp_temp_crit)
        rowt.addStretch(1)
        p.body_layout.addLayout(rowt)
        self.panel_failsafe = p          # dipakai _safety_check (suhu) & guide
        P["failsafe"] = p
        P["guide"] = self._buat_panel_guide()

        # ---- parameter browser
        p = W.Panel("PARAMETERS", "PARAM_REQUEST_LIST / SET", domain="mission")
        p.body_layout.addWidget(VO.ParamBrowser(self.bridge))
        P["params"] = p

        # ---- notifikasi
        p = W.Panel("NOTIFICATIONS", "messages", domain="neutral")
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(600)
        p.body_layout.addWidget(self.log, 1)
        P["notif"] = p

        # ---- capture
        p = W.Panel("CAPTURE", "record · snapshot", domain="mission")
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
            b1.toggled.connect(lambda on, n=nama: self._toggle_rec(n, on))
            b2 = QtWidgets.QPushButton("SNAP")
            b2.setObjectName("BtnTiny")
            b2.clicked.connect(lambda _, n=nama: self._snap(n))
            grid.addWidget(lab, i, 0)
            grid.addWidget(b1, i, 1)
            grid.addWidget(b2, i, 2)
            self.btn_rec[nama] = b1
            self.btn_snap[nama] = b2
        p.body_layout.addLayout(grid)
        self.lbl_sesi = QtWidgets.QLabel("sesi: " + os.path.basename(self.sesi))
        self.lbl_sesi.setObjectName("Faint")
        self.lbl_sesi.setWordWrap(True)
        p.body_layout.addWidget(self.lbl_sesi)
        self.lbl_disk = QtWidgets.QLabel("disk topside: —")
        self.lbl_disk.setObjectName("Faint")
        p.body_layout.addWidget(self.lbl_disk)
        self.lbl_cap_last = QtWidgets.QLabel("")      # kosong sampai ada rekaman
        self.lbl_cap_last.setObjectName("Faint")
        p.body_layout.addWidget(self.lbl_cap_last)
        P["capture"] = p
        td = QtCore.QTimer(self)
        td.timeout.connect(self._update_disk)
        td.start(5000)

        # ---- teleop
        p = W.Panel("TELEOP (GAMEPAD)", "deadman hold", domain="mission")
        self._teleop_bars = {}
        # label per bar (audit 13 Okt: pilot tidak bisa tahu bar itu apa tanpa teks)
        for kode, nama in (("fwd", "FWD"), ("lat", "LAT"), ("depth", "DEPTH"),
                           ("yaw", "YAW"), ("dpx", "PAD-X"), ("dpy", "PAD-Y")):
            baris_bar = QtWidgets.QHBoxLayout()
            baris_bar.setSpacing(theme.px(6))
            lab = QtWidgets.QLabel(nama)
            lab.setObjectName("MicroLabel")
            lab.setFixedWidth(theme.px(44))
            bar = QtWidgets.QProgressBar()
            bar.setRange(-100, 100)
            bar.setTextVisible(False)
            bar.setFixedHeight(theme.px(8))
            baris_bar.addWidget(lab)
            baris_bar.addWidget(bar, 1)
            p.body_layout.addLayout(baris_bar)
            self._teleop_bars[kode] = bar
        self.lbl_pad = QtWidgets.QLabel("pad: —")
        self.lbl_pad.setObjectName("MicroLabel")
        self.lbl_pad.setWordWrap(True)
        p.body_layout.addWidget(self.lbl_pad)
        b_map = QtWidgets.QPushButton("PAD MAPPING…")
        b_map.setObjectName("BtnTiny")
        b_map.setToolTip("see every button function + remap + saved presets")
        b_map.clicked.connect(self._padmap_dialog)
        p.body_layout.addWidget(b_map)
        P["teleop"] = p

        # ---- raw
        p = W.Panel("RAW (ONBOARD)", "onboard · transfer", domain="mission")
        rp = RawPanel(self.args.raw_agent, outdir=self.sesi)
        p.body_layout.addWidget(rp)
        p.panel_raw = rp
        P["raw"] = p

        # ---- peta misi: peta OSM (ala rovgui asli) + plot NED + estimasi
        p = W.Panel("PETA MISI", "OSM · estimate (T2)", domain="mission")
        self.peta = PetaPanel()
        # default T1: perairan Kepulauan Seribu (dekat dermaga Pulau Pramuka).
        # Operator mengganti titik anchor kapan saja lewat "TARUH ANCHOR" (klik peta).
        self.peta.set_anchor({"lat": -5.7463, "lon": 106.6135, "heading_deg": 0.0,
                              "sumber": "default T1 (Kepulauan Seribu)"})
        self.peta.setMinimumHeight(220)
        self.peta.pin_dipilih.connect(self._anchor_dipilih)
        self.plot_misi = PlotMisi()
        self.stack_peta = QtWidgets.QStackedWidget()
        # peta/peta-plot mampu MENYERAP sisa tinggi kolom (jangan dibiarkan kamera
        # melebar mengambil ruang — akar rasio 1.55).
        self.stack_peta.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                      QtWidgets.QSizePolicy.Expanding)
        self.peta.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                QtWidgets.QSizePolicy.Expanding)
        self.plot_misi.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                     QtWidgets.QSizePolicy.Expanding)
        self.stack_peta.addWidget(self.peta)
        self.stack_peta.addWidget(self.plot_misi)
        p.body_layout.addWidget(self.stack_peta, 1)
        rowp = QtWidgets.QHBoxLayout()
        rowp.setSpacing(4)
        self.b_peta = QtWidgets.QPushButton("PETA")
        self.b_peta.setObjectName("BtnTiny")
        self.b_peta.setCheckable(True)
        self.b_peta.setChecked(True)
        self.b_peta.setToolTip("peta OSM ala rovgui (geser: tahan-klik · zoom: roda)")
        self.b_ned = QtWidgets.QPushButton("NED")
        self.b_ned.setObjectName("BtnTiny")
        self.b_ned.setCheckable(True)
        self.b_ned.setToolTip("plot NED: area survei + lajur rencana + jejak")
        self.b_peta.clicked.connect(lambda: self._pilih_peta(0))
        self.b_ned.clicked.connect(lambda: self._pilih_peta(1))
        b_anc = QtWidgets.QPushButton("PLACE ANCHOR")
        b_anc.setObjectName("BtnTiny")
        b_anc.setToolTip("klik: peta masuk mode pin - klik satu titik di peta = anchor (0,0) lokal")
        b_anc.clicked.connect(self._siapkan_anchor)
        b_iku = QtWidgets.QPushButton("FOLLOW ROV")
        b_iku.setObjectName("BtnTiny")
        b_iku.setToolTip("pandangan peta kembali mengikuti posisi estimasi ROV")
        b_iku.clicked.connect(lambda: self.peta.pusatkan_ke_robot())
        b_res = QtWidgets.QPushButton("RESET EST.")
        b_res.setObjectName("BtnTiny")
        b_res.setToolTip("nol-kan posisi estimasi & jejak (mulai dari sini)")
        b_res.clicked.connect(self._reset_estimasi)
        self.lbl_pose = QtWidgets.QLabel("N 0.0  E 0.0")
        self.lbl_pose.setObjectName("MicroLabel")
        _fm2 = QtGui.QFontMetrics(self.font())
        for _b2 in (self.b_peta, self.b_ned, b_anc, b_iku, b_res):
            _b2.setMinimumWidth(_fm2.horizontalAdvance(_b2.text()) + 18)
        rowp.addWidget(self.b_peta)
        rowp.addWidget(self.b_ned)
        rowp.addWidget(b_anc)
        rowp.addWidget(b_iku)
        rowp.addWidget(b_res)
        rowp.addWidget(self.lbl_pose)
        rowp.addStretch(1)
        p.body_layout.addLayout(rowp)
        P["peta"] = p

        # ---- preflight
        p = W.Panel("PRE-FLIGHT CHECK", "pre-flight checklist", domain="mission")
        pf = VO.PreflightPanel(self.bridge)
        p.body_layout.addWidget(pf)
        p.panel_preflight = pf
        P["preflight"] = p

        # ---- coming soon
        p = W.Panel("AUTONOMOUS · VIO", "TAHAP 2 — mode AUV (coming soon)", domain="mission")
        p.body_layout.addWidget(W.Placeholder(
            "COMING SOON",
            "Autonomous, VIO, dan pemetaan berjalan pada Tahap 2 (mode AUV, Jetson Orin Nano). "
            "TAHAP 1 hanya mode ROV: pilot manual + capture."))
        P["soon"] = p

    # ================================================================ menu
    def _build_menubar(self):
        mb = self.menuBar()
        mb.setNativeMenuBar(False)
        m = mb.addMenu("&File")
        m.addAction("Save &screenshot…", self._menu_screenshot, "Ctrl+P")
        m.addSeparator()
        m.addAction("&Quit", self.close, "Ctrl+Q")
        v = mb.addMenu("&View")
        self.vis_act = {}
        label_map = (("cam_front", "Front camera (pilot)"), ("cam_left", "Stereo L"),
                     ("cam_right", "Stereo R"), ("flight", "Flight telemetry"),
                     ("power", "Power & battery"), ("vehicle", "Vehicle (pilot)"),
                     ("preflight", "Pre-flight check"), ("setup", "Setup · kalibrasi"),
                     ("motors", "Motor test"), ("failsafe", "Failsafe & baterai"),
                     ("params", "Parameters"), ("teleop", "Teleop (gamepad)"),
                     ("capture", "Capture"), ("raw", "RAW onboard"),
                     ("notif", "Notifications"), ("soon", "Autonomous (coming soon)"))
        for key, label in label_map:
            a = v.addAction(label)
            a.setCheckable(True)
            a.setChecked(True)
            a.toggled.connect(lambda on, k=key: self._toggle_panel(k, on))
            self.vis_act[key] = a
        v.addSeparator()
        pm = v.addMenu("Layout preset")
        for key in self.SPESIFIKASI:
            pm.addAction(self.NAMA_PRESET[key],
                         lambda _=False, k=key: self._apply_preset(k))
        v.addSeparator()
        vm = v.addMenu("Appearance")
        self.grup_tema = QtGui.QActionGroup(self)
        self.grup_tema.setExclusive(True)
        for nama in theme.tampil():
            a = QtGui.QAction(theme.label(nama), self, checkable=True)
            a.setChecked(nama == theme.aktif())
            a.triggered.connect(lambda _=False, n=nama: self._ganti_tema(n))
            self.grup_tema.addAction(a)
            vm.addAction(a)
        vm.addSeparator()
        # ukuran teks — keterbacaan di kapal / matahari terik (12 Okt 2026)
        self.grup_teks = QtGui.QActionGroup(self)
        self.grup_teks.setExclusive(True)
        for teks_a, nilai_a in (("Ukuran teks: Normal (100%)", 1.0),
                                ("Ukuran teks: Besar (120%)", 1.2),
                                ("Ukuran teks: Ekstra (140%)", 1.4)):
            a = QtGui.QAction(teks_a, self, checkable=True)
            a.setChecked(abs(theme.SKALA - nilai_a) < 0.01)
            a.triggered.connect(lambda _=False, v=nilai_a: self._ganti_skala(v))
            self.grup_teks.addAction(a)
            vm.addAction(a)
        veh = mb.addMenu("&Vehicle")
        veh.addAction("ARM", lambda: self._arm(True))
        veh.addAction("DISARM", lambda: self._arm(False))
        veh.addSeparator()
        for nama, num in MODE_LIST:
            veh.addAction("Mode %s" % nama, lambda _=False, n=num: self.bridge.queue_mode(n))
        veh.addSeparator()
        veh.addAction("Motor test…", self._motor_dialog)
        kal = veh.addMenu("Kalibrasi")
        kal.addAction("Gyroscope", lambda: self._cal("gyro"))
        kal.addAction("Accelerometer (6 posisi)…", self._cal_accel)
        kal.addAction("Compass (onboard)", lambda: self._cal("mag"))
        kal.addAction("Level horizon", lambda: self._cal("level"))
        kal.addAction("Barometer (ground pressure)", lambda: self._cal("baro"))
        veh.addAction("Unduh parameter", lambda: self.bridge.queue_param_list())
        veh.addSeparator()
        veh.addAction("Gamepad mapping…", self._padmap_dialog)
        hlp = mb.addMenu("&Help")
        hlp.addAction("Pre-dive checklist (Blue Robotics)", self._menu_checklist)
        hlp.addAction("About", self._menu_about)

    def _toggle_panel(self, key, on):
        w = self.vis.get(key)
        if w is not None:
            w.setVisible(on)

    def _pill(self, lb, dot, label, value, warna_value, bg="raise_", border="line_hi",
              semua_terang=False):
        """Pill status: titik + LABEL kecil + NILAI — satu bahasa dengan chip."""
        try:
            c_dot = theme.warna(dot) if isinstance(dot, str) else dot
            c_lab = theme.warna("on_accent") if semua_terang else theme.warna("ink_faint")
            c_val = theme.warna(value if semua_terang else warna_value)
            lb.setText(
                '<span style="color:%s">●</span>&nbsp;'
                '<span style="color:%s">%s</span>&nbsp;'
                '<b><span style="color:%s">%s</span></b>'
                % (c_dot, c_lab, label, c_val, value))
            lb.setFixedHeight(theme.px(28))
            lb.setStyleSheet(
                "background:%s; border:1px solid %s; border-radius:%dpx;"
                " padding:0 %dpx; font-size:%dpx; font-weight:600;"
                % (theme.warna(bg), theme.warna(border), theme.px(8),
                   theme.px(12), theme.px(11)))
        except Exception:                                  # noqa: BLE001
            pass

    def _gaya_banner(self, mode, armed):
        """Pill besar: MODE + ARMED/DISARMED — sistem sama dengan chip (dot+label+nilai).

        DI-CACHE: hanya ditulis ulang bila status berubah (anti-kedip).
        """
        try:
            if getattr(self, "_banner_kini", None) == (mode, armed):
                return
            self._banner_kini = (mode, armed)
            self._pill(self.lbl_mode_big, "accent", "MODE", str(mode or "—"), "ink")
            if armed is None:
                self._pill(self.lbl_arm_big, "ink_faint", "ARM", "—", "ink_dim")
            elif armed:
                self._pill(self.lbl_arm_big, "on_accent", "ARM", "ARMED", "on_accent",
                           bg="err", border="err", semua_terang=True)
            else:
                self._pill(self.lbl_arm_big, "ok", "ARM", "DISARMED", "ink_dim")
        except Exception:                                  # noqa: BLE001
            pass

    def _gaya_pad(self, konek):
        """Pill status GAMEPAD — pilot harus tahu pad tersambung atau tidak."""
        try:
            if getattr(self, "_pad_kini", None) == bool(konek):
                return
            self._pad_kini = bool(konek)
            if konek:
                self._pill(self.lbl_pad_big, "ok", "PAD", "READY", "ink")
            else:
                self._pill(self.lbl_pad_big, "ink_faint", "PAD", "NOT FOUND", "ink_dim")
        except Exception:                                  # noqa: BLE001
            pass

    def _ganti_tema(self, nama):
        theme.apply(nama, QtWidgets.QApplication.instance())
        self._set_mark_merek()

    def _ganti_skala(self, x):
        """Ganti ukuran teks GLOBAL (QSS + widget gambar sendiri) secara hidup."""
        theme.set_skala(x)
        theme.apply(theme.aktif(), QtWidgets.QApplication.instance())
        self.lab_lights.setFixedWidth(theme.px(80))
        self.lab_tilt.setFixedWidth(theme.px(68))
        self._apply_preset(self._preset)
        for w_ in self.findChildren(QtWidgets.QWidget):
            w_.update()
        self._catat("ukuran teks -> %.0f%%" % (x * 100))

    def _menu_screenshot(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Simpan screenshot",
                                                     "console.png", "PNG (*.png)")
        if p and self.grab().save(p):
            self._catat("screenshot: %s" % p)

    def _menu_checklist(self):
        teks = ["CHECKLIST PRE-DIVE (Blue Robotics — lengkap ada di panel PRE-FLIGHT):", ""]
        for _k, item in VO.CHECKLIST_PREDIVE:
            teks.append("  □ %s" % item)
        QtWidgets.QMessageBox.information(self, "Pre-dive checklist", "\n".join(teks))

    def _menu_about(self):
        QtWidgets.QMessageBox.about(
            self, "DeepVertex ROV Console",
            "Konsol Misi ROV — TAHAP 1 (mode ROV)\n"
            "BlueROV2 via BlueOS · MAVLink (pymavlink) · GStreamer · PySide6\n"
            "Basis tampilan: konsol sim DeepVertex (satu bahasa desain).\n"
            "Fungsi: teleop gamepad, kalibrasi, motor test, failsafe, parameter, "
            "preflight, capture compressed + RAW onboard.")

    # ================================================================ aksi
    def _cal(self, jenis):
        VO.SimpleCalDialog(self.bridge, jenis, self).exec()

    def _cal_accel(self):
        VO.AccelCalDialog(self.bridge, self).exec()

    def _motor_dialog(self):
        VO.MotorTestDialog(self.bridge, self).exec()

    def _arm(self, on):
        if on:
            pf = getattr(self, "pf", None)
            try:
                total = len(pf.cb) if pf is not None else 0
                belum = sum(1 for c in pf.cb.values() if not c.isChecked()) if total else 0
                if belum:
                    r = QtWidgets.QMessageBox.question(
                        self, "Pre-dive checklist",
                        "Checklist pre-dive %d/%d tercentang.\n"
                        "Lanjut ARM? (ala 'Enforce Preflight' QGC)" % (total - belum, total))
                    if r != QtWidgets.QMessageBox.Yes:
                        self._catat("ARM dibatalkan — checklist pre-dive belum lengkap")
                        return
            except Exception:                              # noqa: BLE001
                pass
        self.bridge.queue_arm(bool(on))
        self._catat("perintah %s dikirim (uji kolam dulu)" % ("ARM" if on else "DISARM"))

    def _buka_mode_nama(self, idx):
        nama, angka = MODE_LIST[idx] if 0 <= idx < len(MODE_LIST) else MODE_LIST[0]
        self.bridge.queue_mode(angka)
        self._catat("perintah mode %s (%d) dikirim" % (nama, angka))

    def _spin_lights(self, v):
        """Ketik persen -> gerakkan slider (satu jalur logika dengan slider)."""
        try:
            if self.sl_lights.value() != int(v):
                self.sl_lights.setValue(int(v))
        except Exception:                                  # noqa: BLE001
            pass

    def _spin_tilt(self, v):
        """Ketik persen (0-100) -> PWM 1100-1900; 50% = 1500 (berhenti)."""
        try:
            pwm = int(1100 + max(0, min(100, int(v))) * 8)
            if self.sl_tilt.value() != pwm:
                self.sl_tilt.setValue(pwm)
        except Exception:                                  # noqa: BLE001
            pass

    def _set_lights(self, val):
        self.lab_lights.setText("LIGHTS %d%%" % int(val))
        try:
            if self.sp_lights.value() != int(val):
                self.sp_lights.blockSignals(True)
                self.sp_lights.setValue(int(val))
                self.sp_lights.blockSignals(False)
        except Exception:                                  # noqa: BLE001
            pass
        if getattr(self.gamepad, "aktif", False):
            self.gamepad.set_lights(val)
        else:
            pwm = int(1100 + (val / 100.0) * 800)
            self._rc_keep[9] = pwm
            self.bridge.rc_override({9: pwm})

    def _kirim_tilt_pwm(self, pwm):
        """Kirim perintah LAJU ch8 langsung (dipakai routine RESET/tengah)."""
        pwm = int(max(1100, min(1900, pwm)))
        if getattr(self.gamepad, "aktif", False):
            self.gamepad.tilt = pwm
        else:
            self._rc_keep[8] = pwm
            self.bridge.rc_override({8: pwm})

    def _reset_tilt(self):
        """RESET tilt: bawa kamera KEMBALI ke posisi 50% (bukan sekadar berhenti).

        RC8 = komando LAJU: menaruh 1500 hanya menghentikan sapuan, posisi kamera
        tetap di tempat terakhir. Routine ini membaca balik CamTilt
        (NAMED_VALUE_FLOAT) dan menyapu perlahan sampai mendekati 50%.
        """
        try:
            self._center_aktif = True
            self._center_t0 = time.monotonic()
            if not hasattr(self, "_center_timer"):
                self._center_timer = QtCore.QTimer(self)
                self._center_timer.timeout.connect(self._tick_center)
            self._center_timer.start(150)
            self._catat("RESET tilt: menyapu kembali ke 50% …")
        except Exception:                                  # noqa: BLE001
            pass

    def _tick_center(self):
        try:
            if not getattr(self, "_center_aktif", False):
                self._center_timer.stop()
                return
            if time.monotonic() - self._center_t0 > 8.0:
                self._center_aktif = False
                self._center_timer.stop()
                self._kirim_tilt_pwm(1500)
                self._catat("RESET tilt: timeout — sapuan dihentikan (1500)")
                return
            x = (self.bridge.state.get("nvf") or {}).get("CamTilt")
            if x is None:
                return
            x = float(x)
            if abs(x - 0.5) <= 0.03:
                self._center_aktif = False
                self._center_timer.stop()
                self._kirim_tilt_pwm(1500)
                try:
                    self.sl_tilt.blockSignals(True)
                    self.sl_tilt.setValue(1500)
                    self.sl_tilt.blockSignals(False)
                    self.lab_tilt.setText("TILT 50%")
                    if self.sp_tilt.value() != 50:
                        self.sp_tilt.blockSignals(True)
                        self.sp_tilt.setValue(50)
                        self.sp_tilt.blockSignals(False)
                except Exception:                          # noqa: BLE001
                    pass
                self._catat("tilt kembali ke 50%% (terbaca %.0f%%)" % (x * 100))
                return
            self._kirim_tilt_pwm(1700 if x > 0.5 else 1300)
        except Exception:                                  # noqa: BLE001
            pass

    def _set_tilt(self, val):
        if getattr(self, "_center_aktif", False):
            self._center_aktif = False
            try:
                self._center_timer.stop()
            except Exception:                              # noqa: BLE001
                pass
            self._catat("RESET tilt dibatalkan (input manual)")
        self._tilt = int(val)
        self.lab_tilt.setText("TILT %d%%" % int(round((self._tilt - 1100) / 8.0)))
        try:
            persen = int(round((self._tilt - 1100) / 8.0))
            if self.sp_tilt.value() != persen:
                self.sp_tilt.blockSignals(True)
                self.sp_tilt.setValue(persen)
                self.sp_tilt.blockSignals(False)
        except Exception:                                  # noqa: BLE001
            pass
        if getattr(self.gamepad, "aktif", False):
            self.gamepad.tilt = self._tilt
        else:
            self._rc_keep[8] = self._tilt
            self.bridge.rc_override({8: self._tilt})

    def _toggle_rec(self, nama, on):
        p = self.players[nama]
        if on:
            path = os.path.join(self.sesi, "%s_%s" % (nama.replace(" ", "_"),
                                                      time.strftime("%H%M%S")))
            if p.start_record(path):
                self.plot_misi.add_marker("rec")
            else:
                self.btn_rec[nama].setChecked(False)
        else:
            p.stop_record()

    def _snap(self, nama):
        path = os.path.join(self.sesi, "snap_%s_%s.png" % (
            nama.replace(" ", "_"), time.strftime("%H%M%S_")))
        ok = self.players[nama].snapshot(path)
        if ok:
            self.plot_misi.add_marker("snap")
        self._catat("snapshot %s → %s" % (nama, path if ok else "GAGAL (belum ada frame)"))

    def _rec_selesai(self, aktif, path):
        if not aktif and path:
            self._catat("rekaman tersimpan: " + path)
            try:
                mb = os.path.getsize(path) / 1e6
                self.lbl_cap_last.setText("last: %s · %.1f MB" %
                                          (os.path.basename(path), mb))
            except OSError:
                pass

    def _update_disk(self):
        import shutil
        try:
            du = shutil.disk_usage(self.sesi)
            self.lbl_disk.setText("disk topside: %.1f GB free" % (du.free / 1e9))
        except OSError:
            pass

    def _teleop_update(self, live):
        vals = live.get("axes") or []
        peta = (vals[1] if len(vals) > 1 else 0, vals[0] if vals else 0,
                vals[4] if len(vals) > 4 else 0, vals[3] if len(vals) > 3 else 0,
                vals[6] if len(vals) > 6 else 0, vals[7] if len(vals) > 7 else 0)
        for kode, v in zip(("fwd", "lat", "depth", "yaw", "dpx", "dpy"), peta):
            self._teleop_bars[kode].setValue(int(max(-1.0, min(1.0, v)) * 100))
        t_live = live.get("tilt")
        try:
            if t_live is not None:
                t_live = int(t_live)
                self.lab_tilt.setText("TILT %d%%" % int(round((t_live - 1100) / 8.0)))
                if not self.sl_tilt.isSliderDown():
                    self.sl_tilt.blockSignals(True)
                    self.sl_tilt.setValue(t_live)
                    self.sl_tilt.blockSignals(False)
        except Exception:                                  # noqa: BLE001
            pass
        btns = [i for i, b in enumerate(live.get("buttons") or []) if b]
        _nama = live.get("nama") or "—"
        _pendek = "DS4" if "sony" in _nama.lower() else _nama[:18]
        _seg = (" | btn %s" % btns) if btns else ""
        self.lbl_pad.setText("pad: %s | deadman %s%s | tilt %d µs" % (
            _pendek, "HELD" if live.get("deadman") else "RELEASED", _seg,
            live.get("tilt", 1500)))
        self.lbl_pad.setToolTip("pad: %s" % _nama)      # info penuh = tooltip (popup)
        self._chip(self.chip_pad, "ok" if live.get("konek") else "warn",
                   "ready" if live.get("konek") else "none")
        self._gaya_pad(bool(live.get("konek")))

    # ================================================================ MAVLink
    def _vehicle(self, s):
        self.last = dict(s)
        try:
            self._chip(self.chip_link, "ok" if s.get("link") else "warn",
                       "terhubung" if s.get("link") else "menunggu")
            self._chip(self.chip_mode, "ink_faint", str(s.get("mode") or "—"))
            self._chip(self.chip_arm, "warn" if s.get("armed") else "ok",
                       "ARMED" if s.get("armed") else "disarm")
            self._gaya_banner(s.get("mode"), s.get("armed"))
            txt_fs = (s.get("last_text") or "").lower()
            if "failsafe" in txt_fs or "e-stop" in txt_fs or "estop" in txt_fs:
                self._chip(self.chip_fs,
                           "err" if s.get("last_text_sev", 6) <= 3 else "warn", "AKTIF")
            else:
                self._chip(self.chip_fs, "ok", "—")
            ekf = s.get("ekf_ok")
            if ekf is True:
                self._chip(self.chip_ekf, "ok", "ok")
            elif ekf is False:
                self._chip(self.chip_ekf, "err", "cek")
            else:
                self._chip(self.chip_ekf, "ink_faint", "—")
            self.ro_depth.set_value(s.get("depth"), "{:.2f}")
            self.ro_head.set_value(s.get("heading"), "{:.0f}")
            self.ro_temp.set_value(s.get("temp"), "{:.1f}")
            self.kompas.set_heading(s.get("heading") or 0)
            nvf = s.get("nvf") or {}
            servo = s.get("servo") or {}
            camt = nvf.get("CamTilt")
            if camt is None and servo.get(16):
                camt = max(0.0, min(1.0, (servo[16] - 1100) / 800.0))
            self.ro_camtilt.set_value(None if camt is None else camt * 100.0, "{:.0f}")
            if (getattr(self, "_perlu_center_awal", False) and camt is not None
                    and not s.get("armed")):
                # permintaan pemilik 13 Okt: baca posisi tilt saat GUI mulai, lalu
                # kembalikan ke 50% SEKALI di setiap awal sesi.
                self._perlu_center_awal = False
                if abs(float(camt) - 0.5) > 0.05:
                    self._catat("camera tilt saat start: %.0f%% — RESET ke 50%%"
                                % (camt * 100))
                    self._reset_tilt()
                else:
                    self._catat("camera tilt saat start: %.0f%% (sudah ~50%%)"
                                % (camt * 100))
            self._safety_check(s)
            l1 = nvf.get("Lights1")
            if l1 is None and servo.get(13):
                l1 = max(0.0, min(1.0, (servo[13] - 1100) / 800.0))
            self.ro_lights.set_value(None if l1 is None else l1 * 100.0, "{:.0f}")
            g = nvf.get("PilotGain")
            self.ro_gain.set_value(None if g is None else g * 100.0, "{:.0f}")
            self.ro_water.set_value(s.get("temp"), "{:.2f}")
            _g = self.bridge.params.get("JS_GAIN_DEFAULT")
            if _g is not None:
                self.lbl_gain.setText("gain: %.2f" % float(_g))
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
            self.sb_msg.setText(str(teks))
            self.log.appendPlainText(time.strftime("[%H:%M:%S] ") + str(teks))
        except Exception:                                  # noqa: BLE001
            pass

    # ================================================================ tutup
    def _sync_tiles(self):
        try:
            for k in ("cam_front", "cam_left", "cam_right"):
                self.P[k].tile._sync_tinggi()
        except Exception:                                  # noqa: BLE001
            pass

    _FS_REKOMENDASI = (
        ("BATT_CAPACITY", 10000), ("BATT_LOW_VOLT", 14.0), ("BATT_CRT_VOLT", 13.2),
        ("BATT_LOW_MAH", 2000), ("BATT_CRT_MAH", 1000),
        ("BATT_FS_LOW_ACT", 1), ("BATT_FS_CRT_ACT", 3),
        ("FS_GCS_ENABLE", 2), ("FS_GCS_TIMEOUT", 5),
        ("FS_PILOT_INPUT", 2), ("FS_PILOT_TIMEOUT", 3),
        ("FS_LEAK_ENABLE", 2),
    )

    def _apply_failsafe_defaults(self):
        """Default failsafe+ baterai utk BlueROV2 + Tattu 4S 10000 mAh (bisa diubah)."""
        for nm, val in self._FS_REKOMENDASI:
            self.bridge.queue_param_set(nm, val)
        self._catat("failsafe defaults applied (BlueROV2 + 4S 10000 mAh) — "
                    "%d params" % len(self._FS_REKOMENDASI))

    def _padmap_dialog(self):
        try:
            padmap.PadMapDialog(self.gamepad, self).exec()
        except Exception as exc:                           # noqa: BLE001
            self._catat("gamepad mapping error: %r" % exc)

    def _buat_panel_guide(self):
        """Panduan SETUP untuk pemula (permintaan pemilik 13 Okt): langkah bernomor."""
        p = W.Panel("SETUP GUIDE (NEW ROV)", "steps 1-7 · ~10 min", domain="motion")
        langkah = (
            (1, "Accelerometer", "ROV flat on the desk — follow the 6 positions.",
             "START…", self._cal_accel),
            (2, "Compass", "Rotate the ROV in all axes; keep metal away.",
             "START…", lambda: self._cal("mag")),
            (3, "Level horizon", "ROV perfectly level, then run.",
             "START…", lambda: self._cal("level")),
            (4, "Barometer (each dive)", "At the surface, before diving.",
             "START…", lambda: self._cal("baro")),
            (5, "Thrusters", "Props OFF on bench; in water use MOTOR DETECT.",
             "OPEN…", self._motor_dialog),
            (6, "Failsafe defaults", "One click for this robot (4S 10000 mAh).",
             "APPLY…", self._apply_failsafe_defaults),
            (7, "Gamepad", "See every button function + remap + presets.",
             "OPEN…", self._padmap_dialog),
        )
        for num, judul, ket, teks_b, aksi in langkah:
            baris = QtWidgets.QHBoxLayout()
            baris.setSpacing(theme.px(6))
            kol = QtWidgets.QVBoxLayout()
            kol.setSpacing(0)
            l1 = QtWidgets.QLabel("%d. %s" % (num, judul))
            l1.setObjectName("RowLabel")
            l2 = QtWidgets.QLabel(ket)
            l2.setObjectName("Faint")
            l2.setWordWrap(True)
            kol.addWidget(l1)
            kol.addWidget(l2)
            b = QtWidgets.QPushButton(teks_b)
            b.setObjectName("BtnTiny")
            b.clicked.connect(aksi)
            baris.addLayout(kol, 1)
            baris.addWidget(b, 0, Qt.AlignTop)
            p.body_layout.addLayout(baris)
        return p

    def _pad_coba(self):
        try:
            if not getattr(self.gamepad, "aktif", False):
                self.gamepad.coba_hotplug()
        except Exception:                                  # noqa: BLE001
            pass

    def _rc_tick(self):
        """Pertahankan RC override tilt/lights saat TANPA gamepad — override
        ArduSub timeout ~3 dtk, jadi nilai slider harus dikirim ulang."""
        if not self._rc_keep or getattr(self.gamepad, "aktif", False):
            return
        try:
            self.bridge.rc_override(dict(self._rc_keep))
        except Exception:                                  # noqa: BLE001
            pass

    def _set_cap(self, _idx):
        """MAX SPEED: skala perintah pilot di sisi GUI (gamepad.cap)."""
        val = self.cb_cap.currentData()
        try:
            val = float(val)
        except (TypeError, ValueError):
            val = 1.0
        try:
            self.gamepad.cap = val
        except Exception:                                  # noqa: BLE001
            pass
        self._catat("MAX SPEED %.0f%% — perintah pilot diskalakan ke %.0f%%"
                    % (val * 100, val * 100))

    def _gain_step(self, delta):
        """Ubah pilot gain via parameter JS_GAIN_DEFAULT (tanpa perlu BTN mapping)."""
        kini = self.bridge.params.get("JS_GAIN_DEFAULT")
        kini = 0.5 if kini is None else float(kini)
        baru = max(0.1, min(1.0, round(kini + delta, 2)))
        self.bridge.queue_param_set("JS_GAIN_DEFAULT", baru)
        self._catat("pilot gain → %.2f (JS_GAIN_DEFAULT)" % baru)
        self.lbl_gain.setText("gain: %.2f" % baru)

    def _pilih_peta(self, ke):
        self.stack_peta.setCurrentIndex(int(ke))
        self.b_peta.setChecked(ke == 0)
        self.b_ned.setChecked(ke == 1)

    def _siapkan_anchor(self):
        self.peta.siapkan_pin()
        self._catat("peta mode PIN: klik satu titik di peta = anchor (0,0) lokal")

    def _anchor_dipilih(self, lat, lon):
        a = dict(self.peta.anchor)
        a["lat"] = float(lat)
        a["lon"] = float(lon)
        a["sumber"] = "pin operator"
        self.peta.set_anchor(a)
        self._catat("anchor peta diset: %.6f, %.6f" % (lat, lon))

    def _reset_estimasi(self):
        self.plot_misi.ancor()
        self.peta.bersihkan()
        self.peta.set_pose(0.0, 0.0, 0.0)
        self._catat("estimasi & jejak direset ke (0,0) di titik anchor")

    def _peta_tick(self):
        now = time.monotonic()
        dt = max(0.02, min(0.5, now - self._peta_t))
        self._peta_t = now
        out = getattr(self.gamepad, "last_out", (0, 0, 500, 0))
        if self.bridge.state.get("armed"):
            vx = out[0] / 1000.0 * 0.5      # m/s (VERIFY kolam)
            vy = out[1] / 1000.0 * 0.35
            hdg = math.radians(self.bridge.state.get("heading") or 0.0)
            vn = vx * math.cos(hdg) - vy * math.sin(hdg)
            ve = vx * math.sin(hdg) + vy * math.cos(hdg)
        else:
            # BELUM ARMED: thruster tidak berputar -> estimasi posisi DIAM
            # (audit 13 Okt: "kenapa robotnya bergerak padahal belum arm").
            vn = ve = 0.0
        n = self.plot_misi.pos[0] + vn * dt
        e = self.plot_misi.pos[1] + ve * dt
        hdg0 = self.bridge.state.get("heading") or 0.0
        self.plot_misi.set_pose(n, e, hdg0)
        self.peta.set_pose(n, e, math.radians(hdg0))
        if now - self._peta_trail_t > 0.5:
            self._peta_trail_t = now
            self.plot_misi.tambah_jejak()
            self.peta.add_gt(n, e)
        self.lbl_pose.setText("N %.1f  E %.1f (EST.)" % (n, e))

    def closeEvent(self, ev):
        try:
            self._peta_timer.stop()
            self.peta.hentikan()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            self.pf._simpan_ck()
        except Exception:                                  # noqa: BLE001
            pass
        for p in getattr(self, "players", {}).values():
            try:
                p.stop()
            except Exception:                              # noqa: BLE001
                pass
        try:
            self.gamepad.stop(keluar=True)
        except Exception:                                  # noqa: BLE001
            pass
        try:
            self.bridge.stop()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            with open(os.path.join(self.sesi, "session_end.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"selesai": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
        except OSError:
            pass
        super().closeEvent(ev)


def _terapkan_tema(app, nama=None, skala=None):
    try:
        theme.muat_font()
    except Exception:                                      # noqa: BLE001
        pass
    try:
        if skala is not None:
            theme.set_skala(skala)
        theme.apply(nama or theme.aktif(), app)
    except Exception:                                      # noqa: BLE001
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
    ap.add_argument("--preset", default="pilot-focus",
                    choices=("pilot-focus", "mapping", "setup", "capture"))
    ap.add_argument("--tlog", default="auto",
                    help="'off' nonaktif; jalur berkas tlog; 'auto' = <sesi>/mavlink.tlog")
    ap.add_argument("--tema", default="abyss", choices=tuple(theme.tampil()),
                    help="palet warna (ink = hitam ala rovgui) — bisa ganti live di View > Appearance")
    ap.add_argument("--teks", type=float, default=1.2, metavar="SKALA",
                    help="skala ukuran teks (1.0/1.2/1.4) utk keterbacaan di lapangan")
    ap.add_argument("--no-pad", action="store_true", help="jangan aktifkan teleop gamepad")
    ap.add_argument("--shot", default=None, help="simpan screenshot jendela ke PATH (uji tampilan)")
    ap.add_argument("--shot-after", type=float, default=6.0, help="detik sebelum screenshot")
    args = ap.parse_args()
    args.port_front = args.front_port
    args.port_stereo_L = args.left_port
    args.port_stereo_R = args.right_port

    app = QtWidgets.QApplication(sys.argv)
    _terapkan_tema(app, getattr(args, "tema", None), getattr(args, "teks", None))
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
