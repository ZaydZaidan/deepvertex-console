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

ACCEL_POSISI = (
    (1, "DATAR (LEVEL)", "Posisi normal ROV — kaki menghadap bawah, tahan diam."),
    (2, "MIRING KIRI (LEFT)", "Putar ROV sehingga sisi KIRI menghadap bawah."),
    (3, "MIRING KANAN (RIGHT)", "Putar ROV sehingga sisi KANAN menghadap bawah."),
    (4, "HIDUNG BAWAH (NOSE DOWN)", "Tundukkan hidung ROV ke bawah (90°)."),
    (5, "HIDUNG ATAS (NOSE UP)", "Dongakkan hidung ROV ke atas (90°)."),
    (6, "PUNGGUNG BAWAH (BACK)", "Terlentangkan ROV — punggung menghadap bawah."),
)


def _judul_dialog(d, judul):
    d.setWindowTitle(judul)


class SimpleCalDialog(QtWidgets.QDialog):
    """Dialog satu-perintah: gyro / baro / level-horizon. Kompas punya alur sendiri."""

    def __init__(self, bridge, jenis, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.jenis = jenis
        self._selesai = False
        _judul_dialog(self, "Kalibrasi — %s" % jenis)
        self.setModal(True)
        self.resize(460, 210)
        v = QtWidgets.QVBoxLayout(self)
        info = {
            "gyro": ("GYROSCOPE",
                     "Pastikan ROV DIAM dan tidak diganggu saat kalibrasi (± 10 detik)."),
            "baro": ("BAROMETER (GROUND PRESSURE)",
                     "Disarankan SETIAP AWAL PENYELAMAN, dengan ROV di permukaan/tekanan atmosfer stabil."),
            "level": ("LEVEL HORIZON (trim)",
                      "Letakkan ROV DATAR di permukaan yang benar-benar level, lalu jalankan."),
            "mag": ("KOMPAS (ONBOARD)",
                    "Angkat ROV dan putar ke SEMUA sumbu (maju-mundur, samping, roll) sampai "
                    "progress 100%. Jauhkan dari logam/bangunan saat kalibrasi."),
        }[jenis]
        t = QtWidgets.QLabel(info[0])
        t.setObjectName("PanelTitle")
        ket = QtWidgets.QLabel(info[1])
        ket.setWordWrap(True)
        theme.set_peran(ket, "dim")
        self.lbl = QtWidgets.QLabel("menunggu …")
        self.lbl.setObjectName("MicroLabel")
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setVisible(jenis == "mag")
        row = QtWidgets.QHBoxLayout()
        self.b_start = QtWidgets.QPushButton("MULAI")
        self.b_start.setObjectName("BtnTiny")
        self.b_close = QtWidgets.QPushButton("TUTUP")
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
            # VERIFY: p1=0 (semua kompas), p2=1 (retry), p3=1 (autosave), p4=0
            self.bridge.queue_command(CMD_DO_START_MAG_CAL, p1=0, p2=1, p3=1, p4=0,
                                      tag="mag-cal")
        self.lbl.setText("perintah dikirim — tunggu umpan balik kendaraan …")
        self.b_start.setEnabled(False)

    def _ack(self, cmd, hasil, tag):
        if tag in ("gyro-cal", "baro-cal", "level-cal", "mag-cal"):
            nama = {0: "DITERIMA", 1: "DITOLAK SEMENTARA", 2: "DITOLAK",
                    3: "TIDAK DIDUKUNG", 4: "GAGAL", 5: "BERJALAN"}.get(hasil, str(hasil))
            self.lbl.setText("balasan autopilot: %s" % nama)
            if hasil != 0:
                self.b_start.setEnabled(True)

    def _text(self, teks, sev):
        t = teks.lower()
        if any(k in t for k in ("calibration", "calibrat", "gyro", "baro", "level", "mag")):
            self.lbl.setText(teks[:120])
            if "success" in t or "complete" in t or "done" in t:
                self.lbl.setText("SELESAI: " + teks[:100])
                self._selesai = True

    def _mag_progress(self, pct):
        self.bar.setValue(int(pct))
        self.lbl.setText("kalibrasi kompas … %d%%" % int(pct))

    def _mag_report(self, d):
        status = {0: "belum mulai", 1: "berjalan", 2: "SUKSES", 3: "GAGAL",
                  4: "ORIENTASI BURUK", 5: "RADIUS BURUK"}.get(d.get("cal_status"), "?")
        try:
            self.bar.setValue(100 if d.get("cal_status") == 2 else self.bar.value())
        except Exception:                                  # noqa: BLE001
            pass
        self.lbl.setText("hasil kalibrasi kompas: %s" % status)
        if d.get("cal_status") == 2:
            self.b_start.setEnabled(False)


class AccelCalDialog(QtWidgets.QDialog):
    """Wizard 6 posisi (MAV_CMD_ACCELCAL_VEHICLE_POS) — alur ala QGC."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self._idx = 0
        _judul_dialog(self, "Kalibrasi Akselerometer — 6 posisi")
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
        self.lbl_stat = QtWidgets.QLabel("tekan MULAI. Tahan ROV DIAM di tiap posisi sampai lanjut.")
        self.lbl_stat.setObjectName("MicroLabel")
        self.lbl_stat.setWordWrap(True)
        row = QtWidgets.QHBoxLayout()
        self.b_start = QtWidgets.QPushButton("MULAI")
        self.b_start.setObjectName("BtnTiny")
        self.b_next = QtWidgets.QPushButton("LANJUT ▶")
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
        self.lbl_pos.setText("LANGKAH %d/6 — %s" % (pos, nama))
        self.lbl_ket.setText(ket)

    def _mulai(self):
        self._idx = 0
        self._tampilkan()
        self._kirim()
        self.b_start.setEnabled(False)

    def _kirim(self):
        pos = ACCEL_POSISI[self._idx][0]
        self.bridge.queue_command(CMD_ACCELCAL_VEHICLE_POS, p1=pos, tag="accel-pos-%d" % pos)
        self.lbl_stat.setText("posisi %d dikirim — tahan diam …" % pos)

    def _lanjut(self):
        self._idx += 1
        if self._idx >= len(ACCEL_POSISI):
            self.lbl_stat.setText("SEMUA POSISI SELESAI ✓ (lihat hasil di notifikasi)")
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
            self.lbl_stat.setText("autopilot menolak langkah ini (hasil=%d) — cek pesan" % hasil)
            return
        if tag.startswith("accel-pos"):
            if self._idx + 1 < len(ACCEL_POSISI):
                self.lbl_stat.setText("posisi %d OK ✓ — siapkan posisi berikut." % (self._idx + 1))
                self.b_next.setEnabled(True)
            else:
                self.lbl_stat.setText("posisi 6 OK ✓ — menunggu hasil akhir…")

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
            "⚠ LEPAS/SANGGA PROPELLER untuk uji di darat. Jangan menjalankan thruster di udara "
            "> beberapa detik (bearing butuh air). Untuk cek ARAH putaran di air pakai mode "
            "MOTOR DETECT. Tandai 'aman' dulu sebelum menguji.")
        warn.setWordWrap(True)
        theme.set_peran(warn, "warn")
        self.cb_aman = QtWidgets.QCheckBox("Saya sudah pastikan area thruster AMAN")
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
        self.b_test = QtWidgets.QPushButton("JALANKAN MOTOR")
        self.b_test.setObjectName("BtnTiny")
        self.b_detect = QtWidgets.QPushButton("MODE MOTOR DETECT")
        self.b_detect.setObjectName("BtnTiny")
        self.b_detect.setToolTip("ubah mode ke MOTOR_DETECT (20) untuk deteksi arah otomatis di air")
        self.b_close = QtWidgets.QPushButton("TUTUP")
        self.b_close.setObjectName("BtnTiny")
        row.addStretch(1)
        row.addWidget(self.b_test)
        row.addWidget(self.b_detect)
        row.addWidget(self.b_close)
        self.lbl = QtWidgets.QLabel("catatan: bila autopilot menolak (DENIED) saat disarmed, "
                                    "coba saat mode MANUAL & mengikuti prosedur keamanan — VERIFY di kolam.")
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
            self.lbl.setText("tandai 'area aman' dulu.")
            return
        self.bridge.queue_command(
            CMD_DO_MOTOR_TEST,
            p1=self.sp_motor.value(), p2=0, p3=self.sp_thr.value(),
            p4=self.sp_dur.value(), p5=1, tag="motor-%d" % self.sp_motor.value())
        self.lbl.setText("perintah motor %d dikirim …" % self.sp_motor.value())

    def _ack(self, cmd, hasil, tag):
        if cmd != CMD_DO_MOTOR_TEST:
            return
        nama = {0: "DITERIMA ✓", 1: "DITOLAK SEMENTARA", 2: "DITOLAK — cek kondisi senjata/disarm",
                3: "TIDAK DIDUKUNG", 4: "GAGAL"}.get(hasil, str(hasil))
        self.lbl.setText("motor test [%s]: %s" % (tag, nama))


class ParamBrowser(QtWidgets.QWidget):
    """Browser parameter sederhana (PARAM_REQUEST_LIST / PARAM_SET) — gaya QGC."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self._terisi = set()
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        row = QtWidgets.QHBoxLayout()
        self.filter = QtWidgets.QLineEdit()
        self.filter.setPlaceholderText("filter nama (mis. FS_ / BATT_ / RC)")
        self.filter.textChanged.connect(self._refilter)
        b_unduh = QtWidgets.QPushButton("UNDUH ULANG")
        b_unduh.setObjectName("BtnTiny")
        b_unduh.clicked.connect(self.bridge.queue_param_list)
        b_set = QtWidgets.QPushButton("SET NILAI …")
        b_set.setObjectName("BtnTiny")
        b_set.clicked.connect(self._set)
        row.addWidget(self.filter, 1)
        row.addWidget(b_unduh)
        row.addWidget(b_set)
        v.addLayout(row)
        self.tabel = QtWidgets.QTableWidget(0, 2)
        self.tabel.setHorizontalHeaderLabels(["PARAMETER", "NILAI"])
        self.tabel.horizontalHeader().setStretchLastSection(False)
        self.tabel.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.Stretch)
        self.tabel.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.tabel.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        v.addWidget(self.tabel, 1)
        self.lbl = QtWidgets.QLabel("klik UNDUH ULANG untuk memuat daftar parameter …")
        self.lbl.setObjectName("MicroLabel")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
        bridge.sig_param.connect(self._param)
        bridge.sig_params_done.connect(
            lambda n: self.lbl.setText("selesai — %d parameter dimuat" % n))

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
            self.lbl.setText("pilih baris parameter dulu")
            return
        nama = self.tabel.item(baris, 0).text()
        lama = self.tabel.item(baris, 1).text()
        val, ok = QtWidgets.QInputDialog.getDouble(
            self, "Set parameter", "%s (sekarang %s) → nilai baru:" % (nama, lama),
            0.0, -1e9, 1e9, 6)
        if ok:
            self.bridge.queue_param_set(nama, val)
            self.lbl.setText("PARAM_SET %s = %g dikirim" % (nama, val))


