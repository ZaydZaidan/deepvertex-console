"""vehicleops.py — operasi kendaraan T1 (gaya QGC): kalibrasi, motor test, failsafe,
browser parameter, dan panel PRE-FLIGHT CHECK.

Sumber perintah: dokumentasi ArduPilot/ArduSub + prosedur Blue Robotics
(software-setup & operation). Titik yang WAJIB diverifikasi di kolam ditandai VERIFY.

Catatan MAV_CMD (VERIFY = konfirmasi dokumen/uji hardware):
  MAV_CMD_PREFLIGHT_CALIBRATION (241): p1=1 gyro · p3=1 baro · p5=1 accel penuh,
      p5=2 level-horizon. Kompas onboard modern: MAV_CMD_DO_START_MAG_CAL (42424)
      dipakai QGC (hasil lewat MAG_CAL_PROGRESS/MAG_CAL_REPORT).
  MAV_CMD_ACCELCAL_VEHICLE_POS (42429): posisi 1..6 (LEVEL..BACK), tiap langkah
      di-ACK oleh autopilot; dibatalkan dengan posisi di luar 1..6.
  MAV_CMD_DO_MOTOR_TEST (209): p1=motor, p2=0 (persen), p3=throttle%, p4=detik,
      p5=1 (jumlah).
"""
import json
import os
import time

from PySide6 import QtCore, QtWidgets

import theme
import ui_common as U
import widgets as W
from maviface import MavBridge  # noqa: F401  (untuk anotasi/uirisan)

MODE_MANUAL = 19
MODE_STABILIZE = 0
MODE_ALT_HOLD = 2
MODE_SURFACE = 9
MODE_MOTOR_DETECT = 20

CMD_PREFLIGHT_CALIBRATION = 241
CMD_DO_START_MAG_CAL = 42424
CMD_ACCELCAL_VEHICLE_POS = 42429
CMD_DO_MOTOR_TEST = 209
CMD_DO_ACCEPT_MAG_CAL = 42425
CMD_DO_CANCEL_MAG_CAL = 42426

ACCEL_POSISI = (
    (1, "LEVEL", "Normal ROV position — feet down, hold still."),
    (2, "LEFT SIDE DOWN", "Roll the ROV so its LEFT side faces down."),
    (3, "RIGHT SIDE DOWN", "Roll the ROV so its RIGHT side faces down."),
    (4, "NOSE DOWN", "Point the nose down (90°)."),
    (5, "NOSE UP", "Point the nose up (90°)."),
    (6, "BACK / UPSIDE DOWN", "Flip the ROV — back faces down."),
)


def _judul_dialog(d, judul):
    d.setWindowTitle(judul)


# Folder khusus APLIKASI untuk berkas simpanan (profil failsafe, dump parameter).
PASTA_SIMPAN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved")
os.makedirs(PASTA_SIMPAN, exist_ok=True)


