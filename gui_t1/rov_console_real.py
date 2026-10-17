#!/usr/bin/env python3
"""ROV mission console — ROV MODE (mode ROV, BlueROV2 via BlueOS) — konsol lengkap.

TAMPILAN: layout 3 kolom; komponen bersama ada di ui_common.py (Segmen/FlowBox/
StatusBar di ui_common.py), rel kiri & kanan yang scroll, kolom tengah + baris
bawah, menu bar, chip status. Preset: Pilot Focus / Setup & Preflight / Capture.

FUNGSI (gaya QGC): teleop gamepad (MANUAL_CONTROL + deadman), ARM/DISARM/mode,
3 stream kamera + rekam/snapshot, RAW onboard (agen), KALIBRASI (gyro/accel/
compass/level/baro), MOTOR TEST, FAILSAFE+BATERAI, browser PARAMETER,
PRE-FLIGHT CHECK (status hidup + checklist pre-dive resmi Blue Robotics).

Jalankan: ./run_console_real.sh [opsi]   (lihat --help)
"""
import argparse
import copy
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
import laysnap
import snapctl
from peta_panel import PetaPanel
from rawcap import RawPanel
from video import StreamPlayer, h264parse_tersedia

STREAM_DEF = (
    ("front", "CAM · FRONT (Low-Light USB)", 5600),
    ("stereo L", "CAM · STEREO L (B0495C)", 5601),
    ("stereo R", "CAM · STEREO R (B0495C)", 5602),
)