class FailsafePanel(QtWidgets.QWidget):
    """Setting failsafe + baterai (subset param penting, gaya QGC Safety page)."""

    BARIS = (
        ("FS_GCS_ENABLE", "Failsafe GCS", "combo",
         [("Nonaktif", 0), ("Peringatan", 1), ("Disarm", 2), ("ALT_HOLD", 3), ("SURFACE", 4)],
         "Aksi saat heartbeat GCS hilang (GUI ini = GCS). VERIFY untaian angka di kolam."),
        ("FS_GCS_TIMEOUT", "Timeout GCS", "spin", (1, 30, 1),
         "detik tanpa heartbeat sebelum failsafe. Default 5 dtk."),
        ("FS_PILOT_INPUT", "Failsafe pilot input", "combo",
         [("Nonaktif", 0), ("Peringatan", 1), ("Disarm", 2)],
         "Aksi saat input pilot (joystick GCS) hilang."),
        ("FS_PILOT_TIMEOUT", "Timeout pilot input", "spin", (1, 30, 1),
         "detik tanpa input pilot sebelum failsafe."),
        ("FS_LEAK_ENABLE", "Failsafe kebocoran", "combo",
         [("Nonaktif", 0), ("Peringatan", 1), ("SURFACE", 2)],
         "Navigator punya sensor bocor — sangat disarankan SURFACE."),
        ("BATT_LOW_VOLT", "Baterai rendah (V)", "spin", (8.0, 30.0, 0.1),
         "BlueRobotics: recover di 12.5 V; jangan di bawah 12.0 V (4S)."),
        ("BATT_LOW_MAH", "Baterai rendah (mAh)", "spin", (0, 30000, 100),
         "0 = nonaktif."),
        ("BATT_FS_LOW_ACT", "Aksi baterai rendah", "combo",
         [("Nonaktif", 0), ("Peringatan", 1), ("Disarm", 2), ("SURFACE", 3)],
         "VERIFY: opsi Surface di firmware Anda."),
        ("BATT_CRT_VOLT", "Baterai kritis (V)", "spin", (8.0, 30.0, 0.1),
         "Ambang layer kedua."),
        ("BATT_CRT_MAH", "Baterai kritis (mAh)", "spin", (0, 30000, 100), "0 = nonaktif."),
        ("BATT_FS_CRT_ACT", "Aksi baterai kritis", "combo",
         [("Nonaktif", 0), ("Peringatan", 1), ("Disarm", 2), ("SURFACE", 3)],
         "VERIFY: opsi Surface di firmware Anda."),
        ("ARMING_CHECK", "Arming check", "combo",
         [("Nonaktif", 0), ("Aktif (disarankan)", 1)],
         "Pemeriksaan pra-arm; biarkan aktif."),
    )

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.wdg = {}
        self._awal = {}
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(3)
        grid = QtWidgets.QGridLayout()
        grid.setSpacing(4)
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
        b_muat = QtWidgets.QPushButton("MUAT")
        b_muat.setObjectName("BtnTiny")
        b_muat.setToolTip("ambil nilai sekarang dari kendaraan (cache parameter)")
        b_muat.clicked.connect(self.muat)
        b_terap = QtWidgets.QPushButton("TERAPKAN")
        b_terap.setObjectName("BtnTiny")
        b_terap.setToolTip("kirim semua nilai yang tampil ke autopilot")
        b_terap.clicked.connect(self.terapkan)
        b_sim = QtWidgets.QPushButton("SIMPAN FILE")
        b_sim.setObjectName("BtnTiny")
        b_sim.clicked.connect(self.simpan)
        b_ld = QtWidgets.QPushButton("MUAT FILE")
        b_ld.setObjectName("BtnTiny")
        b_ld.clicked.connect(self.muat_file)
        row.addStretch(1)
        for b in (b_muat, b_terap, b_sim, b_ld):
            row.addWidget(b)
        v.addLayout(row)
        self.lbl = QtWidgets.QLabel("memuat nilai dari kendaraan …")
        self.lbl.setObjectName("MicroLabel")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)
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
        self.lbl.setText("memuat … (nilai mungkin belum ada bila daftar parameter belum diunduh)")

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

    def simpan(self):
        d = {}
        for nama, w in self.wdg.items():
            d[nama] = w.currentData() if isinstance(w, QtWidgets.QComboBox) else w.value()
        p, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Simpan failsafe", "failsafe.json",
                                                     "JSON (*.json)")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=2)
            self.lbl.setText("disimpan: %s" % p)

    def muat_file(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Muat failsafe", "",
                                                     "JSON (*.json)")
        if not p:
            return
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            for nama, val in d.items():
                if nama in self.wdg:
                    self._param(nama, float(val))
            self.lbl.setText("dimuat dari file: %s (klik TERAPKAN untuk kirim)" % p)
        except Exception as exc:                           # noqa: BLE001
            self.lbl.setText("gagal memuat file: %r" % exc)