class SimpleCalDialog(QtWidgets.QDialog):
    """Dialog satu-perintah: gyro / baro / level-horizon. Kompas punya alur sendiri."""

    def __init__(self, bridge, jenis, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.jenis = jenis
        self._selesai = False
        _judul_dialog(self, "Calibration — %s" % jenis)
        self.setModal(True)
        self.resize(460, 210)
        v = QtWidgets.QVBoxLayout(self)
        info = {
            "gyro": ("GYROSCOPE",
                     "The ROV must be STILL and undisturbed during calibration (± 10 s)."),
            "baro": ("BAROMETER (GROUND PRESSURE)",
                     "Recommended at the START OF EVERY DIVE, at the surface (stable air pressure)."),
            "level": ("LEVEL HORIZON (trim)",
                      "Place the ROV FLAT on a truly level surface, then start."),
            "mag": ("KOMPAS (ONBOARD)",
                    "Lift the ROV and rotate it around ALL axes (pitch, roll, yaw) until "
                    "progress reaches 100%. Keep away from metal / buildings."),
        }[jenis]
        t = QtWidgets.QLabel(info[0])
        t.setObjectName("PanelTitle")
        ket = QtWidgets.QLabel(info[1])
        ket.setWordWrap(True)
        theme.set_peran(ket, "dim")
        self.lbl = QtWidgets.QLabel("waiting …")
        self.lbl.setObjectName("MicroLabel")
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(jenis == "mag")
        row = QtWidgets.QHBoxLayout()
        self.b_start = QtWidgets.QPushButton("START")
        self.b_start.setObjectName("BtnTiny")
        self.b_close = QtWidgets.QPushButton("CLOSE")
        self.b_close.setObjectName("BtnTiny")
        row.addStretch(1)
        row.addWidget(self.b_start)
        row.addWidget(self.b_close)
        v.addWidget(t)
        v.addWidget(ket)
        v.addWidget(self.lbl)
        v.addWidget(self.bar)
        v.addLayout(row)
        self.b_start.clicked.connect(self._mulai)
        self.b_close.clicked.connect(self.close)
        bridge.sig_ack.connect(self._ack)
        bridge.sig_text.connect(self._text)
        bridge.sig_mag_progress.connect(self._mag_progress)
        bridge.sig_mag_report.connect(self._mag_report)

    def _mulai(self):
        if self.jenis == "gyro":
            self.bridge.queue_command(CMD_PREFLIGHT_CALIBRATION, p1=1, tag="gyro-cal")
        elif self.jenis == "baro":
            self.bridge.queue_command(CMD_PREFLIGHT_CALIBRATION, p3=1, tag="baro-cal")
        elif self.jenis == "level":
            self.bridge.queue_command(CMD_PREFLIGHT_CALIBRATION, p5=2, tag="level-cal")
        elif self.jenis == "mag":
            # pola QGC: cancel dulu (flush report lama), lalu start.
            # p1=0 semua kompas · p2=1 retry internal · p3=1 autosave · p4=0 delay.
            self.bridge.queue_command(CMD_DO_CANCEL_MAG_CAL, p1=0, tag="mag-cancel")
            self.bridge.queue_command(CMD_DO_START_MAG_CAL, p1=0, p2=1, p3=1, p4=0,
                                      tag="mag-cal")
        self.lbl.setText("command sent — waiting for vehicle feedback …")
        self.b_start.setEnabled(False)

    def _ack(self, cmd, hasil, tag):
        if tag in ("gyro-cal", "baro-cal", "level-cal", "mag-cal"):
            nama = {0: "DITERIMA", 1: "DITOLAK SEMENTARA", 2: "DITOLAK",
                    3: "NOT SUPPORTED", 4: "FAILED", 5: "RUNNING"}.get(hasil, str(hasil))
            self.lbl.setText("balasan autopilot: %s" % nama)
            if hasil != 0:
                self.b_start.setEnabled(True)

    def _text(self, teks, sev):
        t = teks.lower()
        if any(k in t for k in ("calibration", "calibrat", "gyro", "baro", "level", "mag")):
            self.lbl.setText(teks[:120])
            if "success" in t or "complete" in t or "done" in t:
                self.lbl.setText("DONE: " + teks[:100])
                self._selesai = True

    def _mag_progress(self, pct):
        self.bar.setValue(int(pct))
        self.lbl.setText("compass calibration … %d%%" % int(pct))

    def _mag_report(self, d):
        # MAG_CAL_STATUS (MAVLink): 0 NOT_STARTED · 1/2 RUNNING · 3 SUCCESS ·
        # 4 FAILED · 5 BAD_ORIENTATION · 6 BAD_RADIUS · 7 BAD_FIT
        status = {0: "not started", 1: "running (1)", 2: "running (2)", 3: "SUCCESS",
                  4: "FAILED", 5: "BAD ORIENTATION", 6: "BAD RADIUS",
                  7: "FIT BURUK"}.get(d.get("cal_status"), "?")
        try:
            self.bar.setValue(100 if d.get("cal_status") == 3 else self.bar.value())
        except Exception:                                  # noqa: BLE001
            pass
        self.lbl.setText("compass calibration result: %s" % status)
        if d.get("cal_status") == 3:
            if not d.get("autosaved", 1):
                # sukses tapi BELUM tersimpan -> terima eksplisit (pola QGC)
                self.bridge.queue_command(CMD_DO_ACCEPT_MAG_CAL, p1=0, tag="mag-accept")
                self.lbl.setText("kompas SUKSES — menyimpan paksa (DO_ACCEPT_MAG_CAL) …")
            else:
                self.lbl.setText("compass SUCCESS & saved. Rebooting the autopilot is recommended "
                                 "before arming.")
            self.b_start.setEnabled(True)
            self._selesai = True


class AccelCalDialog(QtWidgets.QDialog):
    """Wizard 6 posisi (MAV_CMD_ACCELCAL_VEHICLE_POS) — alur ala QGC."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self._idx = 0
        _judul_dialog(self, "Accelerometer Calibration — 6 positions")
        self.setModal(True)
        self.resize(500, 260)
        v = QtWidgets.QVBoxLayout(self)
        t = QtWidgets.QLabel("AKSELEROMETER — 6 POSISI")
        t.setObjectName("PanelTitle")
        self.lbl_pos = QtWidgets.QLabel("")
        self.lbl_pos.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.lbl_ket = QtWidgets.QLabel("")
        self.lbl_ket.setWordWrap(True)
        theme.set_peran(self.lbl_ket, "dim")
        self.lbl_stat = QtWidgets.QLabel("press START. Hold the ROV STILL at each position until it advances.")
        self.lbl_stat.setObjectName("MicroLabel")
        self.lbl_stat.setWordWrap(True)
        row = QtWidgets.QHBoxLayout()
        self.b_start = QtWidgets.QPushButton("START")
        self.b_start.setObjectName("BtnTiny")
        self.b_next = QtWidgets.QPushButton("CONTINUE ▶")
        self.b_next.setObjectName("BtnTiny")
        self.b_next.setEnabled(False)
        self.b_cancel = QtWidgets.QPushButton("BATAL")
        self.b_cancel.setObjectName("BtnTiny")
        row.addStretch(1)
        row.addWidget(self.b_start)
        row.addWidget(self.b_next)
        row.addWidget(self.b_cancel)
        v.addWidget(t)
        v.addWidget(self.lbl_pos)
        v.addWidget(self.lbl_ket)
        v.addWidget(self.lbl_stat)
        v.addLayout(row)
        self.b_start.clicked.connect(self._mulai)
        self.b_next.clicked.connect(self._lanjut)
        self.b_cancel.clicked.connect(self._batal)
        bridge.sig_ack.connect(self._ack)
        bridge.sig_text.connect(self._text)

    def _tampilkan(self):
        pos, nama, ket = ACCEL_POSISI[self._idx]
        self.lbl_pos.setText("STEP %d/6 — %s" % (pos, nama))
        self.lbl_ket.setText(ket)

    def _mulai(self):
        self._idx = 0
        self._tampilkan()
        self._kirim()
        self.b_start.setEnabled(False)

    def _kirim(self):
        pos = ACCEL_POSISI[self._idx][0]
        self.bridge.queue_command(CMD_ACCELCAL_VEHICLE_POS, p1=pos, tag="accel-pos-%d" % pos)
        self.lbl_stat.setText("position %d sent — hold still …" % pos)

    def _lanjut(self):
        self._idx += 1
        if self._idx >= len(ACCEL_POSISI):
            self.lbl_stat.setText("ALL POSITIONS DONE ✓ (see the result in notifications)")
            self.b_next.setEnabled(False)
            return
        self._tampilkan()
        self._kirim()
        self.b_next.setEnabled(False)

    def _batal(self):
        # VERIFY: pembatalan = kirim posisi di luar 1..6
        self.bridge.queue_command(CMD_ACCELCAL_VEHICLE_POS, p1=7, tag="accel-cancel")
        self.lbl_stat.setText("permintaan batal dikirim")
        self.close()

    def _ack(self, cmd, hasil, tag):
        if cmd != CMD_ACCELCAL_VEHICLE_POS:
            return
        if hasil != 0:
            self.lbl_stat.setText("autopilot rejected this step (result=%d) — check the messages" % hasil)
            return
        if tag.startswith("accel-pos"):
            if self._idx + 1 < len(ACCEL_POSISI):
                self.lbl_stat.setText("posisi %d OK ✓ — siapkan posisi berikut." % (self._idx + 1))
                self.b_next.setEnabled(True)
            else:
                self.lbl_stat.setText("position 6 OK ✓ — waiting for the final result…")

    def _text(self, teks, sev):
        t = teks.lower()
        if "calibration" in t or "accel" in t:
            self.lbl_stat.setText(teks[:130])


class MotorTestDialog(QtWidgets.QDialog):
    """Motor test per-thruster (MAV_CMD_DO_MOTOR_TEST) — ala QGC/BlueOS PWM page."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        _judul_dialog(self, "Motor Test — BlueROV2 (6 thruster)")
        self.setModal(True)
        self.resize(520, 300)
        v = QtWidgets.QVBoxLayout(self)
        t = QtWidgets.QLabel("MOTOR TEST")
        t.setObjectName("PanelTitle")
        warn = QtWidgets.QLabel(
            "⚠ PROPS OFF / SUPPORTED for bench testing. Do not run thrusters in air "
            "> for more than a few seconds (bearings need water). To check spin DIRECTION in water use "
            "MOTOR DETECT mode. Tick 'safe' before testing.")
        warn.setWordWrap(True)
        theme.set_peran(warn, "warn")
        self.cb_aman = QtWidgets.QCheckBox("I have confirmed the thruster area is SAFE")
        grid = QtWidgets.QGridLayout()
        self.sp_motor = QtWidgets.QSpinBox()
        self.sp_motor.setRange(1, 8)
        self.sp_motor.setValue(1)
        self.sp_thr = QtWidgets.QSpinBox()
        self.sp_thr.setRange(5, 100)
        self.sp_thr.setValue(15)
        self.sp_thr.setSuffix(" %")
        self.sp_dur = QtWidgets.QSpinBox()
        self.sp_dur.setRange(1, 10)
        self.sp_dur.setValue(2)
        self.sp_dur.setSuffix(" s")
        grid.addWidget(QtWidgets.QLabel("Motor #"), 0, 0)
        grid.addWidget(self.sp_motor, 0, 1)
        grid.addWidget(QtWidgets.QLabel("Throttle"), 0, 2)
        grid.addWidget(self.sp_thr, 0, 3)
        grid.addWidget(QtWidgets.QLabel("Durasi"), 0, 4)
        grid.addWidget(self.sp_dur, 0, 5)
        row = QtWidgets.QHBoxLayout()
        self.b_test = QtWidgets.QPushButton("RUN MOTOR")
        self.b_test.setObjectName("BtnTiny")
        self.b_detect = QtWidgets.QPushButton("MODE MOTOR DETECT")
        self.b_detect.setObjectName("BtnTiny")
        self.b_detect.setToolTip("switch to MOTOR_DETECT mode (20): automatic spin-direction check in WATER, "
                                 "ROV armed — result via notifications")
        self.b_close = QtWidgets.QPushButton("CLOSE")
        self.b_close.setObjectName("BtnTiny")
        row.addStretch(1)
        row.addWidget(self.b_test)
        row.addWidget(self.b_detect)
        row.addWidget(self.b_close)
        self.lbl = QtWidgets.QLabel(
            "catatan: ArduSub mewajibkan ROV ARMED + safety switch dilepas; perintah dikirim "
            "ulang otomatis tiap 0,4 dtk (watchdog 500 ms). Nomor motor 0/1-based: VERIFY di bench.")
        self.lbl.setObjectName("MicroLabel")
        self.lbl.setWordWrap(True)
        v.addWidget(t)
        v.addWidget(warn)
        v.addWidget(self.cb_aman)
        v.addLayout(grid)
        v.addWidget(self.lbl)
        v.addLayout(row)
        self.b_test.clicked.connect(self._test)
        self.b_detect.clicked.connect(lambda: self.bridge.queue_mode(MODE_MOTOR_DETECT))
        self.b_close.clicked.connect(self.close)
        bridge.sig_ack.connect(self._ack)

    def _test(self):
        if not self.cb_aman.isChecked():
            self.lbl.setText("tick 'area safe' first.")
            return
        # ArduSub: p6=2 (ORDER_BOARD) WAJIB; p4 (timeout) DIABAIKAN Sub -> durasi
        # dijalankan di GUI, dengan kiriman ulang tiap 0,4 dtk (watchdog 500 ms).
        self._sisa = max(3, int(self.sp_dur.value() * 3))
        self.b_test.setEnabled(False)
        if not hasattr(self, "_timer_motor"):
            self._timer_motor = QtCore.QTimer(self)
            self._timer_motor.setInterval(400)
            self._timer_motor.timeout.connect(self._kirim_motor)
        self._kirim_motor()
        self._timer_motor.start()

    def _kirim_motor(self):
        if getattr(self, "_sisa", 0) <= 0:
            self._timer_motor.stop()
            self._hentikan_motor()
            self.b_test.setEnabled(True)
            return
        self._sisa -= 1
        self.bridge.queue_command(
            CMD_DO_MOTOR_TEST,
            p1=self.sp_motor.value(), p2=0, p3=self.sp_thr.value(),
            p4=self.sp_dur.value(), p5=1, p6=2,
            tag="motor-%d" % self.sp_motor.value())
        self.lbl.setText("motor %d @ %d%% — %d kiriman lagi …" %
                         (self.sp_motor.value(), self.sp_thr.value(), self._sisa))

    def _hentikan_motor(self):
        """Kirim perintah stop (throttle 0) — pengaman tambahan di atas watchdog."""
        try:
            self.bridge.queue_command(CMD_DO_MOTOR_TEST, p1=self.sp_motor.value(),
                                      p2=0, p3=0, p4=1, p5=1, p6=2, tag="motor-stop")
        except Exception:                                  # noqa: BLE001
            pass
        self.lbl.setText("motor test finished.")

    def _ack(self, cmd, hasil, tag):
        if cmd != CMD_DO_MOTOR_TEST:
            return
        nama = {0: "DITERIMA ✓", 1: "DITOLAK SEMENTARA", 2: "DITOLAK — cek kondisi senjata/disarm",
                3: "NOT SUPPORTED", 4: "FAILED"}.get(hasil, str(hasil))
        self.lbl.setText("motor test [%s]: %s" % (tag, nama))


class ParamBrowser(QtWidgets.QWidget):
    """Browser parameter sederhana (PARAM_REQUEST_LIST / PARAM_SET) — gaya QGC."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self._terisi = set()
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        row = QtWidgets.QHBoxLayout()
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText("filter (FS_ / BATT_ / RC)")
        self.filter.textChanged.connect(self._refilter)
        b_unduh = QtWidgets.QPushButton("RELOAD")
        b_unduh.setObjectName("BtnTiny")
        b_unduh.clicked.connect(self.bridge.queue_param_list)
        b_set = QtWidgets.QPushButton("SET…")
        b_set.setObjectName("BtnTiny")
        b_set.clicked.connect(self._set)
        b_save_p = QtWidgets.QPushButton("SAVE…")
        b_save_p.setObjectName("BtnTiny")
        b_save_p.setToolTip("write ALL loaded parameters to saved/params_<time>.json")
        b_save_p.clicked.connect(lambda: self.simpan())
        b_load_p = QtWidgets.QPushButton("LOAD…")
        b_load_p.setObjectName("BtnTiny")
        b_load_p.setToolTip("read a parameter file from saved/ and send as PARAM_SET")
        b_load_p.clicked.connect(lambda: self.muat())
        row.addWidget(self.filter, 1)
        row.addWidget(b_unduh)
        row.addWidget(b_set)
        row.addWidget(b_save_p)
        row.addWidget(b_load_p)
        v.addLayout(row)
        self.tabel = QtWidgets.QTableWidget(0, 2)
        self.tabel.setHorizontalHeaderLabels(["PARAMETER", "VALUE"])
        self.tabel.horizontalHeader().setStretchLastSection(False)
        self.tabel.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch)
        self.tabel.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tabel.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        v.addWidget(self.tabel, 1)
        _hp = QtWidgets.QLabel("save/load → saved/  (inside the app folder)")
        _hp.setObjectName("Faint")
        _hp.setToolTip(PASTA_SIMPAN)
        v.addWidget(_hp)
        self.lbl = QtWidgets.QLabel("click RELOAD to load parameters —")
        self.lbl.setObjectName("MicroLabel")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        bridge.sig_param.connect(self._param)
        bridge.sig_params_done.connect(
            lambda n: self.lbl.setText("done — %d parameters loaded" % n))

    def simpan(self, path=None):
        """Dump EVERY row shown in the table to JSON (default: saved/params_<time>.json)."""
        d = {}
        for i in range(self.tabel.rowCount()):
            it0 = self.tabel.item(i, 0)
            it1 = self.tabel.item(i, 1)
            if it0 is None or it1 is None:
                continue
            try:
                d[it0.text()] = float(it1.text())
            except (TypeError, ValueError):
                continue
        if path is None:
            nama = "params_%s.json" % time.strftime("%Y%m%d_%H%M%S")
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save parameters", os.path.join(PASTA_SIMPAN, nama), "JSON (*.json)")
        if not path:
            return 0
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, sort_keys=True)
        self.lbl.setText("saved %d parameters: %s" % (len(d), path))
        return len(d)

    def muat(self, path=None):
        """Read a parameter file (default: from saved/) and send every entry as PARAM_SET."""
        if path is None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Load parameters", PASTA_SIMPAN, "JSON (*.json)")
        if not path:
            return 0
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as exc:                           # noqa: BLE001
            self.lbl.setText("failed to load file: %r" % exc)
            return 0
        n = 0
        for nama, val in d.items():
            try:
                self.bridge.queue_param_set(str(nama), float(val))
                n += 1
            except (TypeError, ValueError):
                continue
        self.lbl.setText("loaded %d parameters from %s — sent as PARAM_SET" % (n, path))
        return n

    def _baris(self, nama):
        for i in range(self.tabel.rowCount()):
            if self.tabel.item(i, 0).text() == nama:
                return i
        return -1

    def _param(self, nama, nilai):
        self._terisi.add(nama)
        f = self.filter.text().strip().upper()
        if f and f not in nama.upper():
            return
        i = self._baris(nama)
        if i < 0:
            i = self.tabel.rowCount()
            self.tabel.insertRow(i)
            self.tabel.setItem(i, 0, QtWidgets.QTableWidgetItem(nama))
            self.tabel.setItem(i, 1, QtWidgets.QTableWidgetItem(""))
        self.tabel.item(i, 1).setText("%.6g" % nilai)
        if len(self._terisi) % 25 == 0:
            self.lbl.setText("memuat … %d parameter" % len(self._terisi))

    def _refilter(self):
        self.tabel.setRowCount(0)
        for nama in sorted(self._terisi):
            val = self.bridge.params.get(nama)
            if val is not None:
                self._param(nama, val)

    def _set(self):
        baris = self.tabel.currentRow()
        if baris < 0:
            self.lbl.setText("select a parameter row first")
            return
        nama = self.tabel.item(baris, 0).text()
        lama = self.tabel.item(baris, 1).text()
        val, ok = QtWidgets.QInputDialog.getDouble(
            self, "Set parameter", "%s (now %s) → new value:" % (nama, lama),
            0.0, -1e9, 1e9, 6)
        if ok:
            self.bridge.queue_param_set(nama, val)
            self.lbl.setText("PARAM_SET %s = %g dikirim" % (nama, val))


class FailsafePanel(QtWidgets.QWidget):
    """Setting failsafe + baterai (subset param penting, gaya QGC Safety page)."""

    BARIS = (
        ("FS_GCS_ENABLE", "Failsafe GCS", "combo",
         [("Nonaktif", 0), ("Warning", 1), ("Disarm", 2), ("ALT_HOLD", 3), ("SURFACE", 4)],
         "Action when the GCS heartbeat is lost (this GUI = GCS). VERIFY in the pool."),
        ("FS_GCS_TIMEOUT", "Timeout GCS", "spin", (1, 30, 1),
         "seconds without a heartbeat before failsafe. Default 5 s."),
        ("FS_PILOT_INPUT", "Failsafe pilot input", "combo",
         [("Nonaktif", 0), ("Warning", 1), ("Disarm", 2)],
         "Action when pilot input (GCS joystick) is lost."),
        ("FS_PILOT_TIMEOUT", "Timeout pilot input", "spin", (1, 30, 1),
         "seconds without pilot input before failsafe."),
        ("FS_LEAK_ENABLE", "Failsafe kebocoran", "combo",
         [("Nonaktif", 0), ("Warning", 1), ("SURFACE", 2)],
         "The Navigator has a leak sensor — SURFACE is strongly recommended."),
        ("BATT_LOW_VOLT", "Low battery (V)", "spin", (8.0, 30.0, 0.1),
         "BlueRobotics: recover at 12.5 V; never below 12.0 V (4S)."),
        ("BATT_LOW_MAH", "Low battery (mAh)", "spin", (0, 30000, 100),
         "0 = disabled."),
        ("BATT_FS_LOW_ACT", "Low battery action", "combo",
         [("Nonaktif", 0), ("Warning", 1), ("Disarm", 2), ("SURFACE", 3)],
         "VERIFY: opsi Surface di firmware Anda."),
        ("BATT_CRT_VOLT", "Critical battery (V)", "spin", (8.0, 30.0, 0.1),
         "Ambang layer kedua."),
        ("BATT_CRT_MAH", "Critical battery (mAh)", "spin", (0, 30000, 100), "0 = disabled."),
        ("BATT_FS_CRT_ACT", "Critical battery action", "combo",
         [("Nonaktif", 0), ("Warning", 1), ("Disarm", 2), ("SURFACE", 3)],
         "VERIFY: opsi Surface di firmware Anda."),
        ("ARMING_SKIPCHK", "Arming checks to SKIP", "combo",
         [("Run ALL checks (recommended)", 0), ("Skip barometer", 1),
          ("Skip compass", 2), ("Skip INS", 8), ("Skip battery", 128)],
         "ArduSub 4.7+: bitmask of checks to SKIP (0 = run all). "
         "Older firmware uses ARMING_CHECK with the OPPOSITE meaning — VERIFY."),
    )

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.wdg = {}
        self._awal = {}
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(5)
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(6)
        for i, (nama, label, jenis, opsi, tip) in enumerate(self.BARIS):
            lab = QtWidgets.QLabel(label)
            lab.setObjectName("MicroLabel")
            lab.setToolTip(tip if opsi is not None else "")
            if jenis == "combo":
                w = QtWidgets.QComboBox()
                for teks, val in opsi if opsi else []:
                    w.addItem(teks, val)
            else:
                lo, hi, step = opsi
                w = QtWidgets.QDoubleSpinBox()
                w.setRange(lo, hi)
                w.setSingleStep(step)
                w.setDecimals(1)
            w.setToolTip(tip)
            grid.addWidget(lab, i, 0)
            grid.addWidget(w, i, 1)
            self.wdg[nama] = w
        v.addLayout(grid)
        row = QtWidgets.QHBoxLayout()
        b_muat = QtWidgets.QPushButton("LOAD")
        b_muat.setObjectName("BtnTiny")
        b_muat.setToolTip("pull the current values from the vehicle (parameter cache)")
        b_muat.clicked.connect(self.muat)
        b_terap = QtWidgets.QPushButton("TERAPKAN")
        b_terap.setObjectName("BtnTiny")
        b_terap.setToolTip("send every value shown to the autopilot")
        b_terap.clicked.connect(self.terapkan)
        b_sim = QtWidgets.QPushButton("SAVE FILE")
        b_sim.setToolTip("write the current values to saved/failsafe.json "
                         "(inside the app folder)")
        b_sim.setObjectName("BtnTiny")
        b_sim.clicked.connect(self.simpan)
        b_ld = QtWidgets.QPushButton("LOAD FILE")
        b_ld.setToolTip("read a profile from saved/ (inside the app folder)")
        b_ld.setObjectName("BtnTiny")
        b_ld.clicked.connect(self.muat_file)
        row.addStretch(1)
        for b in (b_muat, b_terap, b_sim, b_ld):
            row.addWidget(b)
        v.addLayout(row)
        self.lbl = QtWidgets.QLabel("loading values from the vehicle …")
        self.lbl.setObjectName("MicroLabel")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        _hint = QtWidgets.QLabel("save/load → saved/  (inside the app folder)")
        _hint.setObjectName("Faint")
        _hint.setWordWrap(True)
        _hint.setToolTip("Files are written to and read from:\n%s" % PASTA_SIMPAN)
        v.addWidget(_hint)
        bridge.sig_param.connect(self._param)

    def _param(self, nama, nilai):
        if nama in self.wdg:
            w = self.wdg[nama]
            if isinstance(w, QtWidgets.QComboBox):
                idx = w.findData(int(round(nilai)))
                if idx >= 0:
                    w.setCurrentIndex(idx)
                self._awal[nama] = idx
            else:
                w.setValue(float(nilai))
                self._awal[nama] = float(nilai)

    def muat(self):
        self.lbl.setText("loading … (values may be missing until the parameter list is downloaded)")

    def terapkan(self):
        n = 0
        for nama, w in self.wdg.items():
            if isinstance(w, QtWidgets.QComboBox):
                val = w.currentData()
            else:
                val = w.value()
            self.bridge.queue_param_set(nama, val)
            n += 1
        self.lbl.setText("%d parameter dikirim (PARAM_SET) — cek ACK/notifikasi" % n)

    def simpan(self, path=None):
        """Save the failsafe profile (default: app folder saved/failsafe.json)."""
        d = {}
        for nama, w in self.wdg.items():
            d[nama] = w.currentData() if isinstance(w, QtWidgets.QComboBox) else w.value()
        if path is None:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save failsafe", os.path.join(PASTA_SIMPAN, "failsafe.json"),
                "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)
            self.lbl.setText("saved: %s" % path)

    def muat_file(self, path=None):
        if path is None:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Load failsafe", PASTA_SIMPAN, "JSON (*.json)")
        if not path:
            return
        p = path
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            for nama, val in d.items():
                if nama in self.wdg:
                    self._param(nama, float(val))
            self.lbl.setText("loaded from file: %s (click APPLY to send)" % p)
        except Exception as exc:                           # noqa: BLE001
            self.lbl.setText("failed to load file: %r" % exc)


CHECKLIST_PREDIVE = (
    ("ballast", "Tighten / rotate-check the ballast weights"),
    ("rangka", "Pull side panels — frame must not wobble"),
    ("kabel", "Pull the cable bundle at the 14-hole end cap"),
    ("tether_thimble", "Pull & twist the tether thimble"),
    ("penetrator", "Check all penetrators are snug (by hand)"),
    ("vent", "Vent plug terpasang"),
    ("batt_tube", "Press & rotate battery tubes (sealed shut)"),
    ("sekrup", "Sekrup end cap + dome kencang (visual)"),
    ("thruster", "Tarik semua thruster"),
    ("fairing", "Wiggle the fairing"),
    ("lumen", "Tarik lampu Lumen"),
    ("seal", "Radial seals greased & undamaged (visual)"),
    ("area", "Area thruster bebas & ROV mode MANUAL"),
    ("tilt", "Camera tilt & lights work"),
    ("thruster_test", "Thruster test in water (<30 s), then DISARM"),
)


class PreflightPanel(QtWidgets.QWidget):
    """PRE-FLIGHT CHECK: status hidup dari kendaraan + checklist manual pre-dive resmi."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.sumber = {}                    # diisi konsol: players/gamepad/rawpanel/sesi
        self.cb = {}
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        # ---- status hidup
        self.chip = {}
        self.chip_bar = U.FlowBox(spacing=4)
        for k, nama in (("link", "LINK"), ("mode", "MODE"), ("arm", "ARM"),
                        ("batt", "BATT"), ("leak", "LEAK"), ("pad", "PAD"),
                        ("kamera", "CAM"), ("prearm", "PRE-ARM")):
            c = W.Chip(nama)
            try:
                c.set_state("ink_faint", "—")
            except Exception:                              # noqa: BLE001
                pass
            self.chip[k] = c
            self.chip_bar.tambah(c)
        v.addWidget(self.chip_bar)

        # ---- SENSORS: pastikan SEMUA sensor tersambung & sehat; yang error
        # disebutkan NAMANYA (permintaan pemilik 13 Okt).
        tl = QtWidgets.QLabel("SENSORS (robot):")
        tl.setObjectName("MicroLabel")
        v.addWidget(tl)
        self.chip_sensor = {}
        self.sensor_txt = {}
        self.chip_bar_s = U.FlowBox(spacing=4)
        for k, nama, tip in (
                ("s_gyro", "GYRO", "3D gyro health from SYS_STATUS — FAULT = check IMU / "
                                   "run gyro calibration"),
                ("s_accel", "ACCEL", "3D accelerometer health — FAULT = recalibrate "
                                     "(ACCEL 6-POS) / check IMU"),
                ("s_mag", "MAG", "Compass health + detected compass count (COMPASS_DEV_ID*) — "
                                 "FAULT = recalibrate compass / check wiring"),
                ("s_baro", "BARO", "Barometer (absolute pressure) health — FAULT = depth "
                                   "reading will be wrong"),
                ("s_batt", "BATTERY", "Battery monitor health (voltage/current) — FAULT = "
                                      "check the power module"),
                ("s_motor", "MOTOR", "Motor outputs (ESC/PWM) health — FAULT = check "
                                     "thrusters / wiring"),
                ("s_rc", "RC IN", "RC receiver input — 'n/a' is normal when flying with the "
                                  "GCS gamepad only")):
            c = W.Chip(nama)
            c.setToolTip(tip)
            try:
                c.set_state("ink_faint", "—")
            except Exception:                              # noqa: BLE001
                pass
            self.chip_sensor[k] = c
            self.chip_bar_s.tambah(c)
        v.addWidget(self.chip_bar_s)

        self.lbl_status = QtWidgets.QLabel("waiting for data …")
        self.lbl_status.setObjectName("MicroLabel")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

        # ---- checklist manual
        t = QtWidgets.QLabel("CHECKLIST PRE-DIVE (prosedur Blue Robotics):")
        t.setObjectName("MicroLabel")
        v.addWidget(t)
        for key, teks in CHECKLIST_PREDIVE:
            c = QtWidgets.QCheckBox(teks)
            c.setToolTip("Tick after checking (saved per session)")
            c.toggled.connect(self._simpan_ck)
            self.cb[key] = c
            v.addWidget(c)
        row = QtWidgets.QHBoxLayout()
        b_r = QtWidgets.QPushButton("RESET")
        b_r.setObjectName("BtnTiny")
        b_r.clicked.connect(lambda: self._set_semua(False))
        b_a = QtWidgets.QPushButton("SEMUA ✓")
        b_a.setObjectName("BtnTiny")
        b_a.clicked.connect(lambda: self._set_semua(True))
        row.addStretch(1)
        row.addWidget(b_r)
        row.addWidget(b_a)
        v.addLayout(row)
        v.addStretch(1)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._perbarui)
        self._timer.start(1000)

    # ------------------------------------------------------------------ util
    def _set_semua(self, on):
        for c in self.cb.values():
            c.setChecked(on)

    def _sesi_path(self):
        s = self.sumber.get("sesi") or ""
        return os.path.join(s, "preflight.json") if s else ""

    def _simpan_ck(self):
        p = self._sesi_path()
        if not p:
            return
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump({k: c.isChecked() for k, c in self.cb.items()}, f, indent=2)
        except OSError:
            pass

    def muat_ck(self):
        p = self._sesi_path()
        if p and os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                for k, val in d.items():
                    if k in self.cb:
                        self.cb[k].setChecked(bool(val))
            except Exception:                              # noqa: BLE001
                pass

    def _set_sensor(self, key, warna, teks):
        self.sensor_txt[key] = "%s" % teks
        c = self.chip_sensor.get(key)
        if c is None:
            return
        try:
            c.set_state(warna, teks)
        except Exception:                              # noqa: BLE001
            pass

    def _set(self, key, warna, teks):
        c = self.chip.get(key)
        if c is None:
            return
        try:
            c.set_state(warna, teks)
        except Exception:                              # noqa: BLE001
            pass

    # ------------------------------------------------------------------ tick
    def _perbarui(self):
        s = dict(self.bridge.state)
        # link & mode & arm
        self._set("link", "ok" if s.get("link") else "err",
                  "ok" if s.get("link") else "lost")
        self._set("mode", "ink_faint", str(s.get("mode") or "—"))
        self._set("arm", "warn" if s.get("armed") else "ok",
                  "ARMED" if s.get("armed") else "disarm")
        # baterai
        v = s.get("volt")
        if v is None:
            self._set("batt", "warn", "—")
        elif v >= 13.0:
            self._set("batt", "ok", "%.1f V" % v)
        elif v >= 12.5:
            self._set("batt", "warn", "%.1f V" % v)
        else:
            self._set("batt", "err", "%.1f V — RECOVER" % v)
        # leak dari STATUSTEXT
        txt = (s.get("last_text") or "").lower()
        if "leak" in txt:
            self._set("leak", "err", "LEAK!")
        else:
            self._set("leak", "ok", "ok")
        # gamepad
        pad = self.sumber.get("gamepad")
        if pad is not None:
            self._set("pad", "ok" if pad._konek else "warn",
                      "ready" if pad._konek else "none")
        # kamera
        players = self.sumber.get("players") or {}
        hidup = sum(1 for p in players.values() if p._qimg is not None)
        self._set("kamera", "ok" if hidup >= 2 else ("warn" if hidup else "err"),
                  "%d/%d" % (hidup, len(players)))
        # pre-arm: bit kesehatan SYS_STATUS (MAV_SYS_STATUS_PREARM_CHECK) = pola QGC;
        # pesan "PreArm:" terbaru menang bila severity warning/lebih buruk.
        if ("prearm" in txt or "pre-arm" in txt) and s.get("last_text_sev", 6) <= 4:
            self._set("prearm", "err", "cek notifikasi")
        elif s.get("prearm_ok") is False:
            self._set("prearm", "warn", "not ready")
        elif s.get("prearm_ok"):
            self._set("prearm", "ok", "ready")
        else:
            self._set("prearm", "ink_faint", "—")
        try:
            self.chip["prearm"].setToolTip(str(s.get("last_text") or ""))
        except Exception:                                  # noqa: BLE001
            pass

        # ---- SENSORS per-sensor (mask bit SYS_STATUS MAVLink)
        pres = s.get("sys_present")
        hea = s.get("sys_healthy")

        def _st(mask, mandatory=True):
            if pres is None or hea is None:
                return ("ink_faint", "—")
            if not (int(pres) & mask):
                return ("err", "NOT FOUND") if mandatory else ("ink_faint", "n/a")
            if int(hea) & mask:
                return ("ok", "ok")
            return ("err", "FAULT")

        n_mag = 0
        for prm in ("COMPASS_DEV_ID", "COMPASS_DEV_ID2", "COMPASS_DEV_ID3"):
            try:
                if int(float(self.bridge.params.get(prm) or 0)) > 0:
                    n_mag += 1
            except (TypeError, ValueError):
                pass
        for key, mask, wajib in (("s_gyro", 1 << 0, True), ("s_accel", 1 << 1, True),
                                 ("s_mag", 1 << 2, True), ("s_baro", 1 << 3, True),
                                 ("s_batt", 1 << 24, False),
                                 ("s_motor", 1 << 15, False),
                                 ("s_rc", 1 << 16, False)):
            warna, teks = _st(mask, wajib)
            if key == "s_mag" and warna == "ok":
                if n_mag:
                    teks = "ok (%d compass)" % n_mag
                else:
                    warna, teks = "err", "FAULT (0 compass)"
            self._set_sensor(key, warna, teks)

        # ringkasan
        kurang = [k for k, c in self.cb.items() if not c.isChecked()]
        siap = (s.get("link") and v is not None and v >= 12.5 and "leak" not in txt)
        if siap and not kurang:
            self.lbl_status.setText("STATUS: SIAP BERENANG ✓ (checklist lengkap)")
        elif siap:
            self.lbl_status.setText("STATUS: system OK — %d checklist items not ticked" % len(kurang))
        else:
            self.lbl_status.setText("STATUS: NOT READY — check the red/amber chips above")