def _panel_float_default(p):
    """Default geometry for a floating panel (centered-ish on the screen)."""
    try:
        g = QtWidgets.QApplication.primaryScreen().geometry()
        p.resize(max(360, p.minimumSizeHint().width()),
                 max(320, p.sizeHint().height()))
        p.move(g.x() + (g.width() - p.width()) // 2 + 40,
               g.y() + (g.height() - p.height()) // 3)
    except Exception:                                      # noqa: BLE001
        pass


# LiPo SoC estimate (voltage-based) — ARDU remaining is ENERGY consumed since
# power-on (BATT_CAPACITY 10000 mAh current monitor), so a 15.5 V pack shows
# ~99% while resting. The piecewise anchor curve below is the operator-facing
# truth (piecewise linear, per cell).
_LIPO_ANCHORS = ((4.20, 100), (4.05, 88), (3.95, 72), (3.90, 62), (3.85, 54),
                 (3.80, 45), (3.75, 36), (3.70, 28), (3.60, 14), (3.50, 6),
                 (3.30, 0))


def _lipo_pct(v):
    """(cell count, SoC percent) from pack voltage; None when no signal.

    Linear 3.70 V/cell (14.8 V pada 4S) = 0% s/d 4.20 V/cell = 100% —
    batas bawah DITETAPKAN PEMILIK 17 Okt (dulu kurva anchor; minimum = 14.8 V).
    """
    if not v or v < 2.0:
        return None
    cells = max(2, min(6, int(round(v / 3.75)) or 2))
    vcell = v / float(cells)
    pct = int(round((vcell - 3.70) / (4.20 - 3.70) * 100.0))
    return (cells, max(0, min(100, pct)))


# Margin pengaman storage (permintaan pemilik 13 Okt):
#   < MIN_FREE  -> REC & SNAP DIBLOKIR (transfer/bersihkan dulu)
#   < WARN_FREE -> peringatan kuning di strip ALERT
MIN_FREE_TOP_MB = 1500
WARN_FREE_TOP_MB = 5000
MODE_LIST = (("MANUAL", VO.MODE_MANUAL), ("STABILIZE", VO.MODE_STABILIZE),
             ("ALT_HOLD (depth hold)", VO.MODE_ALT_HOLD), ("SURFACE", VO.MODE_SURFACE))


def _slug(nama):
    """Nama sesi → potongan aman utk nama folder (huruf/angka/-/_; max 40)."""
    out = []
    for ch in (nama or "").strip().lower():
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif ch in " .":
            out.append("_")
    s = "".join(out)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")[:40]


def _buat_dir_sesi(root, nama=""):
    """Struktur sesi (audit 13 Okt — alur file jelas & siap delivery):

    <root>/<YYYYMMDD_HHMMSS>_<nama-sesi>/
        compressed/   video .mkv + snapshot .png (jalur stream BlueOS)
        raw/          chunk RAW hasil transfer dari Pi (arsip utuh, per kamera)
        mavlink.tlog  /  session_start.json / session_end.json
        sensors.csv   (topside sensor log, 10 Hz — robot tak dibebani)
    """
    os.makedirs(root, exist_ok=True)
    d = os.path.join(root, "%s_%s" % (time.strftime("%Y%m%d_%H%M%S"),
                                      _slug(nama) or "dive"))
    os.makedirs(os.path.join(d, "compressed"), exist_ok=True)
    os.makedirs(os.path.join(d, "raw"), exist_ok=True)
    return d


__version__ = "1.1.3"     # versi aplikasi (session.json + update checker)


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
            # (penyesuaian pemilik 15 Okt: stereo L/R PALING ATAS; teleop tepat
            #  di bawah notifikasi, di atas Autonomous)
            "right": ["cam_left", "cam_right", "capture", "raw", "notif", "teleop",
                      "soon"],
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
            # stereo L/R paling atas; teleop di bawah notifikasi (15 Okt)
            "right": ["cam_left", "cam_right", "capture", "raw", "notif", "teleop"],
        },
    }

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.setWindowTitle("DeepVertex Marine Technology Console")
        try:
            _ikon = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "ikon", "deepvertex-app-icon.png")
            if os.path.exists(_ikon):
                self.setWindowIcon(QtGui.QIcon(_ikon))     # ikon taskbar/dock
        except Exception:                                  # noqa: BLE001
            pass
        self.resize(1620, 1000)
        # NAMA SESI (permintaan pemilik 13 Okt): bisa lewat --nama-sesi, atau
        # ditanya sekali saat start. Otomasi memakai --no-prompt / --nama-sesi "".
        _nama = getattr(args, "nama_sesi", None)
        if _nama is None and not getattr(args, "no_prompt", False):
            try:
                _dlg = QtWidgets.QInputDialog()
                _dlg.setWindowTitle("Session name")
                _dlg.setLabelText("Name this session (blank = \u201cdive\u201d):")
                _dlg.setTextValue("dive")
                # pusatkan di layar (tanpa parent, Windows menaruhnya di pojok)
                _scr = QtWidgets.QApplication.primaryScreen()
                if _scr is not None:
                    _dlg.adjustSize()
                    _dlg.move(_scr.availableGeometry().center() -
                              _dlg.rect().center())
                if _dlg.exec():
                    _nama = _dlg.textValue().strip()
                else:
                    _nama = ""
            except Exception:                              # noqa: BLE001
                _nama = ""
        self.nama_sesi = (_nama or "").strip()
        self.sesi = _buat_dir_sesi(os.path.expanduser(args.outdir), self.nama_sesi)
        self.last = {}
        self._floats_state = {}
        self._active_spec = {}
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
        # sensor log topside (kehendak pemilik 17 Okt): lengkap, di laptop saja
        try:
            import toplog
            self.sensorlog = toplog.SensorLog(
                os.path.join(self.sesi, "sensors.csv"))
        except Exception as exc:                            # noqa: BLE001
            self.sensorlog = None
            self._catat("sensor log off: %r" % exc)
        self.bridge.sig_vehicle.connect(self._vehicle)
        self.bridge.sig_attitude.connect(self._attitude)
        self.bridge.sig_text.connect(self._text)
        self.bridge.sig_status.connect(self._catat)

        self._build_panels()
        self._build_menubar()

        # ---------------- layanan ----------------
        self.players = {}
        self.tile_kam = {}
        self.cam_aktif = {}
        for nama, _judul, port in STREAM_DEF:
            port = getattr(args, "port_" + nama.replace(" ", "_"), port)
            p = StreamPlayer(nama, port)
            key = {"front": "cam_front", "stereo L": "cam_left",
                   "stereo R": "cam_right"}[nama]
            self.tile_kam[nama] = self.P[key].tile
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
        self.gamepad.sig_aksi.connect(self._aksi_pad)

        # integrator peta (estimasi dead-reckoning; label jelas di panel)
        self.plot_misi.ancor()
        self._peta_t = time.monotonic()
        self._peta_trail_t = 0.0
        self._peta_timer = QtCore.QTimer(self)
        self._peta_timer.timeout.connect(self._peta_tick)
        self._peta_timer.start(200)

        self.rawpanel = self.P["raw"].panel_raw
        try:
            import mcm as _mcm_mod
            _ra = (getattr(self.args, "raw_agent", "") or "").split("//")[-1]
            _ra = _ra.split(":")[0] or "192.168.2.2"
            self.mcm = _mcm_mod.McmClient(host=_ra)
            self.mcm.sig_result.connect(self._mcm_pesan)
        except Exception as exc:                            # noqa: BLE001
            self.mcm = None
            self._catat("mcm client init failed: %r" % exc)
        # status storage utk estimasi & guard (diisi oleh _update_disk)
        self._disk_free_mb = None
        self._disk_prev = None            # (t, free_mb)
        # animasi REC (kehendak pemilik 17 Okt): kedip merah saat merekam.
        self._rec_robot = set()           # kamera yang RAW-rec DI ROBOT
        self._anim_fase = False
        self._anim_timer = QtCore.QTimer(self)
        self._anim_timer.timeout.connect(self._anim_tick)
        # log sensor topside 10 Hz (lihat _log_tick)
        self._att_last = (None, None, None)
        self._log_timer = QtCore.QTimer(self)
        self._log_timer.timeout.connect(self._log_tick)
        self._log_timer.start(100)
        self._disk_rate_mbpm = 0.0        # laju tulis EMA (MB/menit)
        self._disk_blok = False
        self._rec_aktif = set()           # nama kamera yang sedang REC (compressed)
        self._param_diminta = False       # param list diminta sekali per koneksi
        self.pf = self.P["preflight"].panel_preflight
        self.pf.sumber = {"players": self.players, "gamepad": self.gamepad,
                          "rawpanel": self.rawpanel, "sesi": self.sesi}
        self.pf.muat_ck()
        def _raw_pesan(tag, d):
            if tag == "status":
                self._catat("RAW agent: %s" % ("OK" if d.get("ok")
                                             else str(d.get("error"))[:80]))
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
            json.dump({"start": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "session_name": getattr(self, "nama_sesi", ""),
                       "mavlink": args.mavlink,
                       "streams": {n: getattr(args, "port_" + n.replace(" ", "_"), d)
                                   for n, _j, d in STREAM_DEF},
                       "raw_agent": args.raw_agent,
                       "h264parse": h264parse_tersedia(), "versi": __version__}, f,
                      indent=2, ensure_ascii=False)
        # Data user rapi (paths.py): migrasi sekali dari <app>/saved lama.
        try:
            import paths as _paths
            _mig = _paths.migrate_legacy()
            if _mig:
                self._catat("data: migrated %d legacy files -> %s"
                            % (len(_mig), _paths.saved_dir()))
        except Exception:                              # noqa: BLE001
            pass
        self._apply_preset(self._preset)
        # Chrome-style organize windows: snap controller + restore saved
        # Custom arrangement on a plain start-up (no --preset given).
        if os.environ.get("DV_NO_SNAP") != "1":
            self.snap = snapctl.SnapController(self)
            self.snap.attach()
        if os.environ.get("DV_NO_SNAP") != "1" and \
                getattr(args, "preset", None) in (None, "custom"):
            self._apply_custom()

    # ------------------------------------------------- geometri jendela
    def _muatkan_ke_layar(self):
        """Pastikan jendela SELALU muat di area kerja layar (fix 15 Okt).

        Windows: font sistem + skala DPI memperbesar minimum layout sehingga
        jendela terbuka lebih besar dari layar (kanan/bawah terpotong, status
        bar hilang). Urutan upaya: (1) bila minimum melebihi layar, turunkan
        skala teks ke 1.0 lebih dulu (rail tetap terbaca); (2) muatkan ukuran
        ke area kerja; (3) sebagai upaya terakhir longgarkan minimumSize.
        """
        if QtWidgets.QApplication.platformName() == "offscreen":
            return                       # probe headless: ukuran bebas
        scr = self.screen() or QtWidgets.QApplication.primaryScreen()
        if scr is None:
            return
        av = scr.availableGeometry()
        # diagnostik lapangan: catat apa yang SEBENARNYA dilihat Qt (dpr!)
        if not getattr(self, "_muat_logged", False):
            self._muat_logged = True
            self._catat("display: dpr=%.2f · area %dx%d (%s)"
                        % (scr.devicePixelRatio(), av.width(), av.height(),
                           QtWidgets.QApplication.platformName()))
        app = QtWidgets.QApplication.instance()
        # tangga skala: pakai skala sekarang; bila minimum konten melebihi
        # layar, turunkan bertahap (rail & tengah scrollable — umumnya cukup
        # 1.0; 1.0 = ~1.5x efektif pada DPI 150%).
        for sk in (theme.SKALA, 1.0, 0.9, 0.8):
            if sk > theme.SKALA:
                continue
            m = self.minimumSizeHint()
            if m.width() <= av.width() and m.height() <= av.height():
                break
            if sk < theme.SKALA and app is not None:
                theme.set_skala(sk)
                theme.apply(theme.aktif(), app)
                self.layout().activate()
        m = self.minimumSizeHint()
        if m.width() > av.width() or m.height() > av.height():
            self.setMinimumSize(0, 0)
            self._catat("display: scale %.1f, content > screen %dx%d — scrollable"
                        % (theme.SKALA, av.width(), av.height()))
        elif theme.SKALA < 1.2:
            self._catat("display: text scale auto-set to %.1f (screen %dx%d)"
                        % (theme.SKALA, av.width(), av.height()))
        w, h, dx, dy, _longgar = hitung_geometri_jendela(
            av.width(), av.height(), m.width(), m.height())
        self.resize(w, h)
        self.move(av.x() + dx, av.y() + dy)

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
        # Teks brand ("Marine"/"Technology") DIKOSONGKAN atas permintaan pemilik
        # 13 Okt — header cukup logo (tanpa tulisan tambahan).
        baris.addStretch(1)
        lb = QtWidgets.QLabel("LAYOUT")
        lb.setObjectName("CtlLabel")
        theme.set_peran(lb, "faint")
        baris.addWidget(lb)
        self.seg_layout = U.Segmen(
            [(k, self.NAMA_PRESET[k],
              "apply the %s layout (applies immediately)" % self.NAMA_PRESET[k])
             for k in self.SPESIFIKASI]
            + [("custom", "Custom",
                "restore the hand-arranged Custom layout (saved automatically "
                "after each move)")])
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
        self.chip_ekf = W.Chip("ekf")        # kesehatan EKF
        # Baris chip = INFORMASI TUNGGAL (permintaan pemilik 17 Okt): chip REC &
        # FS DIHAPUS — REC sudah tampil di panel CAPTURE (tombol + animasi), FS
        # tidak perlu chip; sisa: LINK & EKF (banner besar membawa MODE/ARM).
        for c, teks in ((self.chip_link, "wait"), (self.chip_mode, "—"),
                        (self.chip_arm, "—"), (self.chip_pad, "off"),
                        (self.chip_ekf, "—")):
            self._chip(c, "ink_faint", teks)
        for c in (self.chip_link, self.chip_ekf):
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
        """Running safety monitor (leak latch · battery · failsafe · link)."""
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
            fr = self._disk_free_mb
            if fr is not None:
                if fr < MIN_FREE_TOP_MB:
                    issues.append(("krit", "TOPSIDE STORAGE FULL — REC/SNAP blocked; "
                                           "transfer/clean up"))
                elif fr < WARN_FREE_TOP_MB:
                    issues.append(("warn", "Topside storage low — %.1f GB free"
                                   % (fr / 1000.0)))
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
        self.sb_msg = buat("ready — waiting for stream & telemetry …")
        self.sb_link = buat("MAVLink: —")
        self.sb_batt = buat("BATT: —")
        self.sb_raw = buat("RAW: —")
        self.sb_pad = buat("PAD: —")
        self.sb.lay.addWidget(self.sb_msg, 1)
        self.sb.tambah_pemisah()
        for w in (self.sb_link, self.sb_batt, self.sb_raw, self.sb_pad):
            self.sb.lay.addWidget(w)
            self.sb.tambah_pemisah()
        self.sb_sesi = buat("session: " + os.path.basename(self.sesi))
        self.sb.lay.addWidget(self.sb_sesi)
        self.sb.tambah_pemisah()
        self.sb_jam = buat("--:--:--")
        self.sb.lay.addWidget(self.sb_jam)

    def _perbarui_statusbar(self):
        s = self.bridge.state
        self.sb_link.setText("MAVLink: %s" % ("ok" if s.get("link") else "waiting"))
        v = s.get("volt")
        rem = s.get("remaining")
        _est = _lipo_pct(v) if v else None
        self.sb_batt.setText("BATT: %s%s" % (
            "%.2f V" % v if v else "\u2014",
            (" / est %d%%" % _est[1] if _est and _est[1] is not None else "")
            + (" (veh %d%%)" % rem if rem is not None else "")))
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
        # Kolom TENGAH juga scrollable (fix 15 Okt): pada layar logis kecil
        # (DPI Windows 125-150%) konten tengah tidak lagi memaksa jendela
        # keluar dari layar — pengguna men-scroll kolom tengah, persis rail.
        self._mid_inner = QtWidgets.QWidget()
        self._lay_mid = QtWidgets.QVBoxLayout(self._mid_inner)
        self._lay_mid.setContentsMargins(0, 0, 0, 0)
        self._lay_mid.setSpacing(6)
        self.col_mid = QtWidgets.QScrollArea()
        self.col_mid.setWidgetResizable(True)
        self.col_mid.setWidget(self._mid_inner)
        self.col_mid.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.col_mid.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.col_right, self._lay_right = U.kolom_rel(340, 370)
        body.addWidget(self.col_left, 0)
        body.addWidget(self.col_mid, 1)
        body.addWidget(self.col_right, 0)

    def _apply_preset(self, name):
        if name == "custom":
            self._apply_custom()
            return
        if name not in self.SPESIFIKASI:
            name = self.PRESET_BAWAAN
        self._preset = name
        self._last_preset = name
        spec = copy.deepcopy(self.SPESIFIKASI[name])
        # FLOATING panels of a previous custom arrangement return to the
        # position where the user originally parked them ("asal").
        floats_old = dict(getattr(self, "_floats_state", {}) or {})
        for fkey in list(floats_old):
            asal = floats_old[fkey].get("asal") or []
            if len(asal) == 2:
                rail, idx = str(asal[0]), int(asal[1])
                lst = spec.get(rail, []) if rail in ("left", "right") \
                    else spec.setdefault("mid", [])
                if rail not in ("left", "right"):
                    lst.insert(min(len(lst), max(idx, 0)), (fkey, 0))
                else:
                    lst.insert(min(len(lst), max(idx, 0)), fkey)
        self._terapkan_tata(spec, {})
        try:
            self.seg_layout.set_dipilih(name)
        except Exception:                                  # noqa: BLE001
            pass

    def _terapkan_tata(self, spec, floats):
        """Apply an arrangement (spec + floats). Shared by presets and the
        Chrome-style custom state. Anti-stuck rule kept: hide EVERY panel
        first, then re-show only what is placed."""
        spec = dict(spec or {})
        try:
            lb = theme.px(spec.get("lebar_kiri", 350)) + theme.px(28)
            # rail KIRI dan KANAN sama lebar persis (proporsional & simetris)
            for kol in (self.col_left, self.col_right):
                kol.setFixedWidth(lb)
        except Exception:                                  # noqa: BLE001
            lb = theme.px(350) + theme.px(28)
        self.vis = {}
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
        if not any((item[1] if isinstance(item, tuple) else None) or
                   (isinstance(item, str) and item in ("cam_front", "params"))
                   for item in mid):
            self._lay_mid.addStretch(1)
        # floating windows (Chrome "pop out")
        self._floats_state = {}
        for fkey, fin in (floats or {}).items():
            p = self.P.get(fkey)
            if p is None:
                continue
            _on = bool(fin.get("on", True))
            if not p.isWindow():
                p.setParent(None)
                p.setWindowFlags(Qt.Window | Qt.CustomizeWindowHint |
                                 Qt.WindowTitleHint |
                                 Qt.WindowMinMaxButtonsHint |
                                 Qt.WindowCloseButtonHint)
                p.setWindowTitle(p.title.text() + " \u2014 DeepVertex Console")
            geo = fin.get("geo")
            try:
                if geo:
                    p.setGeometry(QtCore.QRect(*geo))
                else:
                    _panel_float_default(p)
            except Exception:                              # noqa: BLE001
                pass
            p.setVisible(_on)
            self._floats_state[fkey] = {
                "geo": list(p.frameGeometry().getRect()) if _on else list(geo or []),
                "on": _on, "asal": fin.get("asal")}
        self._active_spec = copy.deepcopy(spec)
        self._sinkron_menu()
        self._fade_masuk()

    # ------------------------------------------------------ custom layout
    def _apply_custom(self):
        got = self._tata_load()
        if got is None:
            return False
        spec, floats = got
        self._preset = "custom"
        self._terapkan_tata(spec, floats)
        return True

    def snapshot_tata(self):
        """(spec, floats) of the CURRENT arrangement for drag editing."""
        spec = copy.deepcopy(getattr(self, "_active_spec", {}) or {})
        floats = {}
        for key, p in self.P.items():
            if p.isWindow() and p.isVisible():
                fin = (getattr(self, "_floats_state", {}).get(key) or {})
                floats[key] = {"geo": list(p.frameGeometry().getRect()),
                               "on": True, "asal": fin.get("asal")}
        return spec, floats

    def _asal_of(self, key):
        spec = getattr(self, "_active_spec", {}) or {}
        for rail in ("left", "right"):
            lst = spec.get(rail, [])
            for i, it in enumerate(lst):
                if (it if isinstance(it, str) else it[0]) == key:
                    return [rail, i]
        for i, it in enumerate(spec.get("mid", [])):
            if it == key or (isinstance(it, tuple) and it[0] == key):
                return ["mid", i]
        return None

    def commit_drag(self, kind, key):
        """Snap-controller callback: dock/reorder `key` per zone `kind`."""
        spec, floats = self.snapshot_tata()
        if kind is None:
            return
        if kind[0] in ("above", "below"):
            target = kind[1]
            rail = "mid"
            pos = None
            for r_ in ("left", "right"):
                lst = spec.get(r_, [])
                for i, it in enumerate(lst):
                    if it == target:
                        rail, pos = r_, i
                        break
                if r_ == "left":
                    for _r in ("right",):
                        lst = spec.get("right", [])
                        for i, it in enumerate(lst):
                            if it == target:
                                rail, pos = "right", i
                                break
            if pos is None:
                mid = [it[0] if isinstance(it, tuple) else it
                       for it in spec.get("mid", [])]
                if target in mid:
                    rail, pos = "mid", mid.index(target)
            if pos is None:                       # target == row member?
                row = spec.get("mid_row") or []
                if target in row:
                    rail, pos = "mid", int(spec.get("row_di", len(mid)))
                else:
                    return
            if kind[0] == "below":
                pos += 1
        elif kind[0] == "rail":
            rail, pos = kind[1], len(spec.get(kind[1], []))
        else:                                     # ("end",) camera column
            rail, pos = "mid", len(mid if False else spec.get("mid", []))
        spec, floats = laysnap.move_into_spec(spec, floats, key, rail, pos)
        self.tata_apply(spec, floats)

    def commit_float(self, key):
        """Snap-controller callback: pop the panel out as a free window."""
        spec, floats = self.snapshot_tata()
        asal = self._asal_of(key)
        spec, floats = laysnap.float_in_spec(spec, floats, key)
        floats[key]["asal"] = asal
        self.tata_apply(spec, floats)

    def tata_apply(self, spec, floats):
        """Apply + persist as the new Custom arrangement (user rearranged)."""
        self._preset = "custom"
        self._terapkan_tata(spec, floats)
        self._tata_save(spec, floats)

    def _menu_tata_save(self):
        spec, floats = self.snapshot_tata()
        self._tata_save(spec, floats)
        self._preset = "custom"
        self._terapkan_tata(spec, floats)
        self.statusBar().showMessage(
            "Custom layout saved \u2014 it becomes the startup layout.", 4000)

    def _menu_tata_forget(self):
        self._tata_forget()
        self._floats_state = {}
        self._apply_preset(self._last_preset or self.PRESET_BAWAAN)

    def _tata_json(self):
        return snapctl.custom_path(os.path.dirname(os.path.abspath(__file__)))

    def _tata_save(self, spec, floats):
        try:
            path = self._tata_json()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"spec": spec, "floats": floats}, f,
                          indent=2, ensure_ascii=False)
        except Exception:                                  # noqa: BLE001
            pass

    def _tata_load(self):
        try:
            with open(self._tata_json(), "r", encoding="utf-8") as f:
                data = json.load(f)
            spec = data.get("spec") or {}
            if not isinstance(spec, dict):
                return None
            # JSON makes tuples LISTS: restore (key, stretch) tuples for mid.
            mid = []
            for it in spec.get("mid", []):
                if isinstance(it, list):
                    it = tuple(it)
                mid.append(it)
            spec["mid"] = mid
            floats = data.get("floats") or {}
            return spec, floats
        except Exception:                                  # noqa: BLE001
            return None

    def _tata_forget(self):
        try:
            os.remove(self._tata_json())
        except Exception:                                  # noqa: BLE001
            pass

    def _sinkron_menu(self):
        """View-menu checkboxes follow the current arrangement."""
        for key, act in (getattr(self, "vis_act", {}) or {}).items():
            p = self.P.get(key)
            if p is not None:
                try:
                    act.blockSignals(True)
                    act.setChecked(p.isVisible())
                finally:
                    act.blockSignals(False)

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
        tile.mode_gambar = "contain"     #  nyata: frame kamera ditampilkan UTUH
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

        def _baris_batt(judul, tip):
            r = QtWidgets.QHBoxLayout()
            r.setSpacing(8)
            t2 = QtWidgets.QLabel(judul)
            t2.setObjectName("MicroLabel")
            t2.setFixedWidth(theme.px(40))
            bar = W.BarPersen(tinggi=10)
            bar.setToolTip(tip)
            pct = QtWidgets.QLabel("\u2014%")
            pct.setObjectName("MicroLabel")
            pct.setFixedWidth(theme.px(44))
            pct.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight |
                             QtCore.Qt.AlignmentFlag.AlignVCenter)
            r.addWidget(t2)
            r.addWidget(bar, 1)
            r.addWidget(pct)
            p.body_layout.addLayout(r)
            return bar, pct
        self.bar_batt, self.lbl_batt_pct = _baris_batt(
            "ardu", "ArduPilot remaining = ENERGY consumed since power-on / "
            "BATT_CAPACITY (not true state of charge). Green > 50% · amber "
            "25-50% · red < 25%.")
        self.bar_lipo, self.lbl_lipo_pct = _baris_batt(
            "lipo", "LiPo estimate from pack voltage — 3.70 V/cell (14.8 V "
            "on 4S) = 0% · 4.20 V/cell (16.8 V) = 100% (batas bawah "
            "ditetapkan pemilik 17 Okt).")
        lbl_stop = QtWidgets.QLabel(
            "Surface by 14.6 V \u00b7 degrades below 14.0 V \u00b7 never "
            "rest under 13.6 V")
        lbl_stop.setObjectName("MicroLabel")
        lbl_stop.setWordWrap(True)
        lbl_stop.setToolTip("LiPo-health guidance for the BlueROV2 + Tattu 4S "
                            "10000 mAh pack: surface by 14.6 V (\u2248 3.65 "
                            "V/cell under load, est \u2248 32%) or 25% "
                            "remaining; below 14.0 V (\u2248 3.50 V/cell, est "
                            "\u2248 15%) the pack degrades; never leave it "
                            "resting under 13.6 V. Defaults are written to "
                            "the vehicle by APPLY RECOMMENDED (BATT_LOW_VOLT="
                            "14.6, BATT_CRT_VOLT=14.0, remaining-capacity "
                            "2500/1500 mAh).")
        p.body_layout.addWidget(lbl_stop)
        self.lbl_batt_stop = lbl_stop
        P["power"] = p

        # ---- vehicle (pilot)
        p = W.Panel("VEHICLE (PILOT)", "arm · lights · tilt", domain="mission")
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.cb_mode = QtWidgets.QComboBox()
        self.cb_mode.addItems([n for n, _m in MODE_LIST])
        self.cb_mode.setToolTip("active ArduSub mode  (auto/position = coming soon)")
        b_go = QtWidgets.QPushButton("SET")
        b_go.setObjectName("BtnTiny")
        b_go.clicked.connect(lambda: self._buka_mode_nama(self.cb_mode.currentIndex()))
        b_arm = QtWidgets.QPushButton("ARM")
        b_arm.setObjectName("BtnTiny")
        b_arm.setToolTip("ARM — pool test first, make sure the props are safe")
        b_arm.clicked.connect(lambda: self._arm(True))
        b_dis = QtWidgets.QPushButton("DISARM")
        b_dis.setObjectName("BtnTiny")
        b_dis.clicked.connect(lambda: self._arm(False))
        b_surf = QtWidgets.QPushButton("SURFACE")
        b_surf.setObjectName("BtnTiny")
        b_surf.setToolTip("SURFACE mode — bring the ROV to the surface (emergency)")
        b_surf.clicked.connect(lambda: self._buka_mode_nama(3))
        _fm = QtGui.QFontMetrics(self.font())
        for _b in (b_go, b_arm, b_dis, b_surf):
            _b.setMinimumWidth(_fm.horizontalAdvance(_b.text()) + 22)
        row.addWidget(self.cb_mode, 1)
        row.addWidget(b_go)
        row.addStretch(1)
        p.body_layout.addLayout(row)
        rowb = QtWidgets.QHBoxLayout()
        rowb.setSpacing(6)
        for wdg in (b_arm, b_dis, b_surf):
            rowb.addWidget(wdg, 1)
        p.body_layout.addLayout(rowb)
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(6)
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
        b_tc.setToolTip("return camera tilt to 50% — automatic (reads back CamTilt)")
        b_tc.clicked.connect(self._reset_tilt)
        self.sl_tilt = QtWidgets.QSlider(Qt.Horizontal)
        self.sl_tilt.setRange(1100, 1900)
        self.sl_tilt.setValue(1500)
        self.sl_tilt.setToolTip("front camera tilt (RC8 channel — VERIFY in pool)")
        self.sl_tilt.valueChanged.connect(self._set_tilt)
        self.sp_lights = QtWidgets.QSpinBox()
        self.sp_lights.setRange(0, 100)
        self.sp_lights.setSuffix("%")
        self.sp_lights.setFixedWidth(theme.px(64))
        self.sp_lights.setToolTip("type 0-100 then Enter (same as the slider)")
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
        self.sp_tilt.setToolTip("type 0-100 then Enter. 50 = stop; "
                                ">50 tilts down, <50 tilts up (RC8 = rate command)")
        self.sp_tilt.valueChanged.connect(self._spin_tilt)
        row3x.addWidget(self.sp_tilt)
        row3x.addWidget(b_tc)
        p.body_layout.addLayout(row3x)
        row3 = QtWidgets.QHBoxLayout()
        row3.setSpacing(6)
        self.lbl_gain = QtWidgets.QLabel("gain: —")
        self.lbl_gain.setToolTip("pilot gain (param JS_GAIN_DEFAULT) — 100% = full")
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
        self.cb_cap.setToolTip("pilot command speed cap (stick scaling → MANUAL_CONTROL).\n"
                               "100% = full. Set from the GUI; does not write a vehicle param.")
        self.cb_cap.currentIndexChanged.connect(self._set_cap)
        row4.addWidget(lab4)
        row4.addWidget(self.cb_cap, 1)
        p.body_layout.addLayout(row4)
        P["vehicle"] = p

        # ---- setup (kalibrasi)
        p = W.Panel("SETUP · CALIBRATION", "QGC/BlueOS style vehicle setup", domain="mission")
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(6)
        tombol = (("GYRO", lambda: self._cal("gyro")),
                  ("ACCEL (6-POS)…", self._cal_accel),
                  ("COMPASS", lambda: self._cal("mag")),
                  ("LEVEL", lambda: self._cal("level")),
                  ("DEPTH (BARO)", lambda: self._cal("depth")),
                  ("PARAMS", lambda: self.bridge.queue_param_list()))
        for i, (nama, fn) in enumerate(tombol):
            b = QtWidgets.QPushButton(nama)
            b.setObjectName("BtnTiny")
            b.clicked.connect(fn)
            grid.addWidget(b, i // 2, i % 2)
        p.body_layout.addLayout(grid)
        info = QtWidgets.QLabel(
            "Recommended order (BlueOS/QGC): accel 6-pos \u2192 compass \u2192 "
            "LEVEL at a level surface \u2192 DEPTH (baro/ground-pressure) at "
            "the surface on EVERY dive start. Gyro runs automatically before "
            "accel. Restart after compass cal is recommended before arming.")
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
        b2 = QtWidgets.QPushButton("MOTOR DETECT MODE (spin direction, in water)")
        b2.setObjectName("BtnTiny")
        b2.clicked.connect(lambda: self.bridge.queue_mode(VO.MODE_MOTOR_DETECT))
        p.body_layout.addWidget(b2)
        ket = QtWidgets.QLabel("Bench test: PROPS OFF. In water: MOTOR DETECT "
                               "runs the routine automatically (ROV floats freely).")
        ket.setObjectName("MicroLabel")
        ket.setWordWrap(True)
        p.body_layout.addWidget(ket)
        P["motors"] = p

        # ---- failsafe
        p = W.Panel("FAILSAFE & BATTERY", "FS_/BATT_ params (QGC Safety style)", domain="mission")
        p.body_layout.addWidget(VO.FailsafePanel(self.bridge))
        b_fs = QtWidgets.QPushButton("APPLY RECOMMENDED (4S 10000 mAh)")
        b_fs.setObjectName("BtnTiny")
        b_fs.setToolTip("write safe default settings for the BlueROV2 + Tattu 4S 10Ah\n"
                        "(BATT_* + FS_*). Everything can still be changed manually afterwards.")
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
        p.body_layout.addSpacing(theme.px(3))     # margin bawah (audit: baris TEMP mepet)
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
        grid.setSpacing(6)
        self.btn_rec = {}
        self.btn_snap = {}
        self.btn_cam = {}
        self.cb_mode = {}
        for i, (nama, _j, _p) in enumerate(STREAM_DEF):
            lab = QtWidgets.QLabel(nama.upper())
            lab.setObjectName("MicroLabel")
            cb = QtWidgets.QComboBox()
            cb.addItems(["COMPRESSED", "RAW"])
            cb.setSizeAdjustPolicy(
                QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(7)
            cb.setMinimumWidth(56)
            # DINAMIS: biarkan combo MENYUSUT (rel sempit / device & OS lain) —
            # tanpa ini minW panel (470) > viewport rel (442) = isi terklip.
            cb.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                             cb.sizePolicy().verticalPolicy())
            cb.setToolTip("REC mode for this camera:\n"
                          "COMPRESSED = .mkv video through the BlueOS stream (small)\n"
                          "RAW = uncompressed frames via the onboard agent (big; "
                          "files live on the Pi until you transfer them\n"
                          "in the RAW CAPTURE panel → this session's raw/ folder)")
            cb.currentTextChanged.connect(lambda teks, n=nama: self._catat(
                "%s · REC mode → %s" % (n.upper(), teks)))
            b1 = QtWidgets.QPushButton("● REC")
            b1.setObjectName("BtnTiny")
            b1.setCheckable(True)
            b1.toggled.connect(lambda on, n=nama: self._toggle_rec(n, on))
            b2 = QtWidgets.QPushButton("SNAP")
            b2.setObjectName("BtnTiny")
            b2.setToolTip("quick photo → compressed/ folder (.png)\n"
                          "in RAW mode: one uncompressed frame is saved on "
                          "the Pi (transfer it in the RAW panel → raw/)")
            b2.clicked.connect(lambda _, n=nama: self._snap(n))
            b3 = QtWidgets.QPushButton("ON")
            b3.setObjectName("BtnTiny")
            b3.setCheckable(True)
            b3.setChecked(True)
            b3.setToolTip("camera ON/OFF ON THE ROBOT:\n"
                          "OFF stops this camera's stream inside BlueOS "
                          "(frees the Pi's CPU/USB); the preview follows.\n"
                          "ON re-creates the stream with the SAME saved "
                          "settings. RAW capture is unaffected.")
            b3.toggled.connect(lambda on, n=nama: self._toggle_cam(n, on))
            grid.addWidget(lab, i, 0)
            grid.addWidget(cb, i, 1)
            grid.addWidget(b1, i, 2)
            grid.addWidget(b2, i, 3)
            grid.addWidget(b3, i, 4)
            self.btn_rec[nama] = b1
            self.btn_snap[nama] = b2
            self.btn_cam[nama] = b3
            self.cb_mode[nama] = cb
        p.body_layout.addLayout(grid)
        self.lbl_sesi = QtWidgets.QLabel("session: " + os.path.basename(self.sesi))
        self.lbl_sesi.setObjectName("Faint")
        self.lbl_sesi.setWordWrap(True)
        self.lbl_sesi.setToolTip(self.sesi)
        p.body_layout.addWidget(self.lbl_sesi)
        row_sesi = QtWidgets.QHBoxLayout()
        b_sesi = QtWidgets.QPushButton("SESSION NAME…")
        b_sesi.setObjectName("BtnTiny")
        b_sesi.setToolTip("rename this session — the folder (video, photos, tlog) "
                          "moves together")
        b_sesi.clicked.connect(self._ganti_nama_sesi_dialog)
        row_sesi.addWidget(b_sesi)
        row_sesi.addStretch(1)
        p.body_layout.addLayout(row_sesi)
        self.lbl_save = QtWidgets.QLabel("save: compressed/ · raw/ · sensors.csv")
        self.lbl_save.setObjectName("Faint")
        self.lbl_save.setWordWrap(True)
        self.lbl_save.setToolTip(
            "Everything for this dive goes into\n%s\n"
            "  compressed/ ← .mkv + .png from the live streams\n"
            "  raw/        ← uncompressed chunks transferred from the Pi\n"
            "  sensors.csv ← sensor log topside 10 Hz (volt/curr/depth/"
            "attitude/…)" % self.sesi)
        p.body_layout.addWidget(self.lbl_save)
        def _baris_disk(judul, tip):
            r = QtWidgets.QHBoxLayout()
            r.setSpacing(6)
            t = QtWidgets.QLabel(judul)
            t.setObjectName("MicroLabel")
            t.setFixedWidth(theme.px(54))
            bar = W.BarPersen(tinggi=8)
            bar.setToolTip(tip)
            pct = QtWidgets.QLabel("\u2014%")
            pct.setObjectName("MicroLabel")
            pct.setFixedWidth(theme.px(44))
            pct.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight |
                             QtCore.Qt.AlignmentFlag.AlignVCenter)
            r.addWidget(t)
            r.addWidget(bar, 1)
            r.addWidget(pct)
            p.body_layout.addLayout(r)
            return bar, pct
        self.bar_disk, self.lbl_disk_pct = _baris_disk(
            "topside", "Free space on this laptop's capture disk (bar = % free).")
        self.bar_disk_pi, self.lbl_disk_pi_pct = _baris_disk(
            "robot", "Free space on the robot (Pi) RAW disk (bar = % free) — "
            "the agent auto-stops below its min-free margin.")
        self.lbl_disk = QtWidgets.QLabel("disk topside: —")
        self.lbl_disk.setObjectName("Faint")
        p.body_layout.addWidget(self.lbl_disk)
        self.lbl_disk_pi = QtWidgets.QLabel("robot (Pi): —")
        self.lbl_disk_pi.setObjectName("Faint")
        self.lbl_disk_pi.setWordWrap(True)
        self.lbl_disk_pi.setToolTip(
            "Robot storage + RAW estimate (from the onboard agent).\n"
            "Estimate uses the known FRONT raw rate (640×480 YUYV ≈ "
            "0.62 GB/min); the stereo changes it once connected. The agent "
            "auto-stops below its min-free margin.")
        p.body_layout.addWidget(self.lbl_disk_pi)
        self.lbl_est = QtWidgets.QLabel("estimate: —")
        self.lbl_est.setObjectName("Faint")
        self.lbl_est.setWordWrap(True)
        self.lbl_est.setToolTip("Estimated time until the SAFE LIMIT (%d MB margin);\n"
                                "REC and SNAP are blocked below it — transfer RAW to\n"
                                "the topside / clean up to unblock." % MIN_FREE_TOP_MB)
        p.body_layout.addWidget(self.lbl_est)
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
            # bar berpusat NOL (audit 13 Okt): QProgressBar mengisi dari kiri —
            # stick netral tampak "50% terisi" dan pilot salah baca nilainya.
            bar = W.BarTengah(tinggi=8)
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
        rp = RawPanel(self.args.raw_agent, outdir=os.path.join(self.sesi, "raw"))
        p.body_layout.addWidget(rp)
        p.panel_raw = rp
        P["raw"] = p

        # ---- peta misi: peta OSM (ala rovgui asli) + plot NED + estimasi
        p = W.Panel("MISSION MAP", "OSM · estimate (T2)", domain="mission")
        self.peta = PetaPanel()
        # default: perairan Kepulauan Seribu (dekat dermaga Pulau Pramuka).
        # Operator mengganti titik anchor kapan saja lewat "TARUH ANCHOR" (klik peta).
        _anchor = None
        try:
            import paths as _anch1
            with open(_anch1.aset_json("anchor.json"), encoding="utf-8") as _f:
                _anchor = json.load(_f)
        except Exception:                                   # noqa: BLE001
            _anchor = None
        if not (isinstance(_anchor, dict) and "lat" in _anchor
                and "lon" in _anchor):
            _anchor = {"lat": -5.7463, "lon": 106.6135, "heading_deg": 0.0,
                       "sumber": "default  (Kepulauan Seribu)"}
        else:
            _anchor = dict(_anchor)
            _anchor["sumber"] = "saved  (sesi sebelumnya)"
        self.peta.set_anchor(_anchor)
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
        rowp = U.FlowBox(spacing=6)   # membungkus saat kolom sempit (dinamis)
        self.b_peta = QtWidgets.QPushButton("MAP")
        self.b_peta.setObjectName("BtnTiny")
        self.b_peta.setCheckable(True)
        self.b_peta.setChecked(True)
        self.b_peta.setToolTip("OSM map (drag: hold-click · zoom: wheel)")
        self.b_ned = QtWidgets.QPushButton("NED")
        self.b_ned.setObjectName("BtnTiny")
        self.b_ned.setCheckable(True)
        self.b_ned.setToolTip("NED plot: survey area + planned lanes + track")
        self.b_peta.clicked.connect(lambda: self._pilih_peta(0))
        self.b_ned.clicked.connect(lambda: self._pilih_peta(1))
        b_anc = QtWidgets.QPushButton("PLACE ANCHOR")
        b_anc.setObjectName("BtnTiny")
        b_anc.setToolTip("pin mode: click a point on the map = local anchor (0,0)")
        b_anc.clicked.connect(self._siapkan_anchor)
        b_iku = QtWidgets.QPushButton("FOLLOW ROV")
        b_iku.setObjectName("BtnTiny")
        b_iku.setToolTip("map view follows the estimated ROV position again")
        b_iku.clicked.connect(lambda: self.peta.pusatkan_ke_robot())
        b_pos = QtWidgets.QPushButton("COORDS\u2026")
        b_pos.setObjectName("BtnTiny")
        b_pos.setToolTip("type the anchor latitude/longitude manually "
                         "(custom position — same effect as dropping the pin)")
        b_pos.clicked.connect(self._anchor_input)
        b_res = QtWidgets.QPushButton("RESET EST.")
        b_res.setObjectName("BtnTiny")
        b_res.setToolTip("zero the estimated position & track (start from here)")
        b_res.clicked.connect(self._reset_estimasi)
        self.lbl_pose = QtWidgets.QLabel("N 0.0  E 0.0")
        self.lbl_pose.setObjectName("MicroLabel")
        _fm2 = QtGui.QFontMetrics(self.font())
        for _b2 in (self.b_peta, self.b_ned, b_anc, b_pos, b_iku, b_res):
            _b2.setMinimumWidth(_fm2.horizontalAdvance(_b2.text()) + 18)
        rowp.tambah(self.b_peta)
        rowp.tambah(self.b_ned)
        rowp.tambah(b_anc)
        rowp.tambah(b_pos)
        rowp.tambah(b_iku)
        rowp.tambah(b_res)
        rowp.tambah(self.lbl_pose)
        p.body_layout.addWidget(rowp)
        P["peta"] = p

        # ---- preflight
        p = W.Panel("PRE-FLIGHT CHECK", "pre-flight checklist", domain="mission")
        pf = VO.PreflightPanel(self.bridge)
        p.body_layout.addWidget(pf)
        p.panel_preflight = pf
        P["preflight"] = p

        # ---- coming soon
        p = W.Panel("AUTONOMOUS · VIO", "STAGE 2 — AUV mode (coming soon)", domain="mission")
        p.body_layout.addWidget(W.Placeholder(
            "COMING SOON",
            "Autonomous, VIO and mapping run in Stage 2 (AUV mode, Jetson Orin Nano). "
            "ROV mode is ROV mode only: manual pilot + capture."))
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
                     ("preflight", "Pre-flight check"), ("setup", "Setup · calibration"),
                     ("motors", "Motor test"), ("failsafe", "Failsafe & battery"),
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
        to = v.addMenu("Organize windows")
        to.addAction("Re-apply saved Custom layout", self._apply_custom)
        to.addAction("Save current arrangement as Custom (startup layout)",
                     self._menu_tata_save)
        to.addAction("Forget Custom layout", self._menu_tata_forget)
        to.addSeparator()
        hint = to.addAction("Drag a panel header to move it \u2014 snap left / "
                            "right / above / below; drop outside to pop out.")
        hint.setEnabled(False)
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
        for teks_a, nilai_a in (("Text size: Normal (100%)", 1.0),
                                ("Text size: Large (120%)", 1.2),
                                ("Text size: Extra (140%)", 1.4)):
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
        kal = veh.addMenu("Calibration")
        kal.addAction("Gyroscope", lambda: self._cal("gyro"))
        kal.addAction("Accelerometer (6-position)…", self._cal_accel)
        kal.addAction("Compass (onboard)", lambda: self._cal("mag"))
        kal.addAction("Level horizon", lambda: self._cal("level"))
        kal.addAction("Barometer (ground pressure)", lambda: self._cal("baro"))
        veh.addAction("Download parameters", lambda: self.bridge.queue_param_list())
        veh.addSeparator()
        veh.addAction("Gamepad mapping…", self._padmap_dialog)
        konek = mb.addMenu("&Connection")
        konek.addAction("Connection settings (BlueOS / Ethernet)…",
                        self._menu_koneksi)
        hlp = mb.addMenu("&Help")
        hlp.addAction("Pre-dive checklist (Blue Robotics)", self._menu_checklist)
        hlp.addSeparator()
        hlp.addAction("Finish pending update (run installer)…",
                      self._menu_selesaikan_update)
        hlp.addAction("About", self._menu_about)

    def _menu_selesaikan_update(self):
        """Jalankan installer update yang sudah terunduh (jalan pemulihan)."""
        try:
            import updater as _upd
            p = _upd.ada_setup_tertunda()
        except Exception:                                  # noqa: BLE001
            p = None
        if not p:
            QtWidgets.QMessageBox.information(
                self, "Update", "No downloaded installer found.")
            return
        self._catat("launching pending installer: %s" % p)
        try:
            if os.name == "nt":
                os.startfile(p)                            # noqa: S606
            else:
                QtGui.QDesktopServices.openUrl(
                    QtCore.QUrl.fromLocalFile(p))
        except Exception as exc:                           # noqa: BLE001
            QtWidgets.QMessageBox.warning(
                self, "Update", "Could not launch the installer:\n%s" % exc)

    def _menu_koneksi(self):
        """Buka dialog pengaturan koneksi (BlueOS / ethernet / custom)."""
        try:
            import koneksi as _kon
            _kon.buat_dialog(self)
        except Exception as exc:                           # noqa: BLE001
            self._catat("connection: dialog failed: %s" % exc)

    def _toggle_panel(self, key, on):
        # FLOATING panel (window): plain visibility; docked: show/hide in
        # place; never-docked orphan: dock to the END of the right rail.
        w = self.P.get(key)
        if w is None:
            return
        if on and not w.isWindow() and w.parentWidget() is None:
            self.commit_drag(("rail", "right"), key)
            self.P[key].show()
            return
        w.setVisible(on)

    def _pill(self, lb, dot, label, value, warna_value, bg="raise_", border="line_hi",
              semua_terang=False):
        """Status pill: dot + small LABEL + VALUE — same visual language as chips."""
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
        """GAMEPAD status pill — the pilot must always see if the pad is connected."""
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
        """Change the GLOBAL text scale live (QSS + self-painted widgets)."""
        theme.set_skala(x)
        theme.apply(theme.aktif(), QtWidgets.QApplication.instance())
        self.lab_lights.setFixedWidth(theme.px(80))
        self.lab_tilt.setFixedWidth(theme.px(68))
        self._apply_preset(self._preset)
        for w_ in self.findChildren(QtWidgets.QWidget):
            w_.update()
        self._catat("ukuran teks -> %.0f%%" % (x * 100))

    def _menu_screenshot(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save screenshot",
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
            "Marine Technology \u2014 ROV console (ROV mode)\n"
            "BlueROV2 via BlueOS · MAVLink (pymavlink) · GStreamer · PySide6\n"
            "Design language: DeepVertex console (one design system).\n"
            "Features: gamepad teleop, calibration, motor test, failsafe, parameters, "
            "preflight, compressed + RAW onboard capture.")

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
                        "Continue ARM? (QGC 'Enforce Preflight' style)" % (total - belum, total))
                    if r != QtWidgets.QMessageBox.Yes:
                        self._catat("ARM cancelled — pre-dive checklist is not complete")
                        return
            except Exception:                              # noqa: BLE001
                pass
        self.bridge.queue_arm(bool(on))
        self._catat("command %s sent (pool test first)" % ("ARM" if on else "DISARM"))

    def _buka_mode_nama(self, idx):
        nama, angka = MODE_LIST[idx] if 0 <= idx < len(MODE_LIST) else MODE_LIST[0]
        self.bridge.queue_mode(angka)
        self._catat("mode command %s (%d) sent" % (nama, angka))

    def _spin_lights(self, v):
        """Typing a percent moves the slider (same code path as the slider)."""
        try:
            if self.sl_lights.value() != int(v):
                self.sl_lights.setValue(int(v))
        except Exception:                                  # noqa: BLE001
            pass

    def _spin_tilt(self, v):
        """Type percent (0-100) -> PWM 1100-1900; 50% = 1500 (stop)."""
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
        """Send a direct RATE command on ch8 (used by the RESET/center routine)."""
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
            self._catat("RESET tilt: sweeping back to 50% …")
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
                self._catat("tilt back to 50%% (readback %.0f%%)" % (x * 100))
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

    _RAW_KAM = {"front": "front", "stereo L": "left", "stereo R": "right"}

    def _raw_rec(self, nama, on):
        """REC dengan mode RAW: kendalikan agen onboard (chunk di Pi → panel RAW)."""
        kam = self._RAW_KAM.get(nama, "left")
        if on:
            _st = getattr(self.rawpanel, "last_status", {}) or {}
            _free = _st.get("disk_free_mb")
            _min = float(_st.get("min_free_mb") or 0)
            if _free is not None and float(_free) < _min + 200:
                self._catat("RAW REC blocked — the Pi is almost full (%s MB free). "
                            "Transfer RAW chunks to the topside first." % _free)
                self.btn_rec[nama].setChecked(False)
                return
            try:
                self.rawpanel.client.start(kam, self.rawpanel.sp_chunk.value())
                self._rec_robot.add(nama)
                self._anim_mulai(nama)
                self._catat("RAW REC %s (agent cam '%s') — files stay on the Pi; "
                            "transfer via RAW panel → raw/" % (nama, kam))
            except Exception as exc:                       # noqa: BLE001
                self._catat("RAW REC %s failed: %r" % (nama, exc))
                self.btn_rec[nama].setChecked(False)
        else:
            try:
                self.rawpanel.client.stop(kam)
                self._rec_robot.discard(nama)
                self._anim_stop(nama)
                self._catat("RAW REC %s stopped — pull the chunks in RAW CAPTURE → raw/"
                            % nama)
            except Exception as exc:                       # noqa: BLE001
                self._catat("RAW stop %s failed: %r" % (nama, exc))

    # ---------------------------------------------------------- animasi REC
    @staticmethod
    def _gaya_rec(fase):
        if fase:
            return ("QPushButton{background:#C22F26; color:#FFF3F1; "
                    "border:1px solid #FF867A; font-weight:600;}")
        return ("QPushButton{background:#6E1B16; color:#FFD9D3; "
                "border:1px solid #C22F26;}")

    def _anim_tick(self):
        self._anim_fase = not self._anim_fase
        for nama in list(self._rec_aktif) + list(self._rec_robot):
            b = self.btn_rec.get(nama)
            if b is not None:
                b.setStyleSheet(self._gaya_rec(self._anim_fase))

    def _anim_mulai(self, nama):
        b = self.btn_rec.get(nama)
        if b is not None:
            b.setStyleSheet(self._gaya_rec(True))
        if not self._anim_timer.isActive():
            self._anim_fase = True
            self._anim_timer.start(550)

    def _anim_stop(self, nama):
        b = self.btn_rec.get(nama)
        if b is not None:
            b.setStyleSheet("")          # INSTAN kembali normal saat selesai
        if not self._rec_aktif and not self._rec_robot:
            self._anim_timer.stop()
            self._anim_fase = False

    def _flash_snap(self, nama):
        b2 = self.btn_snap.get(nama)
        if b2 is not None:
            b2.setStyleSheet("QPushButton{background:#2E7D5B; color:#EAFFF5; "
                             "border:1px solid #5CC98F; font-weight:600;}")
            QtCore.QTimer.singleShot(320, lambda bb=b2: bb.setStyleSheet(""))

    def _toggle_rec(self, nama, on):
        cb = self.cb_mode.get(nama)
        if cb is not None and cb.currentText() == "RAW":
            self._raw_rec(nama, on)
            return
        if on and self._disk_blok:
            self._catat("REC blocked — topside storage full (< %d MB free). "
                        "Transfer/clean up first." % MIN_FREE_TOP_MB)
            self.btn_rec[nama].setChecked(False)
            return
        p = self.players[nama]
        if on:
            path = os.path.join(self.sesi, "compressed", "%s_%s" % (
                nama.replace(" ", "_"), time.strftime("%H%M%S")))
            if p.start_record(path):
                self._rec_aktif.add(nama)
                self._anim_mulai(nama)
                self.plot_misi.add_marker("rec")
            else:
                self.btn_rec[nama].setChecked(False)
        else:
            p.stop_record()
            self._rec_aktif.discard(nama)
            self._anim_stop(nama)

    def _mcm_pesan(self, tag, d):
        d = d or {}
        if d.get("ok"):
            self._catat("robot stream: %s %s" % (d.get("aksi", tag),
                                                  d.get("nama", "")))
        elif tag != "mcm_status":
            self._catat("robot stream: %s" % d.get("error", "failed"))

    def _toggle_cam(self, nama, on):
        """Preview kamera ON/OFF (hemat CPU laptop; robot & RAW tetap jalan)."""
        p = self.players.get(nama)
        tile = self.tile_kam.get(nama)
        try:
            self.cam_aktif[nama] = bool(on)
            # ROBOT-side (kehendak pemilik 17 Okt): ON/OFF = STREAM DI ROBOT
            try:
                if self.mcm is not None:
                    if on:
                        self.mcm.nyalakan(nama)
                    else:
                        self.mcm.matikan(nama)
            except Exception as exc2:                       # noqa: BLE001
                self._catat("robot stream %s: %r" % (nama, exc2))
            if on:
                if p is not None:
                    p.start()
                if tile is not None:
                    tile.set_on()
                self.btn_rec[nama].setEnabled(True)
                self.btn_snap[nama].setEnabled(True)
                self._catat("%s: camera ON — robot stream restarted "
                            "(preview resumed)" % nama.upper())
            else:
                if p is not None:
                    p.stop()
                if tile is not None:
                    tile.set_off("camera OFF — robot stream stopped "
                                 "(Pi CPU saved)", title="CAMERA OFF")
                cb = self.cb_mode.get(nama)
                if cb is None or cb.currentText() != "RAW":
                    b1 = self.btn_rec[nama]
                    b1.blockSignals(True)
                    b1.setChecked(False)
                    b1.blockSignals(False)
                    self._rec_aktif.discard(nama)
                    self.btn_rec[nama].setEnabled(False)
                # mode RAW: tombol REC tetap hidup (perekam ada di robot)
                self.btn_snap[nama].setEnabled(False)
                self._catat("%s: camera OFF — robot stream stopped "
                            "(Pi freed)" % nama.upper())
        except Exception as exc:                            # noqa: BLE001
            self._catat("%s: camera toggle failed: %r" % (nama.upper(), exc))
        b = self.btn_cam.get(nama) if hasattr(self, "btn_cam") else None
        if b is not None:
            b.blockSignals(True)
            b.setText("ON" if on else "OFF")
            b.blockSignals(False)

    def _ganti_nama_sesi_dialog(self):
        nama, ok = QtWidgets.QInputDialog.getText(
            self, "Session name", "Name this session:",
            text=self.nama_sesi or "dive")
        if ok:
            self._ganti_nama_sesi(nama)

    def _ganti_nama_sesi(self, nama):
        """Ganti nama FOLDER sesi berjalan (compressed/raw/tlog ikut karena satu folder)."""
        slug = _slug(nama) or "dive"
        lama = self.sesi
        base = os.path.basename(lama)
        bagian = base.split("_")
        prefix = "_".join(bagian[:2]) if len(bagian) >= 2 else base
        baru = os.path.join(os.path.dirname(lama), "%s_%s" % (prefix, slug))
        if baru == lama:
            self.nama_sesi = nama.strip()
            return
        n = 2
        while os.path.exists(baru):
            baru = os.path.join(os.path.dirname(lama),
                                "%s_%s-%d" % (prefix, slug, n))
            n += 1
        try:
            os.rename(lama, baru)                      # fd tlog tetap valid (inode)
            self.sesi = baru
            self.nama_sesi = nama.strip()
            try:
                self.bridge.tlog_path = os.path.join(baru, "mavlink.tlog")
            except Exception:                          # noqa: BLE001
                pass
            try:
                self.rawpanel.outdir = os.path.join(baru, "raw")
            except Exception:                          # noqa: BLE001
                pass
            nm = os.path.basename(baru)
            for _lbl in (getattr(self, "lbl_sesi", None),):
                if _lbl is not None:
                    _lbl.setText("session: " + nm)
                    _lbl.setToolTip(baru)
            try:
                self.lbl_save.setToolTip(
                    "Everything for this dive goes into\n%s\n"
                    "  compressed/ ← .mkv + .png from the live streams\n"
                    "  raw/        ← uncompressed chunks transferred from the Pi"
                    % baru)
            except Exception:                          # noqa: BLE001
                pass
            try:
                self.sb_sesi.setText("session: " + nm)
            except Exception:                          # noqa: BLE001
                pass
            self._catat("session renamed: " + nm)
        except OSError as exc:
            self._catat("session rename failed: %r" % exc)

    def _aksi_pad(self, aksi):
        """Tombol fisik REC/PHOTO di gamepad → jalur SAMA dengan tombol GUI."""
        try:
            if aksi == "record":
                b = self.btn_rec.get("front")
                if b is not None:
                    b.setChecked(not b.isChecked())    # memicu _toggle_rec via toggled
                    self._catat("pad: record %s (front)" %
                                ("start" if b.isChecked() else "stop"))
                else:
                    self._toggle_rec("front", True)
            elif aksi == "photo":
                self._snap("front")
                self._catat("pad: photo → front")
        except Exception as exc:                           # noqa: BLE001
            self._catat("pad action error: %r" % exc)

    def _snap(self, nama):
        cb = self.cb_mode.get(nama)
        if cb is not None and cb.currentText() == "RAW":
            kam = self._RAW_KAM.get(nama, "front")
            self._flash_snap(nama)
            try:
                self.rawpanel.client.snap(kam)
                self._catat("RAW snapshot %s — one uncompressed frame is "
                            "being saved on the Pi (agent cam '%s'); transfer "
                            "it in the RAW panel → raw/" % (nama, kam))
            except Exception as exc:                        # noqa: BLE001
                self._catat("RAW snapshot %s failed: %r" % (nama, exc))
            return
        if self._disk_blok:
            self._catat("SNAP blocked — topside storage full (< %d MB free). "
                        "Transfer/clean up first." % MIN_FREE_TOP_MB)
            return
        path = os.path.join(self.sesi, "compressed", "snap_%s_%s.png" % (
            nama.replace(" ", "_"), time.strftime("%H%M%S_")))
        ok = self.players[nama].snapshot(path)
        if ok:
            self._flash_snap(nama)
            self.plot_misi.add_marker("snap")
        self._catat("snapshot %s → %s" % (nama, path if ok else "FAILED (no frame yet)"))

    def _rec_selesai(self, aktif, path):
        if not aktif and path:
            self._catat("recording saved: " + path)
            try:
                mb = os.path.getsize(path) / 1e6
                self.lbl_cap_last.setText("last: %s · %.1f MB" %
                                          (os.path.basename(path), mb))
            except OSError:
                pass

    def _est_menit(self, free_mb, rate_mbpm):
        """Sisa menit sampai batas aman (None bila laju belum terukur)."""
        if free_mb is None or rate_mbpm is None or rate_mbpm < 5.0:
            return None
        sisa = free_mb - MIN_FREE_TOP_MB
        if sisa <= 0:
            return 0.0
        return sisa / rate_mbpm

    def _update_disk(self):
        import shutil
        try:
            # OVERRable utk uji/CI (deterministik): override menggantikan
            # shutil.disk_usage() — semua jalur guard tetap asli.
            _ov = getattr(self, "_disk_free_mb_override", None)
            du_free = (shutil.disk_usage(self.sesi).free / 1e6
                       if _ov is None else float(_ov))
            free_mb = du_free
            self._disk_free_mb = free_mb
            now = time.time()
            if self._disk_prev is not None and self._rec_aktif:
                dt = now - self._disk_prev[0]
                d = self._disk_prev[1] - free_mb
                if dt > 0.5 and d > 0:
                    inst = d / (dt / 60.0)
                    self._disk_rate_mbpm = (0.7 * self._disk_rate_mbpm + 0.3 * inst
                                            if self._disk_rate_mbpm else inst)
            self._disk_prev = (now, free_mb)
            self._disk_blok = free_mb < MIN_FREE_TOP_MB
        except OSError:
            return
        gb = (self._disk_free_mb or 0) / 1e3
        if self._disk_blok:
            self.lbl_disk.setText("disk topside: %.1f GB free — FULL (REC/SNAP "
                                  "blocked; transfer/clean up)" % gb)
        else:
            self.lbl_disk.setText("disk topside: %.1f GB free (block below %.1f GB)"
                                  % (gb, MIN_FREE_TOP_MB / 1000.0))
        # % + bar storage (topside & robot) — kehendak pemilik 17 Okt
        try:
            import shutil as _sh
            _du = _sh.disk_usage(self.sesi)
            _pct = 100.0 * _du.free / max(1, _du.total)
            self.bar_disk.setValue(_pct)
            self.lbl_disk_pct.setText("%.0f%%" % _pct)
            self.bar_disk.setWarna("#E8804A" if _pct < 5 else
                                   ("#E8C15C" if _pct < 15 else None))
        except Exception:                                   # noqa: BLE001
            pass
        # robot (Pi) storage + estimasi RAW (kehendak pemilik 17 Okt)
        try:
            _st = getattr(self.rawpanel, "last_status", {}) or {}
            _pi_free = _st.get("disk_free_mb")
            _tot = _st.get("disk_total_mb")
            if _pi_free is not None and _tot:
                _pipct = 100.0 * float(_pi_free) / max(1.0, float(_tot))
                self.bar_disk_pi.setValue(_pipct)
                self.lbl_disk_pi_pct.setText("%.0f%%" % _pipct)
                self.bar_disk_pi.setWarna("#E8804A" if _pipct < 5 else
                                          ("#E8C15C" if _pipct < 15 else None))
            else:
                self.bar_disk_pi.setValue(0)
                self.lbl_disk_pi_pct.setText("\u2014%")
            if _pi_free is not None:
                _pi_gb = float(_pi_free) / 1000.0
                _menit = int(float(_pi_free) / 619.0)   # 640x480 YUYV ~0.619 GB/min
                _jam, _mm = divmod(max(0, _menit), 60)
                _ket = ("~%d h %02d m" % (_jam, _mm)) if _jam else ("~%d min" % _mm)
                self.lbl_disk_pi.setText(
                    "robot (Pi): %.1f GB free · RAW est %s" % (_pi_gb, _ket))
                self.lbl_disk_pi.setStyleSheet(
                    "color:#E8804A;"
                    if _pi_free < float(_st.get("min_free_mb") or 0) else "")
            else:
                self.lbl_disk_pi.setText("robot (Pi): —  (agent offline?)")
        except Exception:                                   # noqa: BLE001
            pass
        est = self._est_menit(self._disk_free_mb, self._disk_rate_mbpm)
        snaps = int(max(0.0, (self._disk_free_mb or 0) - MIN_FREE_TOP_MB) / 2.5)
        if self._rec_aktif and est is not None:
            self.lbl_est.setText("estimate: ~%d min of recording left (@ %.0f MB/min) "
                                 "· ≈ %d snaps" % (int(est), self._disk_rate_mbpm, snaps))
        elif self._rec_aktif:
            self.lbl_est.setText("estimate: measuring write speed… · ≈ %d snaps" % snaps)
        else:
            self.lbl_est.setText("estimate: start REC to see time left · ≈ %d snaps" % snaps)

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
        # Unduh daftar parameter OTOMATIS sekali per koneksi (audit 14 Okt: chip MAG
        # sempat "FAULT (0 compass)" karena COMPASS_DEV_ID belum pernah diunduh).
        try:
            if s.get("link") and not self._param_diminta:
                self._param_diminta = True
                self.bridge.queue_param_list()
        except Exception:                                  # noqa: BLE001
            pass
        if not s.get("link"):
            self._param_diminta = False        # siap minta lagi saat tersambung ulang
        try:
            self._chip(self.chip_link, "ok" if s.get("link") else "warn",
                       "ok" if s.get("link") else "wait")
            self._chip(self.chip_mode, "ink_faint", str(s.get("mode") or "—"))
            self._chip(self.chip_arm, "warn" if s.get("armed") else "ok",
                       "ARMED" if s.get("armed") else "DISARMED")
            self._gaya_banner(s.get("mode"), s.get("armed"))
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
                    self._catat("camera tilt at start: %.0f%% — RESET to 50%%"
                                % (camt * 100))
                    self._reset_tilt()
                else:
                    self._catat("camera tilt at start: %.0f%% (near 50%%)"
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
            v = s.get("volt")
            lst = _lipo_pct(v)
            rem = s.get("remaining")
            if rem is not None:
                self.bar_batt.setValue(rem)
                self.lbl_batt_pct.setText("%.0f%%" % rem)
                _rm = float(rem)
                self.bar_batt.setWarna("#E8804A" if _rm < 25 else
                                       ("#E8C15C" if _rm < 50 else "#5CC98F"))
            else:
                self.bar_batt.setValue(0)
                self.lbl_batt_pct.setText("\u2014%")
            if lst is not None:
                _cells, soc = lst
                self.bar_lipo.setValue(soc)
                self.lbl_lipo_pct.setText("%d%%" % soc)
                self.bar_lipo.setWarna("#E8804A" if soc < 25 else
                                       ("#E8C15C" if soc < 50
                                        else "#5CC98F"))
            else:
                self.bar_lipo.setValue(0)
                self.lbl_lipo_pct.setText("\u2014%")
        except Exception:                                  # noqa: BLE001
            pass

    def _attitude(self, roll, pitch, _yaw):
        self._att_last = (roll, pitch, _yaw)
        self.horizon.set_attitude(roll, pitch)

    def _log_tick(self):
        """Baris sensor 10 Hz → sensors.csv (topside; robot tak dibebani)."""
        if getattr(self, "sensorlog", None) is None:
            return
        try:
            s = self.bridge.state or {}
            r, p, y = getattr(self, "_att_last", (None, None, None))
            self.sensorlog.tulis({
                "t_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
                "t_unix": "%.3f" % time.time(),
                "link": int(bool(s.get("link"))),
                "mode": s.get("mode") or "",
                "armed": int(bool(s.get("armed"))),
                "volt_v": s.get("volt"), "curr_a": s.get("curr"),
                "batt_pct": s.get("remaining"), "depth_m": s.get("depth"),
                "water_c": s.get("temp"), "press_abs": s.get("press_abs"),
                "roll_deg": r, "pitch_deg": p, "yaw_deg": y,
                "ekf_ok": (None if s.get("ekf_ok") is None
                           else int(bool(s.get("ekf_ok")))),
                "sys_healthy": s.get("sys_healthy"), "msgs": s.get("msgs"),
            })
        except Exception:                                   # noqa: BLE001
            pass

    def _text(self, teks, sev):
        self._catat(("⚠ " if sev <= 4 else "") + teks)

    def _catat(self, teks):
        try:
            self.sb_msg.setText(str(teks))
            self.log.appendPlainText(time.strftime("[%H:%M:%S] ") + str(teks))
        except Exception:                                  # noqa: BLE001
            pass
        # log file rapi (paths.py): data_dir()/logs/console.log (rotasi ringan)
        try:
            import paths as _paths
            _paths.putar_log()
            with open(_paths.file_log(), "a", encoding="utf-8") as f:
                f.write("%s [%s] %s\n" % (time.strftime("%Y-%m-%d"),
                                          time.strftime("%H:%M:%S"), teks))
        except Exception:                                  # noqa: BLE001
            pass

    # ================================================================ tutup
    def _sync_tiles(self):
        try:
            # kamera OFF = pesan tetap (frame telat tak boleh menghapusnya)
            for nama, on in getattr(self, "cam_aktif", {}).items():
                if on:
                    continue
                t = self.tile_kam.get(nama)
                alasan = "camera OFF — robot stream stopped (Pi CPU saved)"
                if t is not None and getattr(t, "_off", None) != alasan:
                    t.set_off(alasan, title="CAMERA OFF")
            for k in ("cam_front", "cam_left", "cam_right"):
                self.P[k].tile._sync_tinggi()
            # panel TENGAH = FIT RESOLUSI kamera (kehendak pemilik 17 Okt):
            # LEBAR penuh kolom + tinggi dikunci ke rasio frame ASLI
            # (lebar/rasio) sehingga video tampak PENUH tanpa border. Bila
            # kolom tidak cukup, KOLOM TENGAH yang menggulir — bukan kamera
            # yang mengecil. Rasio dibaca live dari frame → modular untuk
            # kamera/robot apa pun (4:3 / 16:10 / resolusi lain).
            t = self.P["cam_front"].tile
            if t.width() > 60 and t.parentWidget() is not None:
                ideal = t.heightForWidth(t.width())          # +18 chrome
                # chrome diukur HANYA saat tile penuh (tile == maximumHeight).
                # Kalau tile terjepit, pakai nilai tersimpan — tanpa penjaga
                # ini "slack" ikut terukur dan min-height RATCHET: panel
                # tumbuh 4 px tiap tick → konten turun terus (laporan pemilik
                # 17 Okt: "panel cam front turun-turun sendiri").
                if abs(t.height() - t.maximumHeight()) <= 2:
                    self._chrome_cam = self.P["cam_front"].height() - t.height()
                chrome = getattr(self, "_chrome_cam", None)
                if chrome is None:
                    chrome = 40
                butuh = ideal + max(0, chrome) + 2
                if abs(self.P["cam_front"].minimumHeight() - butuh) > 3:
                    self.P["cam_front"].setMinimumHeight(butuh)
        except Exception:                                  # noqa: BLE001
            pass

    _FS_REKOMENDASI = (
        ("BATT_CAPACITY", 10000), ("BATT_LOW_VOLT", 14.6), ("BATT_CRT_VOLT", 14.0),
        ("BATT_LOW_MAH", 2500), ("BATT_CRT_MAH", 1500),
        ("BATT_FS_LOW_ACT", 1), ("BATT_FS_CRT_ACT", 3),
        ("FS_GCS_ENABLE", 2), ("FS_GCS_TIMEOUT", 5),
        ("FS_PILOT_INPUT", 2), ("FS_PILOT_TIMEOUT", 3),
        ("FS_LEAK_ENABLE", 2),
    )

    def _apply_failsafe_defaults(self):
        """Failsafe + battery defaults for BlueROV2 + Tattu 4S 10000 mAh (editable)."""
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
        """Beginner SETUP guide (owner request 13 Oct): numbered steps."""
        p = W.Panel("SETUP GUIDE (NEW ROV)", "steps 1-7", domain="motion")
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
        self._catat("MAX SPEED %.0f%% — pilot commands scaled to %.0f%%"
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
        self._catat("map PIN mode: click a point on the map = local anchor (0,0)")

    def _anchor_input(self):
        """Input posisi anchor MANUAL (lat/lon) — kehendak pemilik 17 Okt."""
        a = dict(getattr(self.peta, "anchor", {}) or {})
        d = QtWidgets.QDialog(self)
        d.setWindowTitle("Anchor position \u2014 custom")
        form = QtWidgets.QFormLayout(d)
        sp_lat = QtWidgets.QDoubleSpinBox()
        sp_lat.setRange(-90.0, 90.0)
        sp_lat.setDecimals(6)
        sp_lat.setValue(float(a.get("lat", -5.7463)))
        sp_lon = QtWidgets.QDoubleSpinBox()
        sp_lon.setRange(-180.0, 180.0)
        sp_lon.setDecimals(6)
        sp_lon.setValue(float(a.get("lon", 106.6135)))
        tip = QtWidgets.QLabel(
            "Sets the local anchor (0,0) for the mission map \u2014 identical "
            "to dropping the pin on the map, but typed by hand.\n"
            "Current anchor: %.6f, %.6f (%s)" % (
                float(a.get("lat", 0.0)), float(a.get("lon", 0.0)),
                a.get("sumber", "\u2014")))
        tip.setObjectName("MicroLabel")
        tip.setWordWrap(True)
        form.addRow("Latitude", sp_lat)
        form.addRow("Longitude", sp_lon)
        form.addRow(tip)
        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        form.addRow(bb)
        if d.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._anchor_dipilih(sp_lat.value(), sp_lon.value())

    def _anchor_dipilih(self, lat, lon):
        a = dict(self.peta.anchor)
        a["lat"] = float(lat)
        a["lon"] = float(lon)
        a["sumber"] = "pin operator"
        self.peta.set_anchor(a)
        # View TETAP di titik yang dipilih — jangan melompat mengikuti pose
        # estimasi (laporan pemilik 17 Okt: "place anchor malah kepindah jauh").
        try:
            self.peta.pergi_ke(float(lat), float(lon), "anchor")
        except Exception:                                   # noqa: BLE001
            pass
        try:
            import paths as _anch2
            with open(_anch2.aset_json("anchor.json"), "w",
                      encoding="utf-8") as _f:
                json.dump(a, _f, indent=2)
        except Exception:                                   # noqa: BLE001
            pass
        self._catat("map anchor set: %.6f, %.6f (saved)" % (lat, lon))

    def _reset_estimasi(self):
        self.plot_misi.ancor()
        self.peta.bersihkan()
        self.peta.set_pose(0.0, 0.0, 0.0)
        self._catat("estimate & track reset to (0,0) at the anchor")

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
            self.snap.detach()
        except Exception:                                  # noqa: BLE001
            pass
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
            if getattr(self, "sensorlog", None) is not None:
                self.sensorlog.tutup()
        except Exception:                                   # noqa: BLE001
            pass
        try:
            with open(os.path.join(self.sesi, "session_end.json"), "w",
                      encoding="utf-8") as f:
                json.dump({"end": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
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


DESAIN_LEBAR, DESAIN_TINGGI = 1620, 1000    # ukuran jendela rancangan (px logis)


def hitung_geometri_jendela(av_w, av_h, min_w, min_h,
                            des_w=DESAIN_LEBAR, des_h=DESAIN_TINGGI):
    """Geometri jendela agar selalu berada di dalam area kerja layar.

    Return (w, h, dx, dy, longgar):
    * w/h = min(max(desain, minimum-konten), area-kerja);
    * dx/dy = offset tengah (0 bila mengisi penuh);
    * longgar = minimum-konten melebihi area kerja → pemanggil boleh
      melonggarkan setMinimumSize agar resize dihormati.
    """
    w = min(max(des_w, min_w), av_w)
    h = min(max(des_h, min_h), av_h)
    dx = 0 if w >= av_w else (av_w - w) // 2
    dy = 0 if h >= av_h else (av_h - h) // 2
    longgar = (min_w > av_w) or (min_h > av_h)
    return w, h, dx, dy, longgar


def main():
    # Skala DeepVertex berdiri SENDIRI (instruksi pemilik, 15 Okt): ablasi
    # scaling tampilan OS (Windows 125/150%) — kanvas aplikasi selalu 1:1 px
    # fisik; skala UI diatur aplikasi sendiri (--teks / menu Appearance) dan
    # jendela dimuatkan ke area kerja oleh _muatkan_ke_layar().
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
    os.environ.setdefault("QT_FONT_DPI", "96")
    os.environ.setdefault("QT_SCREEN_SCALE_FACTORS", "1")
    ap = argparse.ArgumentParser(
        description="DeepVertex Marine Technology \u2014 ROV console (ROV mode)")
    ap.add_argument("--mavlink", default="udpin:0.0.0.0:14550",
                    help="MAVLink endpoint (e.g. udpin:0.0.0.0:14550 / udp:192.168.2.2:14550)")
    ap.add_argument("--front-port", type=int, default=5600)
    ap.add_argument("--left-port", type=int, default=5601)
    ap.add_argument("--right-port", type=int, default=5602)
    ap.add_argument("--raw-agent", default="http://127.0.0.1:8765")
    ap.add_argument("--outdir", default=os.path.expanduser("~/DeepVertex-ROV-Captures"))
    ap.add_argument("--nama-sesi", "--session-name", default=None,
                    help="session folder name (e.g. 'Coral Survey A'); "
                         "blank string = use 'dive'")
    ap.add_argument("--no-prompt", action="store_true",
                    help="never ask for a session name (automation)")
    ap.add_argument("--preset", default=None,
                    choices=("pilot-focus", "mapping", "setup", "capture",
                             "custom"),
                    help="layout preset (default: restore last saved Custom "
                         "layout, else Pilot Focus)")
    ap.add_argument("--tlog", default="auto",
                    help="'off' disabled; path to a tlog file; 'auto' = <session>/mavlink.tlog")
    ap.add_argument("--tema", default="ink", choices=tuple(theme.tampil()),
                    help="color palette (ink = dark) — switchable live in View > Appearance")
    ap.add_argument("--teks", type=float, default=1.2, metavar="SKALA",
                    help="text size scale (1.0/1.2/1.4) for field readability")
    ap.add_argument("--no-pad", action="store_true", help="do not enable gamepad teleop")
    ap.add_argument("--no-update", action="store_true",
                    help="skip the GitHub release check at startup (automation)")
    ap.add_argument("--preview-update", action="store_true",
                    help="preview the update dialog with demo data (UI review)")
    ap.add_argument("--shot", default=None, help="save a window screenshot to PATH (UI testing)")
    ap.add_argument("--shot-after", type=float, default=6.0, help="seconds before the screenshot")
    args = ap.parse_args()
    # Koneksi tersimpan (koneksi.json) = DEFAULT; argumen CLI tetap menang.
    try:
        import koneksi as _kon
        _kk = _kon.load()
        if _kk:
            _ada = lambda f: any(a == f or a.startswith(f + "=")
                                 for a in sys.argv[1:])
            for _o, _f, _cast in (("mavlink", "--mavlink", str),
                                  ("front_port", "--front-port", int),
                                  ("left_port", "--left-port", int),
                                  ("right_port", "--right-port", int),
                                  ("raw_agent", "--raw-agent", str)):
                if _kk.get(_o) is not None and not _ada(_f):
                    setattr(args, _o, _cast(_kk[_o]))
    except Exception:                                      # noqa: BLE001
        pass
    args.port_front = args.front_port
    args.port_stereo_L = args.left_port
    args.port_stereo_R = args.right_port

    app = QtWidgets.QApplication(sys.argv)
    # SINGLE INSTANCE (laporan 17 Okt: dua konsol berebut UDP 5600 -> video mati).
    _kunci_app = None
    try:
        import paths as _pp
        _kunci_app = QtCore.QLockFile(os.path.join(_pp.data_dir(),
                                                   "console.lock"))
        _kunci_app.setStaleLockTime(15000)
        if not _kunci_app.tryLock(3000):
            QtWidgets.QMessageBox.warning(
                None, "DeepVertex Marine Console",
                "The console is already running.\nClose the other window first "
                "(two instances fight over the video stream).")
            return 1
    except Exception:                                       # noqa: BLE001
        _kunci_app = None
    try:
        import wheelguard
        wheelguard.pasang(app)
    except Exception:                                       # noqa: BLE001
        pass
    # Identitas aplikasi (taskbar/dock): nama & app-id harus sama dgn
    # deepvertex-console.desktop supaya ikon & nama konsisten di GNOME.
    app.setApplicationName("DeepVertex Marine Technology Console")
    app.setApplicationDisplayName("DeepVertex Marine Technology Console")
    app.setDesktopFileName("deepvertex-console")
    try:
        _ikon_app = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "ikon", "deepvertex-app-icon.png")
        if os.path.exists(_ikon_app):
            app.setWindowIcon(QtGui.QIcon(_ikon_app))
    except Exception:                                      # noqa: BLE001
        pass
    _terapkan_tema(app, getattr(args, "tema", None), getattr(args, "teks", None))
    w = RovConsole(args)
    w.show()
    # Geometri SESUDAH show (minimum layout baru final di sini) + ulang sekali
    # setelah timer tile/peta settle — fix 15 Okt utk layar kecil/DPI besar.
    app.processEvents()
    w._muatkan_ke_layar()
    QtCore.QTimer.singleShot(1200, w._muatkan_ke_layar)
    # Banner hasil update sebelumnya (v1.0.9): sukses/gagal tak lagi senyap.
    try:
        import updater as _upd2
        _pesan_upd = _upd2.cek_hasil_update(__version__)
        if _pesan_upd:
            w._catat(_pesan_upd)
        _dihapus = _upd2.bersihkan_update_lama(__version__)
        if _dihapus:
            w._catat("update: removed superseded installer(s): %s"
                     % ", ".join(_dihapus))
        _setup_tertunda = _upd2.ada_setup_tertunda()
        if _setup_tertunda:
            w._catat("An update installer is already downloaded — finish it "
                     "from Help ▸ Finish pending update (or double-click: %s)"
                     % _setup_tertunda)
    except Exception:                                      # noqa: BLE001
        pass

    # GitHub release check (online; popup = user choice; offline = silent).
    if getattr(args, "preview_update", False):
        def _pratinjau_upd():
            try:
                import updater as _upd
                _upd.pratinjau(w, __version__)
            except Exception:                              # noqa: BLE001
                pass
        QtCore.QTimer.singleShot(1200, _pratinjau_upd)
    elif not getattr(args, "no_update", False) and \
            os.environ.get("DV_NO_UPDATE") != "1":
        def _cek_upd():
            try:
                import updater as _upd
                _upd.periksa_di_latar(w, __version__)
            except Exception:                              # noqa: BLE001
                pass
        QtCore.QTimer.singleShot(1800, _cek_upd)
    if args.shot:
        def _ambil_shot():
            pm = w.grab()
            ok = pm.save(args.shot)
            try:
                w._catat("shot: vis=%s %dx%d pm-null=%s ok=%s -> %s"
                         % (w.isVisible(), w.width(), w.height(),
                            pm.isNull(), ok, args.shot))
            except Exception:                                # noqa: BLE001
                pass
            if not ok:
                # jalur cadangan: folder data (kalau path utama ditolak sistem)
                try:
                    import paths as _sp
                    alt = os.path.join(_sp.data_dir(), "shot_fallback.png")
                    ok2 = pm.save(alt)
                    try:
                        w._catat("shot fallback: ok=%s -> %s" % (ok2, alt))
                    except Exception:                        # noqa: BLE001
                        pass
                except Exception:                            # noqa: BLE001
                    pass
            print("shot:", args.shot, "ok" if ok else "FAILED", flush=True)
        QtCore.QTimer.singleShot(int(args.shot_after * 1000), _ambil_shot)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