CHECKLIST_PREDIVE = (
    ("ballast", "Kencangkan/putar cek pemberat ballast"),
    ("rangka", "Tarik panel samping & cek rangka tidak goyang"),
    ("kabel", "Tarik berkas kabel di end cap 14 lubang"),
    ("tether_thimble", "Tarik & putar thimble tether"),
    ("penetrator", "Cek semua penetrator tidak kendor (tangan)"),
    ("vent", "Vent plug terpasang"),
    ("batt_tube", "Tekan & putar tabung baterai (tutup rapat)"),
    ("sekrup", "Sekrup end cap + dome kencang (visual)"),
    ("thruster", "Tarik semua thruster"),
    ("fairing", "Goyang fairing"),
    ("lumen", "Tarik lampu Lumen"),
    ("seal", "Seal radial terlumasi & tidak rusak (visual)"),
    ("area", "Area thruster bebas & ROV mode MANUAL"),
    ("tilt", "Fungsi tilt kamera & lampu bekerja"),
    ("thruster_test", "Uji thruster di air (<30 dtk), lalu DISARM"),
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
        v.setSpacing(4)

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
        self.lbl_status = QtWidgets.QLabel("menunggu data …")
        self.lbl_status.setObjectName("MicroLabel")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

        # ---- checklist manual
        t = QtWidgets.QLabel("CHECKLIST PRE-DIVE (prosedur Blue Robotics):")
        t.setObjectName("MicroLabel")
        v.addWidget(t)
        for key, teks in CHECKLIST_PREDIVE:
            c = QtWidgets.QCheckBox(teks)
            c.setToolTip("Centang setelah diperiksa (tersimpan per-sesi)")
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
                  "terhubung" if s.get("link") else "putus")
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
            self._set("leak", "err", "BOCOR!")
        else:
            self._set("leak", "ok", "ok")
        # gamepad
        pad = self.sumber.get("gamepad")
        if pad is not None:
            self._set("pad", "ok" if pad._konek else "warn",
                      "siap" if pad._konek else "tidak ada")
        # kamera
        players = self.sumber.get("players") or {}
        hidup = sum(1 for p in players.values() if p._qimg is not None)
        self._set("kamera", "ok" if hidup >= 2 else ("warn" if hidup else "err"),
                  "%d/%d" % (hidup, len(players)))
        # pre-arm (pesan warning terbaru yang mengandung prearm/fail)
        if ("prearm" in txt or "pre-arm" in txt) and s.get("last_text_sev", 6) <= 4:
            self._set("prearm", "err", "cek notifikasi")
        else:
            self._set("prearm", "ok", "ok")

        # ringkasan
        kurang = [k for k, c in self.cb.items() if not c.isChecked()]
        siap = (s.get("link") and v is not None and v >= 12.5 and "leak" not in txt)
        if siap and not kurang:
            self.lbl_status.setText("STATUS: SIAP BERENANG ✓ (checklist lengkap)")
        elif siap:
            self.lbl_status.setText("STATUS: sistem OK — checklist %d item belum dicentang" % len(kurang))
        else:
            self.lbl_status.setText("STATUS: BELUM SIAP — periksa chip merah/kuning di atas")
