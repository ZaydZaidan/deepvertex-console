#!/usr/bin/env python3
"""ROV Mission Console — dashboard lokal untuk BlueROV2 + Stonefish + VINS-Fusion.

Panel:
  - Stonefish simulator view (jendela simulator ditanam ke dalam dashboard)
  - Kamera VIO kiri & kanan (720p mono8 dari relay)
  - Peta lintasan realtime: VIO (VINS-Fusion) vs ground truth sim, NED, skala 1:1
  - Telemetri ArduSub/Pixhawk (mode, armed, attitude, gyro, kedalaman, altitude,
    baterai, tekanan, suhu) + status VIO

Rencana pengembangan: peta lintasan akan digantikan peta ala QGroundControl yang
terhubung ke kontrol loop tertutup berbasis VIO.

Jalankan lewat run_dashboard.sh (menangani env ROS 2 + profil Fast DDS).
"""
import argparse
import math
import os
import sys
import time
import traceback
from datetime import datetime
from collections import deque

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

import theme
import widgets as W
RUN_ARCHIVE = "/home/zayd/Project BRIN VIO/RUN SIMULASI"

from procman import PARAMS, ProcManager
from stackui import StackUI
from xembed import SimEmbed

# Label loop auto-PID (harus sinkron dengan LOOPS di scripts/rov_pid_autotune.py)
TUNE_LOOPS = {
    "depth": 'depth (PSC_D_*: cascade ALT_HOLD)',
    "yaw": "heading/yaw (ATC_ANG_YAW_P, ATC_RAT_YAW_P/I/D)",
    "roll": "roll (ATC_ANG_RLL_P, ATC_RAT_RLL_P/I/D)",
    "pitch": "pitch (ATC_ANG_PIT_P, ATC_RAT_PIT_P/I/D)",
}


class TuneDialog(QtWidgets.QDialog):
    """Dialog opsi Auto-PID.

    Dipisah sebagai kelas supaya bisa diuji OFFSscreen tanpa `.exec()` (uji GUI tidak boleh
    menggantung menunggu klik): uji_gui_tuning.py membuat dialog, membaca/menyetel widget,
    lalu memanggil values().
    """

    def __init__(self, parent, loop="depth"):
        super().__init__(parent)
        self.loop = loop
        self.setWindowTitle("Auto-PID — %s" % TUNE_LOOPS.get(loop, loop))
        self.setModal(True)
        form = QtWidgets.QFormLayout(self)

        self.cb_apply = QtWidgets.QCheckBox('APPLY: save the BEST parameters to ArduSub (--apply)')
        self.cb_apply.setToolTip('Candidate gains are always applied TEMPORARILY while being tested (otherwise every '
                             'iteration uses the same gains and the results look nearly identical). If this box is '
                             'NOT ticked, the original values are restored automatically at the end of the run — so '
                             'nothing changes permanently. Tick it if you want the best candidate SAVED.')
        self.cb_apply.setChecked(False)
        self.cb_arm = QtWidgets.QCheckBox('ARM automatically before tuning (--arm)')
        self.cb_arm.setChecked(True)
        self.sp_iters = QtWidgets.QSpinBox()
        self.sp_iters.setRange(1, 20)          # dulu maks 8: pemilik menulis 8 lalu "berhenti
        self.sp_iters.setValue(8)              # di 2" — sekarang 8 benar-benar dijalankan
        self.sp_iters.setToolTip('MAXIMUM number of iterations (1-20). The search stops early when the spec is met OR it'
                         ' is already optimal (the smallest step no longer improves the score) — so this is an '
                         'upper bound, not a target. Candidate gains are applied temporarily while being tested;'
                         ' the original values are restored when APPLY is not ticked.')
        self.sp_amp = QtWidgets.QSpinBox()
        self.sp_amp.setRange(0, 200)
        self.sp_amp.setValue(0)
        self.sp_amp.setSuffix(' µs (0 = loop default)')
        self.sp_dur = QtWidgets.QDoubleSpinBox()
        self.sp_dur.setRange(0.5, 20.0)
        self.sp_dur.setSingleStep(0.5)
        self.sp_dur.setValue(3.0)
        self.sp_dur.setSuffix(" s (lama langkah)")
        self.sp_pra = QtWidgets.QDoubleSpinBox()
        self.sp_pra.setRange(1.0, 20.0)
        self.sp_pra.setValue(4.0)
        self.sp_pra.setSuffix(' s (settle before step)')
        self.sp_pasca = QtWidgets.QDoubleSpinBox()
        self.sp_pasca.setRange(2.0, 40.0)
        self.sp_pasca.setValue(8.0)
        self.sp_pasca.setSuffix(' s (observe after step)')
        self.sp_pasca.setToolTip("Owner 27 Sep: the response looked 'short' because the post-step phase was only 4 s. 8 "
                      's makes the residual swing/settling visible.')
        self.cb_uji = QtWidgets.QComboBox()
        self.cb_uji.addItem('disturbance — apply a force then watch the response (default)', "gangguan")
        self.cb_uji.addItem('step — RC step (old method)', "tangga")
        self.cb_uji.setToolTip('Owner 27 Sep: in hold mode what is tested is the REACTION TO A DISTURBANCE (e.g. '
                "pushed down during depth hold, or told to move forward), not following a step. 'step' "
                'is still available for model identification.')
        self.sp_zmin = QtWidgets.QDoubleSpinBox()
        self.sp_zmin.setRange(0.0, 20.0)
        self.sp_zmin.setSingleStep(0.5)
        self.sp_zmin.setValue(2.5)
        self.sp_zmin.setSuffix(' m (required depth)')
        self.sp_zmin.setToolTip('Owner 27 Sep: tuning MUST be below the surface. The ROV is lowered to this depth '
                 'first; if it is not reached, tuning is CANCELLED because at the surface the response '
                 'is clipped by buoyancy & the water-surface transition. 0 = disable the gate '
                 '(experiment).')
        self.cb_metode = QtWidgets.QComboBox()
        for kode, label in (("auto", 'auto — try all rules, pick the best'),
                            ("simc", 'SIMC (Skogsted) — steadiest'),
                            ("imc", "IMC / lambda"),
                            ("zn", 'Ziegler-Nichols PID — quickest, prone to oscillating'),
                            ("zn_pi", "Ziegler-Nichols PI"),
                            ("coon", "Cohen-Coon PID"),
                            ("tl", 'Tyreus-Luyben PI — softened ZN'),
                            ("pessen", "Pessen PID")):
            self.cb_metode.addItem(label, kode)
        self.cb_metode.setToolTip('Rule that sets the INITIAL gain from the identified model. The final result still '
           'comes from the measured metrics (closed-loop iterations).')
        self.cb_real = QtWidgets.QCheckBox('REAL ROV (--real): ROV IN THE WATER & pilot ready')
        self.cb_real.setChecked(False)
        self.cb_real.setToolTip('On a real vehicle: REQUIRED acknowledgement. The menu no longer '
                                'needs simulator/SITL, the thruster-pattern disturbance is swapped '
                                'for an RC-pulse disturbance (the SITL patch node does not exist '
                                'on the real ROV), and the tuner restores the parameters on '
                                'Ctrl+C. Keep arm=ON only if the pilot agrees.')
        self.le_host = QtWidgets.QLineEdit("tcp:127.0.0.1:5762")
        self.le_host.setToolTip('MAVLink endpoint of the tuner. SITL: tcp:127.0.0.1:5762. '
                                'REAL ROV: the link endpoint, e.g. udp:192.168.2.1:14550 or '
                                'serial:/dev/ttyACM0:115200.')

        form.addRow(self.cb_apply)
        form.addRow(self.cb_arm)
        form.addRow('iterations', self.sp_iters)
        form.addRow('amplitude', self.sp_amp)
        form.addRow('step duration', self.sp_dur)
        form.addRow('settle before', self.sp_pra)
        form.addRow('observe after', self.sp_pasca)
        form.addRow('PID method', self.cb_metode)
        form.addRow('test method', self.cb_uji)
        form.addRow('minimum test depth', self.sp_zmin)
        form.addRow('MAVLink host', self.le_host)
        form.addRow(self.cb_real)

        info = QtWidgets.QLabel(
            'Parameters being tuned = the GENUINE ArduSub parameters (%s).\nWithout APPLY, the tuner'
' only measures & suggests (it writes nothing).\nA parameter snapshot is taken '
'automatically; the Tuning ▸ Restore parameters menu brings it back.' % TUNE_LOOPS.get(loop, loop))
        info.setWordWrap(True)
        info.setObjectName("MicroLabel")
        theme.set_peran(info, "dim")
        form.addRow(info)

        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok
                                        | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def values(self):
        return dict(loop=self.loop, apply=bool(self.cb_apply.isChecked()),
                    metode=self.cb_metode.currentData(),
                    z_uji_min=float(self.sp_zmin.value()),
                    uji=self.cb_uji.currentData(),
                    t_pra=float(self.sp_pra.value()), t_pasca=float(self.sp_pasca.value()),
                    arm=bool(self.cb_arm.isChecked()), iters=int(self.sp_iters.value()),
                    amp=int(self.sp_amp.value()), dur=float(self.sp_dur.value()),
                    real=bool(self.cb_real.isChecked()),
                    host=(str(self.le_host.text()).strip() or None))


R2D = 57.29578


class Segmen(QtWidgets.QWidget):
    """Deretan tombol pilihan EKSKLUSIF (gaya "segmented control" rancangan).

    Dipakai untuk APPEARANCE (4 palet) di menu bar dan LAYOUT (3 tata letak) di header.
    Qt tidak punya kontrol built-in yang persis seperti ini, dan gaya visualnya sudah
    ada di theme.qss() (objectName "SegBtn") sehingga ikut berganti tema.
    """

    dipilih = QtCore.Signal(str)

    def __init__(self, opsi, parent=None):
        """opsi = [(kunci, label, tooltip), ...]"""
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.tombol = {}
        for kunci, label, tip in opsi:
            b = QtWidgets.QPushButton(label)
            b.setObjectName("SegBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=kunci: self._pilih(k))
            lay.addWidget(b)
            self.tombol[kunci] = b

    def _pilih(self, kunci):
        self.set_dipilih(kunci)
        self.dipilih.emit(kunci)

    def set_dipilih(self, kunci):
        """Tandai satu pilihan (tanpa memancarkan sinyal — untuk sinkronisasi dari menu)."""
        for k, b in self.tombol.items():
            b.setChecked(k == kunci)


class FlowLayout(QtWidgets.QLayout):
    """Layout yang MEMBUNGKUS ke baris berikutnya kalau tidak muat.

    Dipakai status bar: pada jendela sempit kelompoknya turun ke baris kedua,
    jadi tidak ada satu pun potongan teks yang hilang (aturan keras pemilik).
    """

    def __init__(self, parent=None, margin=0, spacing=8):
        super().__init__(parent)
        self._item, self._spacing = [], spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._item.append(item)

    def count(self):
        return len(self._item)

    def itemAt(self, i):
        return self._item[i] if 0 <= i < len(self._item) else None

    def takeAt(self, i):
        return self._item.pop(i) if 0 <= i < len(self._item) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        """SENGAJA False — lihat StatusBar.sesuaikan().

        Kalau layout mengiklankan heightForWidth, Qt menanyakannya dengan lebar yang belum
        final dan memakai hasilnya sebagai tinggi tetap (terukur: 6 kelompok -> 196 px di
        jendela 1440 px). Tinggi pembungkusan dihitung pemilik layout pada lebar NYATA.
        """
        return False

    def tinggi_untuk(self, w):
        """Tinggi yang dibutuhkan bila lebar tersedia = `w` (dipakai StatusBar)."""
        return self._do_layout(QtCore.QRect(0, 0, int(w), 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QtCore.QSize()
        for it in self._item:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, uji):
        m = self.contentsMargins()
        x, y = rect.x() + m.left(), rect.y() + m.top()
        kanan = rect.right() - m.right()
        tinggi = 0
        for it in self._item:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            if x + w > kanan and x > rect.x() + m.left():
                x = rect.x() + m.left()
                y += tinggi + self._spacing
                tinggi = 0
            if not uji:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), it.sizeHint()))
            x += w + self._spacing
            tinggi = max(tinggi, h)
        return y + tinggi - rect.y() + m.bottom()


class FlowBox(QtWidgets.QWidget):
    """Wadah satu baris yang MEMBUNGKUS bila sempit; tingginya dihitung ulang.

    Dipakai baris chip header (mockup: chip di baris yang sama dengan merek + LAYOUT).
    Pola sama dengan StatusBar: `FlowLayout` + tinggi dihitung pada lebar nyata, karena
    mengandalkan heightForWidth layout membuat Qt memakai lebar yang belum final.
    """

    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self.setObjectName("FlowBox")
        self.lay = FlowLayout(self, margin=0, spacing=spacing)

    def tambah(self, w):
        self.lay.addWidget(w)

    def sesuaikan(self):
        lebar = self.width() if self.width() > 50 else self.sizeHint().width()
        perlu = max(18, self.lay.tinggi_untuk(max(60, lebar)))
        if self.minimumHeight() != perlu or self.maximumHeight() != perlu:
            self.setMinimumHeight(perlu)
            self.setMaximumHeight(perlu)
        self.lay.activate()
        self.lay.setGeometry(self.rect())
        self.updateGeometry()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.sesuaikan()

    def sizeHint(self):
        """Lebar = cukup untuk SATU baris (masih boleh menyusut → membungkus sendiri)."""
        m = self.lay.contentsMargins()
        lebar = m.left() + m.right()
        tinggi = 18
        for i in range(self.lay.count()):
            it = self.lay.itemAt(i)
            s = it.sizeHint()
            lebar += s.width() + self.lay._spacing
            tinggi = max(tinggi, s.height())
        return QtCore.QSize(lebar, tinggi)


class StatusBar(QtWidgets.QWidget):
    """Bilah status bersegmen: satu baris kelompok, dipisah garis 1 px (rancangan §9).

    SENGAJA memakai QHBoxLayout biasa, bukan layout khusus: percobaan dengan layout
    pembungkus (`FlowLayout`) menyimpan geometri basi sehingga isinya tergambar DI LUAR
    bilah (terukur: label pada y=973 sementara bilah 942..972 — tulisan tampak di atas
    latar window). Kelompoknya dibuat SEKALI (jumlah tetap) dan hanya teks/visibilitasnya
    yang diubah, jadi tidak ada widget dibuat di dalam slot yang dipanggil thread lain.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusBar")
        # WAJIB untuk QWidget biasa: tanpa atribut ini Qt MENGABAIKAN `background:` dari
        # stylesheet, sehingga bilah status tembus pandang dan tulisan tampak di atas latar
        # jendela (terukur: piksel di dalam bilah = warna `field`, bukan `panel`).
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(30)
        self.lay = QtWidgets.QHBoxLayout(self)
        self.lay.setContentsMargins(8, 0, 8, 0)
        self.lay.setSpacing(8)

    def sesuaikan(self):
        """Tidak ada perhitungan tinggi: bilah dipatok 30 px seperti rancangan."""


class Dashboard(QtWidgets.QMainWindow, StackUI):
    # Sinyal "kerjakan di thread GUI": semua pekerjaan UI yang datang dari thread lain
    # (ROS Bridge, MAVLink, ProcManager) DIKIRIM lewat sini, bukan dijalankan di tempat.
    # Tanpa ini Qt mencetak "Cannot create children for a parent that is in a different
    # thread" lalu SEGFAULT — kejadian nyata 28 Sep 2026 saat menjalankan `rovgui`.
    _sinyal_ui = QtCore.Signal(object)

    def __init__(self, args):
        super().__init__()
        self.args = args
        self._sinyal_ui.connect(self._jalankan_ui, Qt.QueuedConnection)
        self._status_terakhir = ""
        # Objek thread GUI disimpan sekali: _di_gui() dipanggil di jalur panas (tiap baris
        # log, tiap refresh panel) sehingga tidak boleh menanyai Qt berulang-ulang.
        self._thread_gui = QtCore.QThread.currentThread()
        self.seabed_z = args.seabed_z          # m (NED, positif ke bawah) untuk ALT AGL
        self.align = None                      # (a, b, c) transformasi VIO -> NED
        self.gt_hist = deque(maxlen=1500)       # (t, n, e, d)
        self.errs = deque(maxlen=4000)
        self.lens = {"gt": 0.0, "vio": 0.0}
        self.last_vio = None
        self.last_depth = None
        self.last_imu = None
        self.rates = {}
        self.mav = {}
        # pilot/manual (23 Sep 2026): posisi kamera depan yang diminta GUI,
        # status chip PAD terakhir (untuk membedakan "belum pernah aktif") dan
        # status fusi altitude terakhir.
        self.pan = 0.0
        self.tilt = 0.0
        self._pad_was_on = False
        self._hint_prio = False      # pesan penting (deadman/tidak-arm) menahan hint biasa
        self._agl_last = None
        # GUI sebagai aplikasi: peta panel yang bisa di-toggle + setelan yang diingat
        self.vis = {}
        self._vis_on = set()
        self._preset = self.PRESET_BAWAAN       # layout MAPPING (dipakai semua mode)
        self._mode = ""
        self.settings = QtCore.QSettings("BRIN", "ROVMissionConsole")

        self.setWindowTitle("ROV Mission Console — BlueROV2 · Stonefish · VINS-Fusion")
        # Ukuran awal = ukuran rancangan TAPI tidak boleh lebih besar dari layar (laptop
        # 1366x768 / 1280x800 tetap bisa membuka GUI). Minimum diturunkan supaya jendela
        # kecil pun tetap muat tanpa ada yang terpotong (tata letak menyusut sendiri).
        scr = None
        try:
            scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
        except Exception:                                     # noqa: BLE001
            scr = None
        lebar, tinggi = 1620, 940
        if scr is not None:
            lebar = min(lebar, max(1000, scr.width() - 60))
            tinggi = min(tinggi, max(620, scr.height() - 90))
        self.resize(lebar, tinggi)
        self._orig_size = QtCore.QSize(lebar, tinggi)
        self.setMinimumSize(1200, 660)   # batas bawah yang masih sehat untuk 3 kolom
        self.embed_mode = "off"
        if not args.no_embed and args.embed == "reparent":
            # ⚠️ BERISIKO: reparent (XReparentWindow) pernah membuat sesi desktop user
            # blank/hilang. Hanya untuk yang tahu risikonya.
            self.embed_mode = "reparent"
        # Default (auto/off): panel simulasi DI-KOSONGKAN — pemilik sistem menonton
        # langsung jendela Stonefish, jadi mirror/grabWindow tidak dipakai lagi.
        # Mode "mirror" dihapus total (23 Sep 2026) — lihat progress 23SEP.
        self._build_ui()

        # ---- manajer proses stack: sim, SITL, VINS, lawnmower ----
        self.pm = ProcManager(args.log_dir)
        self.pm.sig_log.connect(self._on_proc_log)
        # status misi untuk TANDA di tombol START AUTONOMOUS (26 Sep): idle/jalan/selesai/
        # dihentikan + progres waypoint; diperbarui dari baris log lawnmower.
        self.mission_state = "idle"
        self.mission_wp = None
        self.mission_t0 = None
        self.pm.sig_changed.connect(self._refresh_stack_panel)
        self.timer_stack = QtCore.QTimer(self)
        self.timer_stack.timeout.connect(self._refresh_stack_panel)
        self.timer_stack.start(1000)
        self.btn_auto.toggled.connect(self._on_autonomous_toggled)
        if hasattr(self, "btn_manual"):
            self.btn_manual.toggled.connect(self._on_manual_toggled)
        if hasattr(self, "btn_manual_map"):
            self.btn_manual_map.toggled.connect(self._on_manual_map_toggled)
        if not args.no_autostart:
            self._autostart_stack()
        else:
            self._set_status('autostart disabled · start manually from the STACK panel')

        # ---- (dihapus) mirror jendela simulator: panel kini kosong, pemilik sistem
        # ---- menonton jendela Stonefish secara langsung ----

        self.ros = None
        self.mavbridge = None
        if not args.no_ros:
            self._start_ros()
        if not args.no_mav:
            self._start_mav()

        self.timer_embed = QtCore.QTimer(self)
        self.timer_embed.timeout.connect(self._poll_embed)
        self.timer_embed.start(2000)
        self.timer_plot = QtCore.QTimer(self)
        self.timer_plot.timeout.connect(self._refresh_plot_stats)
        self.timer_plot.start(200)

        if args.shot:
            QtCore.QTimer.singleShot(int(args.shot_after * 1000), self._take_shot)

    # ================================================================== #
    # UI
    # ================================================================== #
    # ---------------------------------------------------------------- thread GUI
    def _di_gui(self):
        """True bila kita sedang berjalan di thread GUI (thread pemilik widget)."""
        t = getattr(self, "_thread_gui", None)
        if t is None:
            return True
        return QtCore.QThread.currentThread() is t

    def _ui(self, aksi):
        """Jalankan `aksi` di thread GUI (dari thread lain dikirim lewat sinyal queued)."""
        if self._di_gui():
            aksi()
        else:
            self._sinyal_ui.emit(aksi)

    def _jalankan_ui(self, aksi):
        """Slot di thread GUI: jalankan aksi yang dititipkan thread pekerja."""
        try:
            aksi()
        except RuntimeError:
            # Objek Qt sudah dihancurkan (jendela ditutup / proses berhenti) — aksi yang
            # masih di antrean tidak lagi relevan. JANGAN cetak traceback: ini normal dan
            # hanya membuat log sesi berisik.
            return
        except Exception:                                     # noqa: BLE001
            traceback.print_exc()

    def _build_ui(self):
        """Tata letak aplikasi: menu bar + header + banner + 3 zona + status bar.

        Sejak redesign (28 Sep 2026) komposisi kolom dibangun dari SPESIFIKASI preset
        (`SPESIFIKASI`) sehingga preset baru bisa ditambah tanpa menyalin kode UI:

          kiri   = rel telemetri (lebar tetap ± 300 px, boleh di-scroll)
          tengah = peta (bidang terbesar) + baris VIO/mozaik + STACK LOG
          kanan  = kamera depan + pilot input + MISSION CONTROL + stack proses

        Prinsip lama tetap: yang tampil DEFAULT hanya informasi krusial; sisanya lewat
        menu View. Panel yang dimatikan benar-benar menyusut sehingga tidak ada teks
        atau panel yang terpotong, dan kolom yang isinya panjang boleh di-scroll.
        """
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 3)      # napas di bawah status bar
        root.setSpacing(0)

        self._build_panels()
        self._build_menubar()
        root.addWidget(self._build_header())
        root.addWidget(self._build_banner())

        self.body = QtWidgets.QWidget()
        bl = QtWidgets.QHBoxLayout(self.body)
        bl.setContentsMargins(6, 6, 6, 6)
        bl.setSpacing(6)
        self._lay_body = bl
        self.col_left, self._lay_left = self._kolom_scroll()
        self.col_mid, self._lay_mid, self._mid_row, self._lay_midrow = self._kolom_tengah()
        self.col_right, self._lay_right = self._kolom_scroll(min_w=336, max_w=430)
        bl.addWidget(self.col_left, 0)
        bl.addWidget(self.col_mid, 1)
        bl.addWidget(self.col_right, 0)
        root.addWidget(self.body, 1)
        root.addWidget(self._build_statusbar())
        # DEFAULT (permintaan pemilik 28 Sep 2026): tata letak MISSION OPS. Preset sesi
        # sebelumnya TIDAK dipulihkan supaya GUI selalu mulai dengan susunan yang sama;
        # operator tetap bisa memilih preset lain lewat menu View ▸ Layout preset atau
        # tombol LAYOUT di header.
        self._apply_preset(str(self.args.preset or self.PRESET_BAWAAN))
        self._ukur_ulang()          # sesuaikan dengan ukuran jendela sebenarnya
        # BAWAAN MODE AUTONOMOUS = LEGACY (keputusan pemilik 2 Okt 2026: "motion untuk yang
        # WP puter balik itu belom bener, jadi pakai yang legacy aja dulu"). Dipanggil SEKALI
        # di sini supaya keempat permukaan state menunjuk mode yang sama sejak detik pertama:
        # tanda menu Operator, tombol PLANNER di panel MISSION CONTROL, label komponen
        # `lawnmower` + cmd peluncurannya (procman), dan penanda segmen misi.
        # Kalau pemilik ingin kembali ke mode bebas: tekan FIXED WP (panel) atau
        # Operator ▸ Autonomous mode ▸ FIXED WP.
        self._set_auto_mode_gui("fixed_wp_lama")
        # banner siap-kerja dikembalikan (banner mode autonomous tadi hanya umpan balik
        # sesaat saat operator mengganti mode; waktu start yang benar = ajakan kerja).
        self._set_banner("ready — autonomous default: LEGACY (old WP motion) · "
                         "click START MANUAL PILOT to fly with the gamepad", "dim")
        # indikator operator (pilot manual / otonom) disegarkan 1 Hz
        self._t_op = QtCore.QTimer(self)
        self._t_op.timeout.connect(self._update_operator)
        self._t_op.start(1000)
        self._update_operator()

    # ---------------------------------------------------------------- kerangka kolom
    def _kolom_scroll(self, min_w=300, max_w=360):
        """Kolom rel (kiri/kanan): isinya tinggi ALAMI, kelebihannya jadi scrollbar.

        Panel di dalam QScrollArea TIDAK boleh diperas layout (itu penyebab baris
        terakhir keluar dari kotak panel) — jadi kolom diakhiri stretch, bukan
        pembagian ruang.
        """
        inner = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)                # rel: jarak antar panel 4 px (padat, tanpa scroll)
        lay.addStretch(1)
        sa = QtWidgets.QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(inner)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setFrameShape(QtWidgets.QFrame.NoFrame)
        sa.setMinimumWidth(min_w)
        sa.setMaximumWidth(max_w)
        return sa, lay

    def _kolom_tengah(self):
        """Kolom tengah: peta (mengisi penuh) + baris VIO/mozaik + STACK LOG."""
        c = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(c)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        # WAJIB berinduk ke kolom tengah: widget tanpa induk yang di-setVisible(True)
        # muncul sebagai JENDELA TERPISAH (kejadian nyata 28 Sep: menekan tombol LAYOUT
        # memunculkan popup berisi VIO stereo + mozaik, dan panelnya "hilang" dari GUI).
        row = QtWidgets.QWidget(c)
        rl = QtWidgets.QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        row.setMinimumHeight(170)      # mockup: baris VIO/mozaik flex 0 0 268px;
        row.setMaximumHeight(268)      # minimum 170 supaya jendela pendek tetap muat
        return c, v, row, rl

    def _ukur_ulang(self):
        """Sesuaikan lebar rel & tinggi panel kunci dengan UKURAN JENDELA (resolusi apa pun).

        Dipanggil pada setiap resize. Nilainya dijaga (guard) supaya perubahan ukuran tidak
        memicu gelung tata letak. Rasio yang dipakai sengaja longgar: layar 16:9, 16:10,
        3:2, dan ultrawide semuanya hanya menjalani clamp yang sama.
        """
        if not hasattr(self, "col_left"):
            return
        spec = self.SPESIFIKASI.get(self._preset, self.SPESIFIKASI[self.PRESET_BAWAAN])
        H = max(560, self.height())
        tinggi_row = max(120, min(268, int(H * 0.27)))      # baris VIO/mozaik
        tinggi_log = max(104, min(190, int(H * 0.19)))      # STACK LOG
        tinggi_cam = max(104, min(172, int(H * 0.18)))      # kotak kamera depan
        kunci = (self.width(), self.height(), self._preset)
        if getattr(self, "_ukuran_terakhir", None) == kunci:
            return
        self._ukuran_terakhir = kunci
        self._atur_lebar(spec)
        if hasattr(self, "_mid_row"):
            self._mid_row.setMaximumHeight(tinggi_row)
            self._mid_row.setMinimumHeight(min(tinggi_row, 170))
        if "log" in self.vis:
            self.vis["log"].setMinimumHeight(tinggi_log)
            self.vis["log"].setMaximumHeight(tinggi_log)
        if hasattr(self, "cam_front"):
            self.cam_front.tinggi_maks = tinggi_cam

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._ukur_ulang()

    def _bersihkan(self, lay):
        """Keluarkan semua item dari layout tanpa menghancurkan widget-nya."""
        while lay.count():
            lay.takeAt(0)

    def _atur_lebar(self, spec):
        """Lebar kolom: rel tetap (rancangan baru) atau komposisi lama 9:6:2 (Legacy).

        Komposisi lama sengaja dipertahankan apa adanya untuk preset Legacy/Mapping
        supaya "tampilan hari ini" tetap bisa dibandingkan berdampingan.
        """
        if spec.get("lebar", "rail") == "lama":
            for sa in (self.col_left, self.col_right):
                sa.setMinimumWidth(0)
                sa.setMaximumWidth(16777215)
            self.col_mid.setMinimumWidth(0)
            self._lay_body.setStretch(0, 9)
            self._lay_body.setStretch(1, 6)
            self._lay_body.setStretch(2, 2)
        else:
            # Lebar rel DIPATOK pada lebar rancangan (kiri 296/340, kanan 340) TETAPI menyusut
            # proporsional di layar sempit supaya kolom tengah tidak terjepit dan tidak ada yang
            # terpotong. Layar lebar (>= 1620 px) memakai angka rancangan apa adanya.
            #   kiri  = clamp(220, 21% lebar, lebar rancangan)
            #   kanan = clamp(240, 21% lebar, 340)          (rancangan: 340 px)
            W = max(900, self.width())
            kiri_maks = int(spec.get("lebar_kiri", 296))
            kiri = max(220, min(kiri_maks, int(W * 0.21)))
            kanan = max(240, min(340, int(W * 0.21)))
            self.col_left.setMinimumWidth(kiri)
            self.col_left.setMaximumWidth(kiri)
            self.col_right.setMinimumWidth(kanan)
            self.col_right.setMaximumWidth(kanan)
            self._lebar_rel = (kiri, kanan)
            self._lay_body.setStretch(0, 0)
            self._lay_body.setStretch(1, 1)
            self._lay_body.setStretch(2, 0)

    def _pasang_kolom(self):
        """Susun ulang panel ke tiga zona sesuai spesifikasi preset yang aktif."""
        spec = self.SPESIFIKASI.get(self._preset, self.SPESIFIKASI[self.PRESET_BAWAAN])
        self._bersihkan(self._lay_left)
        self._bersihkan(self._lay_mid)
        self._bersihkan(self._lay_midrow)
        self._bersihkan(self._lay_right)
        self._col_keys = {"left": [], "mid": [], "right": []}
        pos_row = spec.get("row_di")
        entri_mid = spec.get("mid", [])
        for zona, lay in (("left", self._lay_left), ("mid", self._lay_mid),
                          ("right", self._lay_right)):
            if zona != "mid":
                for entri in spec.get(zona, []):
                    kunci, regangan = entri if isinstance(entri, tuple) else (entri, 0)
                    lay.addWidget(self.vis[kunci], regangan)
                    self._col_keys[zona].append(kunci)
                continue
            # kolom tengah: baris VIO/mozaik disisipkan di posisi yang ditentukan preset
            for i, entri in enumerate(entri_mid):
                if pos_row is not None and i == pos_row:
                    self._lay_mid.addWidget(self._mid_row)
                kunci, regangan = entri if isinstance(entri, tuple) else (entri, 0)
                lay.addWidget(self.vis[kunci], regangan)
                self._col_keys["mid"].append(kunci)
        for entri in spec.get("mid_row", []):
            kunci, regangan = entri if isinstance(entri, tuple) else (entri, 1)
            self._lay_midrow.addWidget(self.vis[kunci], regangan)
            self._col_keys["mid"].append(kunci)
        # Baris VIO/mozaik: kalau preset ini tidak memakainya, wadahnya TETAP diletakkan di
        # layout (dalam keadaan tersembunyi) — widget yang tidak dikelola layout bisa
        # mengapung sebagai jendela sendiri.
        if pos_row is None or pos_row > len(entri_mid):
            self._lay_mid.addWidget(self._mid_row)
        self._mid_row.setVisible(bool(spec.get("mid_row")))
        # Kolom rel memakai tinggi ALAMI panel (kelebihannya jadi scrollbar); kolom
        # tengah tidak perlu stretch karena panel peta yang mengisi sisa ruang.
        self._lay_left.addStretch(1)
        self._lay_right.addStretch(1)
        if hasattr(self, "_log_scroll"):
            tinggi = spec.get("log_h")
            if tinggi and "log" in self._col_keys["mid"]:
                self.vis["log"].setMinimumHeight(int(tinggi))
                self.vis["log"].setMaximumHeight(int(tinggi))
        if hasattr(self, "box_baru"):
            # FITUR BARU (PLANNER + estimasi waktu) tidak ada di GUI lama -> di preset
            # Legacy disembunyikan (kolomnya juga hanya 12 % lebar jendela).
            self.box_baru.setVisible(bool(spec.get("fitur_baru", True)))
        if hasattr(self, "_perbarui_seg_layout"):
            self._perbarui_seg_layout()


    def _build_panels(self):
        """Buat semua panel tanpa memasangnya (penempatan diatur _column/_apply_preset)."""
        # ---- kamera depan pilot + kontrol pan/tilt
        self.p_sim = W.Panel("Cam · front (pilot)", "MANUAL mode", domain="field")
        self.sim = None
        self.sim_tile = None
        if self.embed_mode == "reparent":
            self.sim = SimEmbed()
            self.p_sim.body_layout.setContentsMargins(0, 0, 0, 0)
            self.p_sim.body_layout.addWidget(self.sim)
        else:
            self.cam_front = W.CameraTile("front camera", judul=False)
            # Tinggi kotak kamera depan dipatok seperti mockup (kotak video 124 px); tanpa
            # patokan ini panelnya mengambil tinggi rasio 16:9 penuh dan mendorong
            # MISSION CONTROL keluar dari area pandang rel kanan.
            # Kotak kamera depan: 200 px (rancangan hanya 124 px). Lebih besar dari
            # rancangan, tetapi tetap membuat rel kanan MUAT UTUH di jendela pemilik
            # (1620x978) — tanpa kotak scroll memotong panel terbawah.
            self.cam_front.tinggi_maks = 172
            self.p_sim.body_layout.setContentsMargins(4, 4, 4, 4)
            self.p_sim.body_layout.addWidget(self.cam_front, 1)
            pt = QtWidgets.QHBoxLayout()
            pt.setSpacing(4)
            self.r_cam = QtWidgets.QLabel("pan  —   tilt  —")
            self.r_cam.setObjectName("MicroLabel")
            theme.set_peran(self.r_cam, "dim")
            # Teks DINAMIS panjang (nilai pan/tilt nyata) tidak boleh memaksa panel melebar:
            # membungkus + tooltip berisi teks penuh. Terukur sebelum ini: label 222 px ->
            # panel CAM 382 px > rel 340 px (temuan probe jalan nyata 28 Sep).
            self.r_cam.setWordWrap(True)
            self.r_cam.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                     QtWidgets.QSizePolicy.Preferred)
            # Maksimum 2 baris: nilai pan/tilt nyata hanya ~26 karakter (1 baris), dan kalau
            # nilainya panjang (mis. saat kalibrasi) panel TIDAK boleh ikut meninggi — teks
            # penuh selalu tersedia di tooltip. Tanpa batas ini panel CAM bisa tumbuh 65 px
            # (terukur) dan mendorong panel terbawah keluar viewport rel.
            self.r_cam.setMaximumHeight(32)
            pt.addWidget(self.r_cam)
            pt.addStretch(1)
            self.btn_cam = {}
            for key, txt, tip in (("pan_dn", "◀", "pan camera left"),
                                  ("pan_up", "▶", "pan camera right"),
                                  ("tilt_up", "▲", "tilt camera up"),
                                  ("tilt_dn", "▼", "tilt camera down"),
                                  ("center", "⌂", "centre pan/tilt")):
                b = QtWidgets.QPushButton(txt)
                b.setFixedSize(26, 22)
                b.setCursor(Qt.PointingHandCursor)
                b.setObjectName("BtnTiny")
                b.setToolTip(tip)
                b.setAutoRepeat(True)
                b.setAutoRepeatDelay(300)
                b.setAutoRepeatInterval(80)
                if key == "center":
                    b.clicked.connect(self._cam_center)
                else:
                    axis, sign = key.split("_")
                    b.clicked.connect(lambda _, a=axis, s=1.0 if sign == "up" else -1.0:
                                      self._cam_nudge(a, s))
                self.btn_cam[key] = b
                pt.addWidget(b)
            self.p_sim.body_layout.addLayout(pt)

        # ---- trajektori VIO vs ground truth
        # ---- trajektori: PETA OSM (ala QGC) + tab NED lama -------------------------------
        # Permintaan pemilik 26 Sep: "hasil pergerakan VIO terintegrate dengan view dari google
        # maps seperti pada QGC ... sebagai pengganti window trajektori (tapi window lama
        # simpen aja jadi pilihan)". Karena itu keduanya jadi TAB: peta sebagai tampilan
        # utama, jendela NED 1:1 tetap tersedia. Peta memakai ubin OpenStreetMap (tanpa API
        # key) + anchor manual (menu Operator → "Set anchor peta...").
        from peta_panel import PetaPanel
        # Judul + pemilih tampilan saja di kepala panel (mockup); keterangan rinci ada di
        # baris info peta di bawah kanvas.
        self.p_map = W.Panel('Trajectory & map · VIO vs ground truth', '',
                             domain="mission")
        self.plot = W.TrajectoryPlot()
        # Panel peta: mesin ubin sendiri (satelit Esri/OSM) — klon QGC (QtLocation/QML) sudah
        # DIHAPUS atas permintaan pemilik (overlay-nya tidak pernah bisa digambar).
        self.peta = PetaPanel()
        self.peta_ubin = self.peta
        # Dua tampilan yang SAMA seperti sebelumnya (peta ubin / plot NED) — hanya
        # pemilihnya pindah ke kepala panel mengikuti mockup. Nama `tabs_traj`
        # dipertahankan supaya kode & uji lama tetap jalan.
        self.stack_traj = QtWidgets.QStackedWidget()
        self.stack_traj.addWidget(self.peta)
        self.stack_traj.addWidget(self.plot)
        self.tabs_traj = self.stack_traj
        self.seg_traj = Segmen([("map", 'MAP', 'tile map (satellite/OSM) — default view'),
                                ("ned", 'NED 1:1', 'legacy 1:1 NED plot of VIO vs ground truth')])
        self.seg_traj.set_dipilih("map")
        self.seg_traj.dipilih.connect(
            lambda k: self.stack_traj.setCurrentIndex(0 if k == "map" else 1))
        self.p_map.sisip_kontrol(self.seg_traj)
        # kedua panel peta harus menerima data & setelan yang sama
        for _attr in ("set_anchor", "pergi_ke", "set_provider", "pusatkan_ke_robot"):
            if self.peta_ubin is not self.peta and hasattr(self.peta_ubin, _attr):
                pass                      # dipanggil eksplisit di metod GUI (lihat _set_penyedia_peta)
        self.p_map.body_layout.setContentsMargins(4, 4, 4, 4)
        self.p_map.body_layout.addWidget(self.stack_traj, 1)
        # ---- baris info peta (mockup): sumber ubin, zoom/skala, antrean, grid + 2 tombol
        # yang sudah ada di menu Operator (jalur aksi TIDAK berubah, hanya tombolnya
        # dinaikkan ke panel). Isinya dibaca 1 Hz dari panel peta, tanpa kerja tambahan.
        # FlowBox = baris yang membungkus: chip TILES + info + 2 tombol tidak akan terpotong
        # ketika kolom tengah menyempit (temuan pemilik: "box tiles OSM nya juga kepotong").
        baris_peta = QtWidgets.QHBoxLayout()
        baris_peta.setSpacing(8)
        self.bar_peta = FlowBox(spacing=8)
        self.chip_ubin = W.Chip("tiles")
        self.chip_ubin.set_state("ok", "—")
        self.l_ubin = QtWidgets.QLabel("")
        self.l_ubin.setObjectName("MapBar")
        theme.set_peran(self.l_ubin, "dim")
        self.l_map_info = QtWidgets.QLabel("")
        self.l_map_info.setObjectName("MapBar")
        theme.set_peran(self.l_map_info, "faint")
        self.btn_peta_go = QtWidgets.QPushButton("Go to location")
        self.btn_peta_go.setObjectName("BtnGhost")
        self.btn_peta_go.setCursor(Qt.PointingHandCursor)
        self.btn_peta_go.setToolTip('Same action as Operator ▸ "Map: go to coordinates…"')
        self.btn_peta_go.clicked.connect(self._pergi_ke_dialog)
        self.btn_peta_pin = QtWidgets.QPushButton("Pin anchor")
        self.btn_peta_pin.setObjectName("BtnGhost")
        self.btn_peta_pin.setCursor(Qt.PointingHandCursor)
        self.btn_peta_pin.setToolTip('Same action as Operator ▸ "Set map anchor: PIN on the map…"')
        self.btn_peta_pin.clicked.connect(self._mulai_pin_peta)
        for wdg in (self.chip_ubin, self.l_ubin, self.l_map_info,
                    self.btn_peta_go, self.btn_peta_pin):
            self.bar_peta.tambah(wdg)
        self.p_map.body_layout.addWidget(self.bar_peta)
        try:                                   # pin di peta -> jadikan anchor + laporkan
            self.peta.pin_dipilih.connect(self._pin_peta_dipilih)
        except Exception:                                     # noqa: BLE001
            pass
        try:                                   # anchor peta tersimpan (kalau ada)
            self.peta.set_anchor(self._muat_anchor_peta())
        except Exception:                                     # noqa: BLE001
            pass

        # ---- panel pilot (bar input stick; diminta pemilik 23 Sep)
        self.p_pilot = self._build_pilot()

        # ---- kamera VIO: dua tile BERDAMPINGAN (hemat tinggi, terlihat proporsional)
        self.p_viocam = W.Panel("VIO · stereo", "left/right · VINS needs ~10 s to initialise",
                                domain="digital")
        self.cam_l = W.CameraTile("left")
        self.cam_r = W.CameraTile("right")
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self.cam_l, 1)
        row.addWidget(self.cam_r, 1)
        self.p_viocam.body_layout.setContentsMargins(4, 4, 4, 4)
        self.p_viocam.body_layout.addLayout(row, 1)
        if self.args.capture_preview:
            self.cam_cap = W.CameraTile("capture · SfM RGB 1920×1200")
            self.p_viocam.body_layout.addWidget(self.cam_cap, 1)

        # ---- jendela hasil SfM (permintaan pemilik 24 Sep 2026) ----
        # Panel VIO di atas kini stretch 0 (tingginya PAS mengikuti rasio gambar,
        # tanpa ruang kosong percuma); ruang yang dibebaskan dipakai untuk jendela
        # orthomosaic. Isinya menyusul (Tahap H-map) -> sekarang jendela kosong
        # yang JUJUR bilang "belum ada isi", bukan gambar/angka karangan.
        # Nama panel sesuai permintaan pemilik 27 Sep: "MOZAIK MAPPING RESULT" — SATU jendela,
        # isinya mozaik 2D, plus tombol SHOW 3D MODEL (3D tetap di jendela Python terpisah).
        self.p_sfm = W.Panel('Mosaic mapping result', '2D mosaic · 3D in its own window',
                             domain="mission")
        self.p_sfm.body_layout.setContentsMargins(4, 4, 4, 4)
        # Tahap H-map (25 Sep 2026): panel ini menampilkan orthomosaic NYATA bila ada
        # (dihasilkan `Progress/tools/sfm_colmap.py` dari folder capture misi). Kalau
        # belum ada, tetap jujur kosong + alasan — bukan gambar/angka karangan.
        self.sfm_tile = W.CameraTile("SFM")
        # Default: hasil SFM OTOMATIS run ini (<run>/sfm/mosaic.png). Saat sesi dibuka
        # berkas belum ada -> panel tetap KOSONG (permintaan pemilik 25 Sep: "jendela
        # hasil mozaik kosongin dulu"); begitu sfm_auto.py selesai memproses pasca-misi,
        # _reload_sfm() (poll 2 s) memuat mozaiknya sendiri.
        _run = getattr(self.args, "log_dir", "") or ""
        self._run_dir_sfm = _run                 # folder run untuk gambar + jendela 3D
        self.sfm_path = (getattr(self.args, "sfm_image", "") or
                         (os.path.join(_run, "sfm", "mosaic.png") if _run else ""))
        self._sfm_mtime = None
        self.sfm_tile.set_off('no orthomosaic yet — this panel stays EMPTY until this run finishes; once the mission '
'stops, Progress/tools/sfm_auto.py processes the captures and the mosaic appears here '
'automatically',
                              title="NO MAP YET")
        # SATU TAMPILAN (permintaan pemilik 27 Sep): "window mozaiknya satu aja, defaultnya
        # yang 2D, dan tombol Show 3D model untuk membuka jendela Python terpisah".
        # Alasannya terukur: kanvas matplotlib 3D yang DITANAM di GUI menggambar ulang seluruh
        # figur di dalam proses Qt setiap redraw → GUI terasa ngelag. Sekarang 3D berjalan di
        # PROSES TERPISAH (`dem3d_viewer.py`), sehingga GUI tetap ringan.
        self._tampilan_sfm = "2d"                # selalu mozaik 2D (tidak ada toggle/sub-window)
        self.gabung_preview = (os.path.join(_run, "sfm", "peta_gabungan_preview.png")
                               if _run else "")
        baris_sfm = QtWidgets.QHBoxLayout()
        baris_sfm.setSpacing(4)
        self.btn_sfm_3d = QtWidgets.QPushButton("SHOW 3D MODEL \u25b8")
        self.btn_sfm_3d.setCursor(Qt.PointingHandCursor)
        self.btn_sfm_3d.setToolTip('Open the 3D map in a SEPARATE PYTHON WINDOW: seafloor surface + mosaic imagery '
'applied as a texture, with a full-column / relief-focus switch inside. Interactive: rotate/zoom/pan + '
'vertical scale factor. The GUI stays light because 3D is not drawn inside it.')
        self.btn_sfm_3d.clicked.connect(self._buka_3d)
        baris_sfm.addWidget(self.btn_sfm_3d)
        self.btn_sfm_3d_lite = QtWidgets.QPushButton('MOSAIC + RELIEF')
        self.btn_sfm_3d_lite.setCursor(Qt.PointingHandCursor)
        self.btn_sfm_3d_lite.setToolTip('Open the combined map image (photo + grey relief) with the system image viewer — not '
'inside the GUI.')
        self.btn_sfm_3d_lite.clicked.connect(self._buka_gabungan)
        baris_sfm.addWidget(self.btn_sfm_3d_lite)
        self.lbl_sfm_status = QtWidgets.QLabel("")
        self.lbl_sfm_status.setObjectName("Faint")
        theme.set_peran(self.lbl_sfm_status, "dim")
        self.lbl_sfm_status.setWordWrap(True)
        self.lbl_sfm_status.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                          QtWidgets.QSizePolicy.Preferred)
        baris_sfm.addWidget(self.lbl_sfm_status, 1)
        self.p_sfm.body_layout.addLayout(baris_sfm)
        self.p_sfm.body_layout.addWidget(self.sfm_tile, 1)
        self._init_sfm()
        try:                       # tulis area bawaan (5,0 = 10x10 m) ke cmd lawnmower
            self._set_area(self.sp_area.value())
        except Exception:                                          # noqa: BLE001
            pass

        # ---- panel misi + telemetri
        self.p_autonomous = self._build_autonomous()
        self.p_stack = self._panel_stack()
        self.p_flight = self._panel_flight()
        self.p_gyro = self._panel_gyro()
        self.p_power = self._panel_power()
        self.p_vio = self._panel_vio()
        self.p_events = self._panel_events()
        self.p_log = self._panel_log()
        self.p_tuning = self._panel_tuning()
        # Daftar panel yang bisa di-toggle: SATU tempat yang dipakai _pasang_kolom(),
        # _apply_preset(), _toggle_panel() (menu View) dan uji.
        self.vis = {"cam": self.p_sim, "pilot": self.p_pilot, "map": self.p_map,
                    "viocam": self.p_viocam, "sfm": self.p_sfm, "auto": self.p_autonomous,
                    "stack": self.p_stack, "flight": self.p_flight, "gyro": self.p_gyro,
                    "power": self.p_power, "vio": self.p_vio, "events": self.p_events,
                    "tuning": self.p_tuning, "log": self.p_log}

    def _column(self, items, scroll=False):
        """Bungkus panel jadi satu kolom (widget) yang bisa disembunyikan utuh."""
        inner = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        keys = []
        for key, wdg, stretch in items:
            lay.addWidget(wdg, stretch)
            self.vis[key] = wdg
            keys.append(key)
        if scroll:
            lay.addStretch(1)       # kolom telemetri selalu mulai dari atas
        if not scroll:
            return inner, keys
        sa = QtWidgets.QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(inner)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        sa.setFrameShape(QtWidgets.QFrame.NoFrame)
        # lebar kolom: cukup lega agar chip/pill tidak rapat ke tepi jendela
        sa.setMinimumWidth(330)
        sa.setMaximumWidth(470)
        return sa, keys

    # ---------------------------------------------------------------- menu & preset
    # Mode ArduSub (nomor resmi): dipakai menu Vehicle
    FLIGHT_MODES = (("MANUAL", 19), ("STABILIZE", 0), ("ALT HOLD (depth hold)", 2),
                    ("POS HOLD", 16), ("SURFACE", 9), ("AUTO (mission)", 3),
                    ("GUIDED", 4))
    PRESETS = {
        # MISSION OPS — BAWAAN sejak 28 Sep 2026 (keputusan pemilik atas rancangan A):
        # rel telemetri kiri tidak lagi diperas, log misi pindah ke kolom tengah.
        "mission-ops": ("cam", "pilot", "map", "viocam", "sfm", "auto", "stack", "flight",
                        "gyro", "power", "vio", "events", "tuning", "log"),
        # PILOT FOCUS — varian 4 MOCKUP_FINAL (BAWAAN): susunan kolom sama dengan Mission
        # Ops, rel kiri 340 px, dan panel MOZAIK disembunyikan (di mockup: .p-mosaic
        # display:none). Dipakai untuk menerbangkan ROV manual.
        "pilot-focus": ("cam", "pilot", "map", "viocam", "auto", "stack", "flight",
                        "gyro", "power", "vio", "events", "tuning", "log"),
        # Preset lama TETAP ada (permintaan pemilik: jangan hapus pilihan yang sudah ada),
        # tetapi opsi "Legacy (as-is)" TIDAK lagi ditawarkan (permintaan pemilik 28 Sep).
        "mapping": ("cam", "pilot", "map", "viocam", "sfm", "auto", "stack", "flight", "vio",
                    "log"),
        "pilot": ("cam", "pilot", "map", "sfm", "auto", "stack", "flight", "log"),
        "diagnostik": ("cam", "pilot", "map", "viocam", "sfm", "auto", "stack", "tuning",
                       "flight", "gyro", "power", "vio", "events", "log"),
    }
    PRESET_BAWAAN = "pilot-focus"

    # Nama tampilan preset (tombol LAYOUT di header + menu View).
    NAMA_PRESET = {"mission-ops": "Mission Ops", "pilot-focus": "Pilot Focus",
                   "legacy": "Legacy (as-is)", "mapping": "Mapping (52/35/12)",
                   "pilot": "Pilot (compact)", "diagnostik": "Diagnostics (all panels)"}

    # Susunan kolom tiap preset: (panel, bobot). Angka bobot 0 = tinggi alami.
    #   "lebar": "rail" = rel kiri/kanan lebar tetap (rancangan baru)
    #            "lama" = komposisi 9:6:2 seperti GUI hari ini (Legacy/Mapping)
    #   "fitur_baru": False -> blok PLANNER + estimasi waktu disembunyikan (Legacy)
    SPESIFIKASI = {
        "mission-ops": {
            "lebar_kiri": 296,
            "left": ["flight", "vio", "power", "gyro", "tuning", "events"],
            "mid": [("map", 1), "log"],
            "row_di": 1,
            "mid_row": [("viocam", 5), ("sfm", 4)],
            "right": ["cam", "pilot", "auto", "stack"],
            "log_h": 190,
        },
        "pilot-focus": {
            # varian 4 MOCKUP_FINAL: rel kiri 340 px, mozaik disembunyikan, susunan lain
            # sama dengan Mission Ops (markup varian 1 & 4 identik; bedanya hanya CSS).
            "left": ["flight", "vio", "power", "gyro", "tuning", "events"],
            "mid": [("map", 1), "log"],
            "mid_row": [("viocam", 1)],
            "row_di": 1,
            "right": ["cam", "pilot", "auto", "stack"],
            "log_h": 190,
            "lebar_kiri": 340,
        },
        "mapping": {
            "left": ["cam", "pilot", "map"],
            "mid": ["viocam", "sfm", "auto"],
            "right": ["stack", "tuning", "flight", "gyro", "power", "vio", "events", "log"],
            "lebar": "lama",
        },
        "pilot": {
            "left": ["cam", "pilot", "map"],
            "mid": ["sfm", "auto"],
            "right": ["stack", "flight", "log"],
            "lebar": "lama",
        },
        "diagnostik": {
            "left": ["cam", "pilot", "map"],
            "mid": ["viocam", "sfm", "auto"],
            "right": ["stack", "tuning", "flight", "gyro", "power", "vio", "events", "log"],
            "lebar": "lama",
        },
    }

    def _build_menubar(self):
        """Menu bar gaya aplikasi desktop: opsi non-krusial pindah ke sini."""
        mb = self.menuBar()
        mb.setNativeMenuBar(False)
        # Kontrol cepat APPEARANCE (4 palet) di sudut kanan menu bar — sama dengan
        # View ▸ Appearance, jadi tidak perlu masuk menu untuk mengganti tema.
        try:
            mb.setCornerWidget(self._seg_appearance())
        except Exception:                                     # noqa: BLE001
            pass

        m = mb.addMenu("&File")
        m.addAction("Open &run folder", self._menu_open_run, "Ctrl+O")
        m.addAction("Save &screenshot…", self._menu_screenshot, "Ctrl+P")
        m.addSeparator()
        m.addAction("&Quit", self.close, "Ctrl+Q")

        v = mb.addMenu("&View")
        self.vis_act = {}
        for key, label, sc in (("cam", "Front camera (pilot)", "Ctrl+1"),
                               ("pilot", "Gamepad input", "Ctrl+2"),
                               ("map", "Trajectory VIO vs GT", "Ctrl+3"),
                               ("viocam", "VIO stereo cameras", "Ctrl+4"),
                               ("sfm", "SfM result window", ""),
                               ("stack", "Stack control (START/STOP)", "Ctrl+5"),
                               ("flight", "Flight telemetry", "Ctrl+6"),
                               ("auto", "Autonomous mission", "Ctrl+7"),
                               ("gyro", "Gyro / IMU", "Ctrl+8"),
                               ("power", "Power & temperature", "Ctrl+9"),
                               ("vio", "VIO / VINS summary", "Ctrl+0"),
                               ("events", "Events", "Ctrl+E"),
                               ("tuning", "Auto-PID (tuning)", "Ctrl+T"),
                               ("log", "Mission log", "Ctrl+L")):
            a = v.addAction(label)
            a.setCheckable(True)
            a.setShortcut(sc)
            a.toggled.connect(lambda on, k=key: self._toggle_panel(k, on))
            self.vis_act[key] = a
        v.addSeparator()
        pm = v.addMenu("Layout preset")
        self._menu_preset = pm
        for key in ("mission-ops", "pilot-focus", "mapping", "pilot", "diagnostik"):
            pm.addAction(self.NAMA_PRESET.get(key, key),
                         lambda _=False, k=key: self._apply_preset(k))
        v.addSeparator()
        # ---- tampilan (Appearance): tiga field brand + tema lama (permintaan pemilik)
        vm = v.addMenu("Appearance")
        self._menu_tema = vm
        self.grup_tema = QtGui.QActionGroup(self)
        self.grup_tema.setExclusive(True)
        self.act_tema = {}
        for nama in theme.tampil():
            a = QtGui.QAction(theme.label(nama), self, checkable=True)
            a.setChecked(nama == theme.aktif())
            a.triggered.connect(lambda _=False, n=nama: self._ganti_tema(n))
            self.grup_tema.addAction(a)
            vm.addAction(a)
            self.act_tema[nama] = a
        v.addSeparator()
        fs = v.addAction("Full screen")
        fs.setCheckable(True)
        fs.setShortcut("F11")
        fs.toggled.connect(lambda on: self.showFullScreen() if on else self.showNormal())

        m2 = mb.addMenu("&Operator")
        m2.addAction("Start/stop manual pilot", self._menu_manual, "F2")
        m2.addAction("Start/stop mapping (pilot + capture + guide)", self._menu_manual_map, "F4")
        m2.addAction("Start autonomous mission", self._menu_auto, "F3")
        # ---- Kerapatan baris misi autonomous (mode "adaptive" DIHAPUS 30 Sep 2026) ----
        sub = m2.addMenu("Autonomous mode")
        grp = QtGui.QActionGroup(self)
        self.act_auto_fixed = sub.addAction('FIXED WP (default: fixed depth, 1.0 m lines)')
        # Kerapatan baris (26 Sep, lanjutan H3): overlap peta = (footprint lintas-baris /
        # jarak baris) x (footprint searah / step capture) ≈ 1,1 x 3,8 = ~2 foto/piksel
        # pada 1,0 m. Untuk peta yang benar-benar butuh overlap >= 3 (ambang fotogrametri),
        # jarak baris dirapatkan ke 0,55 m — konsekuensinya misi ~1,8x lebih panjang.
        self.act_auto_rapat = sub.addAction('FIXED WP DENSE (0.55 m lines → map overlap ≥3)')
        # MODE LEGACY (fallback, permintaan pemilik 1 Okt): gerakan WP lama (omni, tanpa putar-balik)
        self.act_auto_lama = sub.addAction('LEGACY (old WP motion: omni, no turn-around)')
        for act in (self.act_auto_fixed, self.act_auto_rapat, self.act_auto_lama):
            act.setCheckable(True)
            grp.addAction(act)
        self.act_auto_fixed.setChecked(True)          # default 1,0 m
        self.act_auto_fixed.triggered.connect(lambda: self._set_auto_mode("fixed_wp", 1.0))
        self.act_auto_rapat.triggered.connect(lambda: self._set_auto_mode("fixed_wp", 0.55))
        self.act_auto_lama.triggered.connect(lambda: self._set_auto_mode("fixed_wp_lama", 1.0))

        # ---- PETA: anchor manual (26 Sep). Nanti di robot nyata anchor diambil dari GPS
        # saat robot di permukaan (peta_geo.anchor_dari_gps), jadi tombolnya juga memberi
        # jalan untuk itu. ------------------------------------------------------------
        m2.addSeparator()
        self.act_anchor = QtGui.QAction('Set map anchor (lat, lon, heading)...', self)
        self.act_anchor.triggered.connect(self._set_anchor_peta)
        m2.addAction(self.act_anchor)
        self.act_anchor_pin = QtGui.QAction('Set map anchor: PIN on the map...', self)
        self.act_anchor_pin.triggered.connect(self._mulai_pin_peta)
        m2.addAction(self.act_anchor_pin)
        self.act_pergi = QtGui.QAction('Map: go to coordinates...', self)
        self.act_pergi.triggered.connect(self._pergi_ke_dialog)
        m2.addAction(self.act_pergi)
        self.menu_penyedia = m2.addMenu('Map: map provider')
        # DAFTAR PENYEDIA BERTINGKAT seperti QGC (Provider ▸ Type): grup OSM / Esri, dibangun
        # dari gui/peta_penyedia.py supaya tidak ada dua daftar yang berbeda.
        #
        # BUG yang diperbaiki (pertanyaan pemilik "kenapa centangnya barengan banyak?"):
        # pilihan penyedia berada di dalam SUBMENU, sedangkan kode sebelumnya hanya
        # meng-uncheck item di tingkat atas menu → setiap penyedia yang pernah diklik tetap
        # tercentang. QActionGroup eksklusif membuat Qt sendiri menjaga hanya satu centang.
        self.grup_penyedia = QtGui.QActionGroup(self)
        self.grup_penyedia.setExclusive(True)
        try:
            from peta_penyedia import PENYEDIA_DAFTAR, per_grup, BAWAAN
            _grup = per_grup()
        except Exception:                                     # noqa: BLE001
            PENYEDIA_DAFTAR, _grup, BAWAAN = {}, {}, "OSM : Standard"
        _aktif = getattr(self.peta, "provider", BAWAAN)
        if _grup:
            for _nama_grup, _daftar in _grup.items():
                _sub = self.menu_penyedia.addMenu(_nama_grup)
                for _nama in _daftar:
                    _a = QtGui.QAction(_nama, self, checkable=True)
                    _a.setChecked(_nama == _aktif)
                    _a.triggered.connect(lambda _=False, n=_nama: self._set_penyedia_peta(n))
                    self.grup_penyedia.addAction(_a)
                    _sub.addAction(_a)
        else:
            for _nama in ("Satelit (Esri World Imagery)",):
                _a = QtGui.QAction(_nama, self, checkable=True)
                _a.setChecked(_nama == _aktif)
                _a.triggered.connect(lambda _=False, n=_nama: self._set_penyedia_peta(n))
                self.menu_penyedia.addAction(_a)
        self.menu_penyedia.addSeparator()
        self.menu_penyedia.addAction('Manage tile cache (info/trim)...', self._kelola_cache_peta)
        # grid meter di peta: default MATI (pemilik tidak suka tampilan bergaris)
        self.act_grid_peta = QtGui.QAction('Map: metre grid (on/off)', self, checkable=True)
        self.act_grid_peta.setChecked(False)
        self.act_grid_peta.triggered.connect(self._toggle_grid_peta)
        m2.addAction(self.act_grid_peta)
        # Menu "sumber ubin sendiri" (server XYZ / folder lokal / .mbtiles) DIHAPUS atas
        # permintaan pemilik — server ubinnya juga sudah dihapus. Mesin ubin di panel tetap
        # bisa membacanya kalau nanti diperlukan, tetapi tidak lagi ditawarkan di menu.
        self.act_uji_peta = QtGui.QAction('Map: test connection & reload', self)
        self.act_uji_peta.triggered.connect(self._uji_koneksi_peta)
        m2.addAction(self.act_uji_peta)
        self.menu_lokasi = m2.addMenu('Map: go to location')
        self._isi_menu_lokasi()
        self.act_peta_ikuti = QtGui.QAction('Map: FOLLOW vehicle mode (on/off)', self)
        self.act_peta_ikuti.setCheckable(True)
        self.act_peta_ikuti.setChecked(True)
        self.act_peta_ikuti.triggered.connect(self._toggle_peta_ikuti)
        m2.addAction(self.act_peta_ikuti)

        # ---- Tuning (26 Sep): auto-PID parameter ArduSub ----
        # ArduSub TIDAK punya autotune internal (beda dengan ArduCopter). Menu ini
        # menjalankan rov_pid_autotune.py yang menyetel parameter ASLI ArduSub
        # (PSC_D_* kedalaman, ATC_* yaw/roll/pitch) lewat MAVLink — sehingga nilai hasil
        # tuning bisa dipindahkan ke robot nyata.
        m4 = mb.addMenu("&Tuning")
        for loop, label in (("depth", 'Auto-PID DEPTH (PSC_D_*)'),
                            ("yaw", "Auto-PID HEADING (ATC_*YAW*)"),
                            ("roll", "Auto-PID ROLL (ATC_*RLL*)"),
                            ("pitch", "Auto-PID PITCH (ATC_*PIT*)")):
            m4.addAction(label, lambda _=False, l=loop: self._menu_tune(l))
        m4.addSeparator()
        m4.addAction("Stop auto-PID", self._menu_tune_stop)
        m4.addAction('Restore parameters (last snapshot)', self._menu_tune_restore)

        # ---- Profil operasi (26 Sep, permintaan pemilik: "atur arsitekturnya supaya ringan") ----
        # Menentukan komponen + set kamera yang hidup saat MANUAL. Terukur: tiap kamera =
        # satu pass scene (~2 juta segitiga), jadi jumlah kamera aktif yang menentukan beban.
        m5 = m2.addMenu('Manual profile')
        self.act_prof_ringan = m5.addAction('LIGHT — front camera only, VINS off')
        self.act_prof_vio = m5.addAction('MANUAL + VIO — 3 cameras + VINS (old policy)')
        for act in (self.act_prof_ringan, self.act_prof_vio):
            act.setCheckable(True)
        self.act_prof_vio.setChecked(True)     # BAWAAN: MANUAL + VIO (VINS nyala)
        self.act_prof_ringan.triggered.connect(lambda: self._set_profil_manual("manual_ringan"))
        self.act_prof_vio.triggered.connect(lambda: self._set_profil_manual("manual_vio"))

        # Menu Vehicle = fungsi ArduSub yang biasa ada di QGroundControl
        m3 = mb.addMenu("&Vehicle")
        for label, num in self.FLIGHT_MODES:
            m3.addAction("Flight mode: %s" % label,
                         lambda _=False, n=num: self._set_flight_mode(n))
        m3.addSeparator()
        m3.addAction("ARM", lambda: self._arm(True))
        m3.addAction("DISARM", lambda: self._arm(False))

        h = mb.addMenu("&Help")
        h.addAction("Controls & shortcuts", self._menu_help, "F1")
        h.addAction("About", self._menu_about)

    # ------------------------------------------------------------------ PETA (26 Sep)
    ANCHOR_JSON = os.path.expanduser("~/.config/rovgui/peta_anchor.json")

    def _muat_anchor_peta(self):
        """Anchor tersimpan; kalau belum ada pakai bawaan panel (dari <ned> skenario)."""
        import peta_geo
        bawaan = dict(getattr(self.peta, "anchor", {"lat": 0.0, "lon": 0.0,
                                                   "heading_deg": 0.0}))
        a = peta_geo.muat_anchor(self.ANCHOR_JSON, bawaan)
        a.setdefault("sumber", "manual")
        return a

    def _set_anchor_peta(self):
        """Dialog satu baris: `lat, lon[, arah°]`. Disimpan supaya bertahan antar sesi.

        Nanti di robot nyata anchor diambil dari GPS saat robot di permukaan
        (peta_geo.anchor_dari_gps) — fungsi itu sudah disiapkan.
        """
        import peta_geo
        a = self.peta.anchor
        teks, ok = QtWidgets.QInputDialog.getText(
            self, 'Map anchor',
            'Track start coordinates:  lat, lon [, local X-axis heading to true north °]\nExample: '
'56.136459, -2.706819, 0',
            text="%.6f, %.6f, %.1f" % (a["lat"], a["lon"], a.get("heading_deg", 0.0)))
        if not ok or not teks.strip():
            return
        bagian = [b.strip() for b in teks.replace(";", ",").split(",") if b.strip()]
        try:
            lat = float(bagian[0]); lon = float(bagian[1])
            hd = float(bagian[2]) if len(bagian) > 2 else a.get("heading_deg", 0.0)
        except (IndexError, ValueError):
            self._log('map: invalid anchor format — use `lat, lon[, heading]`')
            return
        baru = {"lat": lat, "lon": lon, "heading_deg": hd, "sumber": "manual"}
        self.peta.set_anchor(baru)
        peta_geo.simpan_anchor(self.ANCHOR_JSON, baru)
        self._log('map: manual anchor → %.6f, %.6f (heading %.1f°) saved to %s'
                  % (lat, lon, hd, self.ANCHOR_JSON))

    LOKASI_JSON = os.path.expanduser("~/.config/rovgui/peta_lokasi.json")

    def _isi_menu_lokasi(self):
        """Daftar lokasi preset (bisa diedit operator) untuk 'pergi ke lokasi'.

        Catatan jujur: koordinat Kepulauan Seribu di bawah ini **perkiraan posisi pulau**
        (untuk navigasi awal), bukan koordinat situs restorasi yang pasti — arahkan peta ke
        sana, zoom ke area yang benar, lalu pakai 'PIN di peta' untuk menaruh anchor tepat.
        """
        bawaan = {
            "Kepulauan Seribu · P. Pramuka (perkiraan)": [-5.7440, 106.6120],
            "Kepulauan Seribu · P. Panggang (perkiraan)": [-5.7390, 106.5940],
            "Kepulauan Seribu · P. Pari (perkiraan)": [-5.8590, 106.6200],
            "Kepulauan Seribu · P. Tidung (perkiraan)": [-5.7950, 106.5000],
            "Origin skenario simulasi (North Sea)": [56.136459, -2.706819],
        }
        import json
        if not os.path.exists(self.LOKASI_JSON):
            try:
                os.makedirs(os.path.dirname(self.LOKASI_JSON), exist_ok=True)
                json.dump(bawaan, open(self.LOKASI_JSON, "w"), indent=1, ensure_ascii=False)
            except OSError:
                pass
        try:
            lok = json.load(open(self.LOKASI_JSON))
        except Exception:                                     # noqa: BLE001
            lok = bawaan
        for nama, koord in lok.items():
            try:
                la, lo = float(koord[0]), float(koord[1])
            except (TypeError, ValueError, IndexError):
                continue
            act = QtGui.QAction(nama, self)
            act.triggered.connect(lambda _=False, la=la, lo=lo, n=nama: self._pergi_ke(n, la, lo))
            self.menu_lokasi.addAction(act)
        self.menu_lokasi.addSeparator()
        act = QtGui.QAction('Centre on the ROV (FOLLOW mode)', self)
        act.triggered.connect(self._pusatkan_roi)
        self.menu_lokasi.addAction(act)
        self.menu_lokasi.addAction('Add this location (from map centre)...',
                                   self._tambah_lokasi)

    def _pergi_ke(self, nama, lat, lon):
        # JANGAN paksa pindah tab: operator mungkin sedang melihat tab lain; aksi berlaku untuk
        # SEMUA panel peta supaya keduanya siap saat dilihat.
        for panel in self._peta_semua():
            panel.pergi_ke(lat, lon, sumber=nama)
        self._log('map: go to %s → %.6f, %.6f (track anchor UNCHANGED)'
                  % (nama, lat, lon))

    def _pergi_ke_dialog(self):
        teks, ok = QtWidgets.QInputDialog.getText(
            self, 'Go to coordinates',
            'Centre the map on:  lat, lon\nExample: -5.7440, 106.6120', text="-5.7440, 106.6120")
        if not ok or not teks.strip():
            return
        try:
            la, lo = [float(x) for x in teks.replace(";", ",").split(",")[:2]]
        except ValueError:
            self._log('map: invalid coordinate format — use `lat, lon`')
            return
        self._pergi_ke('manual coordinates', la, lo)





    def _toggle_grid_peta(self, aktif=None):
        """Nyalakan/matikan grid meter di panel peta (bawaan: mati)."""
        peta = getattr(self, "peta_ubin", self.peta)
        baru = bool(aktif) if aktif is not None else not getattr(peta, "tampilkan_grid", False)
        for panel in {id(self.peta): self.peta, id(self.peta_ubin): self.peta_ubin}.values():
            if hasattr(panel, "tampilkan_grid"):
                panel.tampilkan_grid = baru
                panel.update()
        self.act_grid_peta.setChecked(baru)
        self._log('map: metre grid %s' % ("ON" if baru else "OFF"))

    def _kelola_cache_peta(self):
        """Info + pangkas cache ubin (padanan halaman "Maps ▸ Tile Cache" di QGC)."""
        import peta_panel as _pp
        akar = _pp.CACHE
        baris, total, jml = [], 0, 0
        if os.path.isdir(akar):
            for nama in sorted(os.listdir(akar)):
                d = os.path.join(akar, nama)
                n = uk = 0
                if os.path.isdir(d):
                    for a, _, berkas in os.walk(d):
                        for b in berkas:
                            n += 1
                            uk += os.path.getsize(os.path.join(a, b))
                elif nama.endswith(".png"):
                    n, uk = 1, os.path.getsize(d)
                if n:
                    baris.append('  %-16s %7d files · %8.1f MB' % (nama, n, uk / 1e6))
                    total += uk
                    jml += n
        info = ('Tile cache in %s\n\n%s\n\nTOTAL: %d files · %.1f MB'
                % (akar, "\n".join(baris) if baris else '  (empty)',
                   jml, total / 1e6))
        self._log('map: tile cache → %d files · %.1f MB' % (jml, total / 1e6))
        jawab = QtWidgets.QMessageBox.question(
            self, 'Manage tile cache',
            info + '\n\nDelete ALL tile cache? (the next download fetches them again; your local '
'folders/mbtiles are untouched)',
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
        if jawab == QtWidgets.QMessageBox.Yes:
            import shutil
            try:
                shutil.rmtree(akar, ignore_errors=True)
                os.makedirs(akar, exist_ok=True)
                self.peta._lapisan.clear(); self.peta._ubin.clear(); self.peta._kosong.clear()
                self.peta._minta.clear(); self.peta._minta_kasar.clear()
                self.peta.update()
                self._log('map: tile cache cleared (%d files · %.1f MB freed)'
                          % (jml, total / 1e6))
            except OSError as exc:
                self._log('map: failed to clear cache: %r' % (exc,))

    def _uji_koneksi_peta(self):
        """Ambil satu ubin sekarang juga -> membedakan masalah jaringan dari masalah tampilan."""
        self.tabs_traj.setCurrentIndex(0)
        ok, pesan = self.peta.uji_koneksi()
        self._log('map: connection test → %s' % pesan)
        QtWidgets.QMessageBox.information(
            self, 'Map connection test',
            ('SUCCESS fetching map tiles:\n\n%s\n\nIf the map is still empty after this, it is a display'
' problem (send this message to Hermes).' % pesan) if ok else
            ('FAILED to fetch map tiles:\n\n%s\n\nNetwork/proxy/firewall may be blocking. You can also '
"use 'download area for OFFLINE' from another network." % pesan))
        self.peta.update()

    def _peta_semua(self):
        """Semua panel peta yang ada (QML dan 'ubin sendiri').

        Penting: menu peta dulu hanya mengubah panel QML, sehingga saat tampilan pertama adalah
        peta QML, panel "ubin sendiri" tidak bisa disetel dan tampak kosong (keluhan pemilik).
        """
        hasil = []
        for nm in ("peta", "peta_ubin"):
            x = getattr(self, nm, None)
            if x is not None and all(x is not y for y in hasil):
                hasil.append(x)
        return hasil

    def _set_penyedia_peta(self, nama):
        diterima = []
        for panel in self._peta_semua():
            try:
                if panel.set_provider(nama):
                    diterima.append(type(panel).__name__)
            except Exception as exc:                          # noqa: BLE001
                self._log("map: panel %s rejected provider '%s' (%r)"
                          % (type(panel).__name__, nama, exc))
        if diterima:
            # centang HANYA pada penyedia yang aktif (dulu loop ini menelusuri item tingkat
            # atas saja, sehingga item di dalam submenu tidak pernah di-uncheck → banyak
            # centang sekaligus; sekarang lewat QActionGroup eksklusif)
            for a in self.grup_penyedia.actions():
                a.setChecked(a.text() == nama)
            self._log("map: provider '%s' used by %s" % (nama, ", ".join(diterima)))
        else:
            self._log("map: no panel accepted provider '%s'" % nama)


    def _pusatkan_roi(self):
        for panel in self._peta_semua():
            panel.pusatkan_ke_robot()
        self.act_peta_ikuti.setChecked(True)
        self._log('map: following the ROV again (FOLLOW mode)')

    def _tambah_lokasi(self):
        lat, lon, _ = self.peta._bidang()[:3]
        import json
        nama, ok = QtWidgets.QInputDialog.getText(self, 'Add location',
                                                  'Location name:', text='New location')
        if not ok or not nama.strip():
            return
        try:
            lok = json.load(open(self.LOKASI_JSON))
        except Exception:                                     # noqa: BLE001
            lok = {}
        lok[nama.strip()] = [lat, lon]
        try:
            json.dump(lok, open(self.LOKASI_JSON, "w"), indent=1, ensure_ascii=False)
            self._log("map: location '%s' saved (%.6f, %.6f) to %s"
                      % (nama, lat, lon, self.LOKASI_JSON))
        except OSError as exc:
            self._log('map: failed to save location: %r' % (exc,))

    def _mulai_pin_peta(self):
        """Mode PIN: sekali klik di peta menaruh anchor (alternatif mengetik koordinat)."""
        for panel in self._peta_semua():
            panel.siapkan_pin()
        self._log('map: PIN mode active — click the track start point on the map; its coordinates are '
'reported immediately')

    def _pin_peta_dipilih(self, lat, lon):
        """Dipanggil panel peta saat operator menaruh pin: jadikan anchor + laporkan."""
        import peta_geo
        a = self.peta.anchor
        baru = {"lat": float(lat), "lon": float(lon),
                "heading_deg": float(a.get("heading_deg", 0.0)), "sumber": 'map pin'}
        # terapkan ke SEMUA panel peta (QML maupun 'ubin sendiri') supaya keduanya sinkron
        for panel in self._peta_semua():
            panel.set_anchor(baru)
        peta_geo.simpan_anchor(self.ANCHOR_JSON, baru)
        self._log('map: PIN → anchor %.6f, %.6f (heading %.1f°) · saved to %s'
                  % (lat, lon, baru["heading_deg"], self.ANCHOR_JSON))
        QtWidgets.QMessageBox.information(
            self, 'Anchor from map pin',
            'Track start point (anchor) set from the pin on the map:\n\n   latitude  = %.6f\n   '
'longitude = %.6f\n   local X-axis heading to north = %.1f°\n\nSaved to %s\n\nThese numbers '
'are also written to the bottom panel log.'
            % (lat, lon, baru["heading_deg"], self.ANCHOR_JSON))

    def _toggle_peta_ikuti(self, aktif=None):
        peta = self.peta
        baru = bool(aktif) if aktif is not None else not getattr(peta, "ikuti", True)
        for panel in self._peta_semua():
            if hasattr(panel, "ikuti"):
                panel.ikuti = baru
            if hasattr(panel, "update"):
                panel.update()
        self.act_peta_ikuti.setChecked(baru)
        self._log('map: FOLLOW vehicle mode %s' % ("ON" if self.peta.ikuti else 'OFF (free view)'))

    def _set_auto_mode(self, mode, line=1.0):
        """Pilih mode autonomous + KERAPATAN BARIS untuk peluncuran berikutnya (menu Operator)."""
        # Simpan pilihan (26 Sep): dipakai penanda segmen supaya evaluasi per-segmen tahu
        # mode & kerapatan baris yang sedang dijalankan.
        self._auto_mode = (mode, float(line))
        try:
            from procman import set_lawn_planner
            set_lawn_planner(mode, line)
            _lama = str(mode).lower() in ("fixed_wp_lama", "legacy", "omni")
            self.act_auto_lama.setChecked(_lama)
            self.act_auto_rapat.setChecked((not _lama) and abs(float(line) - 0.55) < 0.05)
            self.act_auto_fixed.setChecked((not _lama) and abs(float(line) - 1.0) < 0.05)
        except Exception as e:                       # noqa: BLE001
            print('set autonomous mode failed:', e)
        nama = ('FIXED WP (2 waypoints/line, line spacing %.2f m, fixed depth' % line
                + (', DENSE: map overlap ≥3, mission ~1.8x longer)' if line < 0.9 else ")"))
        self._set_banner('Autonomous mode: %s — applies when the next mission starts' % nama,
                         "accent")
        self._refresh_estimasi()

    def _set_area(self, half_m):
        """Set area survei (setengah-sisi, m) untuk misi BERIKUTNYA (permintaan pemilik 27 Sep)."""
        try:
            from procman import set_lawn_area
            set_lawn_area(half_m)
        except Exception as exc:                                   # noqa: BLE001
            self._log('area: failed to set (%r)' % exc)
            return
        sisi = 2.0 * float(half_m)
        if hasattr(self, "lbl_area"):
            self.lbl_area.setText("%.1fx%.1f m" % (sisi, sisi))
        self._refresh_estimasi()          # estimasi ikut berubah saat area diubah
        self._log('next mission area: %.1f x %.1f m (half-side %.1f m)'
                  % (sisi, sisi, float(half_m)))

    def _set_profil_manual(self, nama):
        """Pilih profil operasi MANUAL (menu Operator ▸ Profil manual)."""
        from procman import set_manual_profile, profil
        n = set_manual_profile(nama)
        if hasattr(self, "act_prof_ringan"):
            self.act_prof_ringan.setChecked(n == "manual_ringan")
            self.act_prof_vio.setChecked(n == "manual_vio")
        p = profil(n)
        self._set_banner('MANUAL profile: %s · camera %s · %s'
                         % (p["nama"], p["kamera"], ", ".join(p["comp"])), theme.ACCENT)
        self._log('Manual profile set: %s (applies on START MANUAL PILOT)' % p["nama"])

    def _menu_tune(self, loop="depth"):
        """Jalankan Auto-PID untuk satu loop (menu Tuning).

        ROBOT ASLI (4 Okt): dialog dibuka LEBIH DULU; bila 'REAL ROV' dicentang, syarat sim+SITL
        dilewati (di robot nyata tidak ada simulator), gangguan otomatis memakai pulsa RC
        (dorong butuh node patch SITL), dan host MAVLink bisa diarahkan ke link robot nyata.
        """
        from procman import set_tuner_cmd
        dlg = TuneDialog(self, loop)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        v = dlg.values()
        if not v["real"] and not (self.pm.is_running("sim") and self.pm.is_running("sitl")):
            self._set_banner('Auto-PID: simulator/SITL is not running (tick "REAL ROV" for a '
                             'real vehicle)', theme.ERR)
            return
        if v["real"]:
            self._log('Auto-PID: REAL ROV mode — sim/SITL not required · host %s · disturbance '
                      'falls back to RC pulses' % (v.get("host") or "default"))
        # link EKSKLUSIF (satu klien): hentikan pengirim RC lain dulu
        for n in ("lawnmower", "teleop"):
            if self.pm.is_running(n):
                self._log('Auto-PID: stopping %s first (exclusive link)' % n)
                self.pm.stop(n)
        set_tuner_cmd(loop=loop, apply_=v["apply"], arm=v["arm"], iters=v["iters"],
                      amp=v["amp"], dur=v["dur"], real=v["real"],
                      metode=v.get("metode", "auto"), t_pra=v.get("t_pra", 4.0),
                      t_pasca=v.get("t_pasca", 8.0), z_uji_min=v.get("z_uji_min", 2.5),
                      uji=v.get("uji", "gangguan"), host=v.get("host"))
        if "tuning" in getattr(self, "vis", {}):
            self._toggle_panel("tuning", True)
            if "tuning" in self.vis_act:
                self.vis_act["tuning"].setChecked(True)
        self._set_banner(
            ('Auto-PID %s: APPLY ArduSub parameters (a snapshot is taken automatically)'
             if v["apply"] else
             'Auto-PID %s: MEASURE-ONLY (no parameters written)') % loop,
            theme.ACCENT if v["apply"] else theme.WARN)
        self._tuning_line('TUNE|preparing loop %s · apply=%s arm=%s iterations=%d amplitude=%d µs'
                          % (loop, v["apply"], v["arm"], v["iters"], v["amp"]))
        self.pm.start("tuning")
        # Permintaan pemilik 27 Sep: jendela respon+galat muncul SENDIRI saat masuk mode tuning
        # PID. Kalau sudah terbuka, jangan dobel (dicek lewat pid-nya di _buka_respon).
        self._buka_respon(otomatis=True)
        self._banner_respon()

    def _menu_tune_stop(self):
        if self.pm.is_running("tuning"):
            self._log('Auto-PID stopped (the parameters will be restored by the tuner)')
            self.pm.stop("tuning")
            self._set_banner('Auto-PID stopped', theme.WARN)
        else:
            self._set_banner('Auto-PID is not running', theme.TEXT_DIM)

    def _menu_tune_restore(self):
        """Kembalikan parameter ArduSub dari snapshot tuning terakhir."""
        import glob
        snaps = sorted(glob.glob("/tmp/rov_pid_autotune/*/params_before.json"),
                       key=lambda p: os.path.getmtime(p))
        if not snaps:
            QtWidgets.QMessageBox.information(
                self, "Auto-PID",
                'No parameter snapshot yet. A snapshot is taken automatically every time auto-tuning '
'runs (params_before.json).')
            return
        snap = snaps[-1]
        if QtWidgets.QMessageBox.question(
                self, 'Restore parameters',
                'Restore ArduSub parameters from the snapshot:\n%s\n\nContinue?' % snap
        ) != QtWidgets.QMessageBox.Yes:
            return
        for n in ("lawnmower", "teleop"):
            if self.pm.is_running(n):
                self.pm.stop(n)
        from procman import set_tuner_restore_cmd
        set_tuner_restore_cmd(snap)
        self._set_banner('Auto-PID: restoring the parameters from the snapshot', theme.ACCENT)
        self.pm.start("tuning")

    def _menu_open_run(self):
        path = getattr(self.pm, "log_dir", None)
        if not path:
            return
        QtWidgets.QMessageBox.information(self, "Folder run", str(path))
        try:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            self._set_banner('cannot open run folder: %r' % e, theme.WARN)

    def _menu_screenshot(self):
        p = os.path.join(os.path.expanduser("~"), "Pictures")
        os.makedirs(p, exist_ok=True)
        out = os.path.join(p, "rov_console_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
        ok = bool(self.grab().save(out))
        self._set_banner(('screenshot saved: %s' % out) if ok
                         else 'FAILED to save screenshot',
                         theme.OK if ok else theme.ERR)

    def _menu_manual(self):
        b = getattr(self, "btn_manual", None)
        if b is not None:
            b.setChecked(not b.isChecked())

    def _menu_auto(self):
        b = getattr(self, "btn_auto", None)
        if b is not None:
            b.setChecked(not b.isChecked())

    def _menu_manual_map(self):
        """MAPPING MANUAL dari menu (F4) — sama dengan tombolnya di MISSION CONTROL."""
        b = getattr(self, "btn_manual_map", None)
        if b is not None:
            b.setChecked(not b.isChecked())

    # ---- fungsi kendaraan (menu Vehicle) ----
    def _set_flight_mode(self, num):
        nama = dict((n, l) for l, n in self.FLIGHT_MODES).get(int(num), str(num))
        try:
            self.mavbridge.queue_mode(int(num))
            self._set_banner("flight mode %s (%d) requested" % (nama, num), theme.TEXT)
        except Exception as e:
            self._set_banner("flight mode request failed: %r" % e, theme.ERR)

    def _arm(self, on):
        try:
            self.mavbridge.queue_arm(bool(on))
            self._set_banner("%s requested" % ("ARM" if on else "DISARM"),
                             theme.WARN if on else theme.TEXT)
        except Exception as e:
            self._set_banner("arm request failed: %r" % e, theme.ERR)

    def _update_operator(self):
        """Chip jelas: siapa yang sedang mengendalikan ROV (pilot manual / otonom / standby)."""
        if not self._di_gui():
            self._ui(self._update_operator)
            return
        self._perbarui_baris_peta()
        man = bool(getattr(self, "btn_manual", None) and self.btn_manual.isChecked())
        mmap = bool(getattr(self, "btn_manual_map", None) and self.btn_manual_map.isChecked())
        auto = False
        try:
            auto = bool(self.pm.is_running("lawnmower"))
        except Exception:
            pass
        if not auto and getattr(self, "btn_auto", None) is not None:
            auto = bool(self.btn_auto.isChecked() and self.btn_auto.text().upper().startswith(
                ("MISSION", "STOP", "AUTO")))
        if auto or getattr(self, "mission_state", "idle") in ("selesai", "dihentikan"):
            wp = getattr(self, "mission_wp", None)
            st = getattr(self, "mission_state", "idle")
            prog = (" wp %d/%d" % wp) if wp else ""
            if st == "selesai":
                self.chip_op.set_state("accent", 'MISSION COMPLETE' + prog)
            elif st == "dihentikan":
                self.chip_op.set_state("warn", 'MISSION STOPPED' + prog)
            else:
                self.chip_op.set_state("ok", "AUTONOMOUS" + prog)
        elif mmap:
            # MAPPING MANUAL: pilot yang mengemudi, tapi sesinya sesi pemetaan (capture).
            self.chip_op.set_state("ok", "PILOT MAPPING" + (" · guide" if getattr(
                self, "peta", None) is not None and getattr(self.peta, "rencana", None) else ""))
        elif man:
            self.chip_op.set_state("accent", "PILOT MANUAL")
        else:
            self.chip_op.set_state("faint", "standby")

    def _perbarui_baris_peta(self):
        """Baris info peta (mockup): sumber ubin, zoom/skala, antrean, keadaan grid.

        Dibaca dari panel peta apa adanya (tanpa perhitungan baru) dan diperbarui 1 Hz
        bersama indikator operator.
        """
        try:
            self.chip_ubin.set_state("ok", str(getattr(self.peta, "provider", "—")))
        except Exception:                                     # noqa: BLE001
            pass
        try:
            import peta_geo as G
            lat, _lon, z = self.peta._bidang()
            self.l_ubin.setText("zoom %d · %.2f m/px" % (z, G.skala_m_per_piksel(lat, z)))
        except Exception:                                     # noqa: BLE001
            pass
        try:
            antre = self.peta.unduh.q.qsize()
            kosong = len(getattr(self.peta, "_kosong", ()) or ())
            grid = "on" if (hasattr(self, "act_grid_peta")
                            and self.act_grid_peta.isChecked()) else "off"
            self.l_map_info.setText("queued %d · empty %d · grid %s" % (antre, kosong, grid))
        except Exception:                                     # noqa: BLE001
            pass

    def _menu_help(self):
        ax = self.ax
        QtWidgets.QMessageBox.information(
            self, "Controls & shortcuts",
            "FLYING — MANUAL mode\n"
            "  button %d (L1/LB)  = deadman, MUST be held\n"
            "  left stick         = forward/back (ax%d), strafe left/right (ax%d)\n"
            "  right stick        = heave up/down (ax%d), yaw (ax%d)\n"
            "  dpad               = front camera pan/tilt, button X = centre\n"
            "  button %d (Options) = ARM <-> DISARM\n"
            "  button %d (Share)   = next ArduSub flight mode\n"
            "  button %d (L2, hold)= ALT HOLD (depth hold); release = base mode\n\n"
            "The PILOT panel shows the raw /joy values: if you move a stick the bars must\n"
            "move. If they do not, joy/teleop is not running (see the banner).\n\n"
            "MENUS  View     panels & layout presets (Ctrl+1..0, Ctrl+E/L, F11 full screen)\n"
            "       Operator manual pilot / mapping (pilot+guide) / autonomous (F2 / F4 / F3)\n"
            "       Vehicle  ArduSub flight modes and ARM/DISARM\n"
            "       File     run folder & screenshot (Ctrl+O / Ctrl+P)\n"
            % (ax["deadman"], ax["fwd"], ax["lat"], ax["depth"], ax["yaw"],
               ax["arm"], ax["mode_cycle"], ax["depth_hold"]))

    def _menu_about(self):
        QtWidgets.QMessageBox.about(
            self, "About",
            "ROV Mission Console\nBlueROV2 Heavy · Stonefish 1.6 · ArduSub SITL · "
            "VINS-Fusion\n\nBRIN VIO — coral-reef mapping with visual-inertial odometry "
            "(downward stereo camera + IMU).")

    def _toggle_panel(self, key, on):
        if key not in self.vis:
            return
        # lacak NIAT tampil: isVisible() selalu False sebelum jendela ditampilkan,
        # sehingga memakainya untuk memutuskan kolom (bug 23 Sep: badan jendela kosong).
        if on:
            self._vis_on.add(key)
        else:
            self._vis_on.discard(key)
        self.vis[key].setVisible(bool(on))
        self._rebuild_vis()

    def _menu_preset_names(self):
        """Label entri menu View ▸ Layout preset (dipakai uji: tidak boleh ada 'Otonom' lagi)."""
        try:
            return [a.text() for a in self._menu_preset.actions()]
        except AttributeError:
            return []

    def _apply_preset(self, name):
        name = name if name in self.PRESETS else self.PRESET_BAWAAN
        # panel kamera depan: tampil di SEMUA preset (layout tidak lagi berganti antar mode)
        if hasattr(self, "cam_front"):
            self.cam_front.setVisible(True)
            try:
                self.cam_front.set_unmuted()
            except Exception:                                 # noqa: BLE001
                pass
        keys = set(self.PRESETS[name])
        self._vis_on = keys
        for k, wdg in self.vis.items():
            wdg.setVisible(k in keys)
        self._preset = name
        # SUSUN ULANG KOLOM: preset baru (mission-ops/pilot-focus/legacy) punya susunan
        # panel yang berbeda dari komposisi lama — panel dipindah (bukan dibuat ulang).
        self._pasang_kolom()
        self._atur_lebar(self.SPESIFIKASI.get(name, {}))
        try:
            self.settings.setValue("preset", name)
        except Exception:
            pass
        if hasattr(self, "vis_act"):
            for k, act in self.vis_act.items():
                act.blockSignals(True)
                act.setChecked(k in keys)
                act.blockSignals(False)
        self._rebuild_vis()

    def _layout_mapping(self, catatan=""):
        """Layout TETAP: preset yang sedang berlaku (tidak berganti antar mode).

        Permintaan pemilik 27 Sep 2026: "tampilan GUI gausa berubah-ubah antar mode karena
        bikin pusing; tetap di layout mapping dengan tambahan layout Ctrl+7 dan mission log".

        Karena itu fungsi ini SATU-SATUNYA jalur layout otomatis: dipanggil pada START
        AUTONOMOUS, START MANUAL, STOP, misi selesai, dan fallback — hasilnya selalu sama
        (tidak ada perpindahan susunan panel antar mode). Perubahan kamera/VINS antar mode
        tetap terjadi di jalur lain (profil komponen + tabel kamera mode manager), jadi
        tampilan tidak perlu ikut berubah.
        """
        nama = self._preset if self._preset in self.PRESETS else self.PRESET_BAWAAN
        self._apply_preset(nama)
        if catatan:
            try:
                self._log('layout: %s — staying in the %s layout (does not change between modes)'
                          % (catatan, nama))
            except Exception:                                 # noqa: BLE001
                pass

    # Nama lama dipertahankan sebagai alias supaya skrip/uji lama tidak mati mendadak;
    # keduanya kini MELAKUKAN HAL YANG SAMA (tidak ada lagi layout khusus per mode).
    def _layout_otonom(self, catatan=""):
        """[DEPRECATED 27 Sep] Dulu: mapping + kamera depan dimatikan. Sekarang = _layout_mapping."""
        return self._layout_mapping(catatan)

    def _layout_pilot(self, catatan=""):
        """[DEPRECATED 27 Sep] Dulu: kembali ke preset pilot. Sekarang = _layout_mapping."""
        return self._layout_mapping(catatan)

    def _rebuild_vis(self):
        """Kolom yang seluruh panelnya tidak tampil ikut disembunyikan (tanpa celah)."""
        for nama, col in (("left", self.col_left), ("mid", self.col_mid),
                          ("right", self.col_right)):
            keys = self._col_keys.get(nama, [])
            col.setVisible(any(k in self._vis_on for k in keys))

    def _pilot_axes(self):
        """Indeks sumbu/tombol dari mission_params.yaml (blok rov_gamepad_teleop).

        Dibaca dari YAML, bukan hardcode, supaya panel pilot selalu cocok dengan
        pemetaan yang dipakai teleop.
        """
        a = {"fwd": 1, "lat": 0, "depth": 4, "yaw": 3, "deadman": 4,
             "arm": 9, "mode_cycle": 8, "depth_hold": 6}
        try:
            import yaml as _yaml
            with open(PARAMS) as f:
                blk = (_yaml.safe_load(f) or {}).get("rov_gamepad_teleop", {}).get(
                    "ros__parameters", {})
            for src, dst in (("axis_fwd", "fwd"), ("axis_lat", "lat"),
                             ("axis_depth", "depth"), ("axis_yaw", "yaw"),
                             ("deadman_button", "deadman"),
                             ("btn_arm_toggle", "arm"),
                             ("btn_mode_cycle", "mode_cycle"),
                             ("btn_depth_hold", "depth_hold")):
                if src in blk:
                    a[dst] = int(blk[src])
        except Exception:
            pass
        return a

    def _build_pilot(self):
        """Bar input stick dari /joy MENTAH + status deadman.

        Diminta pemilik 23 Sep: perlu terlihat apakah stick kiri/kanan benar-benar
        memberi input. Dibaca langsung dari /joy (bukan dari teleop) sehingga tetap
        menunjukkan gerakan walau teleop belum dijalankan.
        """
        p = W.Panel("Pilot input", "deadman L1 must be held", domain="field")
        self.p_pilot = p
        self.ax = self._pilot_axes()
        g = QtWidgets.QGridLayout()
        g.setSpacing(6)
        self.m_lx = W.Meter("L-x", -1, 1, color=theme.CYAN, label_w=34)
        self.m_ly = W.Meter("L-y", -1, 1, color=theme.CYAN, label_w=34)
        self.m_rx = W.Meter("R-x", -1, 1, color=theme.ACCENT, label_w=34)
        self.m_ry = W.Meter("R-y", -1, 1, color=theme.ACCENT, label_w=34)
        g.addWidget(self.m_lx, 0, 0)
        g.addWidget(self.m_ly, 0, 1)
        g.addWidget(self.m_rx, 1, 0)
        g.addWidget(self.m_ry, 1, 1)
        p.body_layout.addLayout(g)
        self.l_pilot = QtWidgets.QLabel("no /joy yet — is joy_node running?")
        self.l_pilot.setWordWrap(True)
        self.l_pilot.setObjectName("Faint")
        theme.set_peran(self.l_pilot, "dim")
        p.body_layout.addWidget(self.l_pilot)
        p.set_hint('left ax%d/ax%d forward-strafe - right ax%d/ax%d heave-yaw - deadman btn%d'
                   % (self.ax["fwd"], self.ax["lat"], self.ax["depth"], self.ax["yaw"],
                      self.ax["deadman"]))
        p.setMaximumHeight(200)
        return p

    def _on_joy(self, axes, buttons):
        """Perbarui bar input stick dari /joy mentah."""
        def ax(i):
            return float(axes[i]) if 0 <= i < len(axes) else 0.0
        self.m_lx.set_value(ax(self.ax["lat"]))
        self.m_ly.set_value(ax(self.ax["fwd"]))
        self.m_rx.set_value(ax(self.ax["yaw"]))
        self.m_ry.set_value(ax(self.ax["depth"]))
        dm = self.ax["deadman"]
        tekan = dm < len(buttons) and buttons[dm] > 0
        # tombol yang sedang ditekan (indeks saja, supaya pilot bisa memetakan sendiri)
        aktif = [i for i, b in enumerate(buttons) if b]
        self.l_pilot.setText(
            "deadman btn%d: %s | buttons held: %s | %d axes %d buttons"
            % (dm, "HELD" if tekan else "released",
               aktif if aktif else "-", len(axes), len(buttons)))
        self._joy_seen = time.monotonic()

    def _build_banner(self):
        """Baris pesan pilot selebar jendela (word-wrap) - pengganti hint panjang.

        Pelajaran 23 Sep: pesan panjang di kepala panel membuat kotak kanan melar dan
        terpotong. Pesan penting (deadman / TIDAK-ARM / teleop belum jalan) sekarang
        tampil di sini sehingga tidak ada yang kepotong di layar sekecil apa pun.
        """
        self.banner = QtWidgets.QLabel("")
        self.banner.setObjectName("Banner")
        self.banner.setWordWrap(True)
        self._banner_teks, self._banner_token = "", "dim"
        self._set_banner("ready — click START MANUAL PILOT to fly with the gamepad", "dim")
        return self.banner

    def _set_banner(self, text, color="dim"):
        """Pesan banner; `color` = NAMA TOKEN ('dim', 'ok', 'warn', 'err', 'accent').

        Token (bukan hex) supaya teks banner tidak tertinggal bergaya lama saat tema
        diganti: nilainya diambil ulang di sini dan di _tema_ulang().
        """
        if not self._di_gui():                    # dipanggil dari thread pekerja
            self._ui(lambda: self._set_banner(text, color))
            return
        self._banner_teks = text
        self._banner_token = theme.token_dari(color if color else "dim")
        self.banner.setText(text)
        self.banner.setStyleSheet(
            "color: %s; background: %s; border-bottom: 1px solid %s; padding: 4px 12px;"
            % (theme.hexa(self._banner_token, theme.warna("ink_dim")),
               theme.warna("head"), theme.warna("line")))

    # ------------------------------------------------------------------ TAMPILAN (tema)
    def _seg_appearance(self):
        """Kontrol cepat APPEARANCE di sudut kanan menu bar (4 palet)."""
        bungkus = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(bungkus)
        lay.setContentsMargins(6, 0, 8, 0)
        lay.setSpacing(6)
        judul = QtWidgets.QLabel("APPEARANCE")
        judul.setObjectName("CtlLabel")
        theme.set_peran(judul, "faint")
        lay.addWidget(judul)
        self.seg_tema = Segmen([(n, theme.label(n),
                                 "use the %s field (applies immediately)" % theme.label(n))
                                for n in theme.tampil()])
        self.seg_tema.set_dipilih(theme.aktif())
        self.seg_tema.dipilih.connect(self._ganti_tema)
        lay.addWidget(self.seg_tema)
        return bungkus

    def _ganti_tema(self, nama):
        """Ganti palet TANPA restart: satu stylesheet, semua widget ikut.

        Pelajaran yang sudah pernah menggagalkan pekerjaan ini: matplotlib memulihkan
        tema putih tiap `axes.clear()`, dan gaya per-widget bisa tertinggal. Karena itu
        (a) tidak ada lagi warna keras di stylesheet per-widget — semua lewat objectName
        + properti `peran` di theme.qss(), (b) setelah stylesheet diganti, widget yang
        menyimpan warna di dalam data (baris log, banner, peta) digambar ulang di sini.
        """
        if nama == theme.aktif():
            self._tema_ulang()
            return
        theme.apply(nama, QtWidgets.QApplication.instance())
        self._tema_ulang()
        self._log("appearance: %s" % theme.label(nama))

    def _tema_ulang(self):
        """Segarkan widget yang menyimpan warna di dalam data/berkas sendiri."""
        if hasattr(self, "seg_tema"):
            self.seg_tema.set_dipilih(theme.aktif())
        for n, a in getattr(self, "act_tema", {}).items():
            a.blockSignals(True)
            a.setChecked(n == theme.aktif())
            a.blockSignals(False)
        self._set_mark_merek()
        if hasattr(self, "banner"):
            self._set_banner(getattr(self, "_banner_teks", ""), getattr(self, "_banner_token", "dim"))
        if hasattr(self, "_render_log"):
            self._render_log()
        for w in self.findChildren(QtWidgets.QWidget):
            w.update()                       # widget yang menggambar sendiri (meter/chip/peta)
        try:
            self.settings.setValue("appearance", theme.aktif())
        except Exception:                                     # noqa: BLE001
            pass

    def _perbarui_seg_layout(self):
        """Samakan tombol LAYOUT di header dengan preset yang sedang berlaku."""
        seg = getattr(self, "seg_layout", None)
        if seg is not None:
            seg.set_dipilih(self._preset)

    def _set_mark_merek(self):
        """Mark merek harus ikut berganti di field terang (mark gelap jadi tak terlihat)."""
        if not hasattr(self, "brand_mark"):
            return
        gelap = theme.aktif() != "ivory"
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ikon",
                            "dvx_mark_terang.png" if gelap else "dvx_mark_gelap.png")
        pix = QtGui.QPixmap(path)
        if pix.isNull():
            self.brand_mark.clear()
            return
        self.brand_mark.setPixmap(pix.scaled(31, 31, Qt.KeepAspectRatio,
                                            Qt.SmoothTransformation))

    def _build_header(self):
        """Header merek: mark + wordmark DeepVertex · MISSION CONSOLE + LAYOUT + chip status.

        Mark 31 px dengan wordmark 18 px ExtraBold mengikuti rasio kit brand (2,37).
        Tinggi TIDAK dipatok: kalau jendela sempit, isinya membungkus — tidak ada yang
        terpotong (aturan keras pemilik).
        """
        h = QtWidgets.QWidget()
        h.setObjectName("Header")
        h.setAttribute(Qt.WA_StyledBackground, True)   # QWidget biasa: lihat StatusBar
        baris1 = QtWidgets.QHBoxLayout(h)
        baris1.setContentsMargins(12, 6, 12, 6)
        baris1.setSpacing(12)
        self.brand_mark = QtWidgets.QLabel()
        self.brand_mark.setFixedSize(31, 31)
        brand = QtWidgets.QWidget()
        bl = QtWidgets.QHBoxLayout(brand)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(9)
        bl.addWidget(self.brand_mark)
        self.brand_word = QtWidgets.QLabel("DeepVertex")
        self.brand_word.setObjectName("BrandWord")
        bl.addWidget(self.brand_word)
        baris1.addWidget(brand)
        sep = QtWidgets.QFrame()
        sep.setObjectName("StatusSep")
        sep.setFixedSize(1, 26)
        baris1.addWidget(sep)
        judul = QtWidgets.QWidget()
        jl = QtWidgets.QVBoxLayout(judul)
        jl.setContentsMargins(0, 0, 0, 0)
        jl.setSpacing(1)
        t = QtWidgets.QLabel("MISSION CONSOLE")
        t.setObjectName("HeaderTitle")
        s = QtWidgets.QLabel("BlueROV2 Heavy · Stonefish 1.6 · ArduSub SITL · VINS-Fusion")
        s.setObjectName("HeaderSub")
        theme.set_peran(s, "faint")
        # subjudul boleh menyusut (bukan mendorong chip keluar tepi jendela)
        s.setWordWrap(True)          # layar sempit: membungkus, bukan terpotong
        s.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        s.setToolTip("BlueROV2 Heavy · Stonefish 1.6 · ArduSub SITL · VINS-Fusion")
        jl.addWidget(t)
        jl.addWidget(s)
        baris1.addWidget(judul, 1)
        lb = QtWidgets.QLabel("LAYOUT")
        lb.setObjectName("CtlLabel")
        theme.set_peran(lb, "faint")
        baris1.addWidget(lb)
        self.seg_layout = Segmen([(k, self.NAMA_PRESET[k],
                                   "apply the %s layout (applies immediately)"
                                   % self.NAMA_PRESET[k])
                                  for k in ("mission-ops", "pilot-focus")])
        self.seg_layout.set_dipilih(self._preset)
        self.seg_layout.dipilih.connect(self._apply_preset)
        baris1.addWidget(self.seg_layout)

        # ---- chip status: 6 chip (chip DDS dihapus — infonya ada di status bar).
        # Di baris YANG SAMA seperti mockup; kalau jendela sempit, chip membungkus. ----
        self.chip_bar = FlowBox(spacing=4)
        self.chip_op = W.Chip("operator")
        self.chip_sim = W.Chip("sim")
        self.chip_vio = W.Chip("vio")
        self.chip_mav = W.Chip("mavlink")
        self.chip_mode = W.Chip("mode")
        self.chip_pad = W.Chip("pad")
        # Nama chip lama (chip_dds) tidak lagi dibuat; kalau ada kode/uji lama yang
        # menyentuhnya, `getattr(self, "chip_dds", None)` mengembalikan None (aman).
        self.chip_dds = None
        for c in (self.chip_op, self.chip_sim, self.chip_vio, self.chip_mav, self.chip_mode,
                  self.chip_pad):
            self.chip_bar.tambah(c)
        baris1.addWidget(self.chip_bar)
        self.chip_op.set_state("ink_faint", "standby")
        self.chip_mode.set_state("warn", "waiting")
        self.chip_pad.set_state("warn", "off")
        self._set_mark_merek()
        return h

    def _panel_flight(self):
        p = W.Panel("Flight · ArduSub / Pixhawk", 'mode · depth · fused AGL', domain="field")
        self.horizon = W.Horizon()
        self.horizon.setFixedHeight(124)
        p.body_layout.addWidget(self.horizon)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self.r_depth = W.Readout("Depth", "m", size=16, color=theme.TEXT, fmt="{:.2f}")
        # AGL dari /rov/altitude (FUSI depth+stereo, Tahap C) — bukan lagi asumsi
        # seabed_z - depth; kalau fusi belum ada nilainya "—" (jujur, bukan menebak).
        self.r_alt = W.Readout("Altitude AGL", "m", size=16, color=theme.ACCENT, fmt="{:.2f}")
        row.addWidget(self.r_depth, 1)
        row.addWidget(self.r_alt, 1)
        p.body_layout.addLayout(row)
        # lingkaran horizon disejajarkan vertikal dengan midpoint konten
        # Depth & Altitude AGL (bukan sekadar tengah panel — lihat widgets.Horizon)
        self.horizon.anchor_a = self.r_depth
        self.horizon.anchor_b = self.r_alt

        # indikator pilot: status fusi + status kalibrasi/hover (permintaan pemilik
        # 23 Sep: "make sure setiap indikator sudah siap ... pastikan pilot tidak buta")
        ind = QtWidgets.QHBoxLayout()
        ind.setSpacing(6)
        self.chip_fusi = W.Chip('fusion')
        # Netral, bukan 'warn waiting' (28 Sep): di sesi tuning PID / manual tidak ada
        # sumber stereo sama sekali (relay hanya hidup saat misi), jadi chip ini tidak
        # boleh tampak memberi peringatan sebelum node fusi mengirim status sebenarnya.
        self.chip_fusi.set_state("ink_faint", 'standby')
        self.l_hover = QtWidgets.QLabel("hover —")
        self.l_hover.setObjectName("MicroLabel")
        theme.set_peran(self.l_hover, "dim")
        # kolom telemetri sempit: ringkas di layar, teks lengkap di tooltip
        self.l_hover.setWordWrap(True)      # teks dinamis: membungkus, bukan melebarkan panel
        self.l_hover.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                   QtWidgets.QSizePolicy.Preferred)
        self.l_hover.setToolTip('D_seabed / hover calibration state from /rov/altitude/calib')
        ind.addWidget(self.chip_fusi)
        ind.addWidget(self.l_hover, 1)
        p.body_layout.addLayout(ind)

        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        self.r_mode = W.Readout("Mode", "", size=13, color=theme.TEXT)
        self.r_armed = W.Readout("Armed", "", size=13, color=theme.TEXT)
        self.r_heading = W.Readout("Heading", "°", size=13, fmt="{:.0f}")
        self.r_throttle = W.Readout("Throttle", "%", size=13, fmt="{:.0f}")
        grid.addWidget(self.r_mode, 0, 0)
        grid.addWidget(self.r_armed, 0, 1)
        grid.addWidget(self.r_heading, 1, 0)
        grid.addWidget(self.r_throttle, 1, 1)
        p.body_layout.addLayout(grid)

        self.m_roll = W.Meter("roll", -45, 45, color=theme.CYAN)
        self.m_pitch = W.Meter("pitch", -45, 45, color=theme.CYAN)
        self.m_yaw = W.Meter("yaw", -180, 180, color=theme.CYAN, decimals=0)
        for m in (self.m_roll, self.m_pitch, self.m_yaw):
            p.body_layout.addWidget(m)
        return p

    def _panel_gyro(self):
        p = W.Panel("Gyro · angular velocity", "rad/s", domain="marine")
        self.m_gx = W.Meter("gx", -2, 2, color=theme.ACCENT, decimals=2)
        self.m_gy = W.Meter("gy", -2, 2, color=theme.ACCENT, decimals=2)
        self.m_gz = W.Meter("gz", -2, 2, color=theme.ACCENT, decimals=2)
        for m in (self.m_gx, self.m_gy, self.m_gz):
            p.body_layout.addWidget(m)
        self.r_acc = W.Readout("accel x/y/z", "m/s²", size=11, fmt="{:+.2f}")
        p.body_layout.addWidget(self.r_acc)
        return p

    def _panel_power(self):
        p = W.Panel("Power & environment", "Pixhawk", domain="marine")
        g = QtWidgets.QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(2)
        self.r_volt = W.Readout("Battery", "V", size=13, fmt="{:.2f}")
        self.r_curr = W.Readout("Current", "A", size=13, fmt="{:.2f}")
        self.r_batt = W.Readout("Remaining", "%", size=13, fmt="{:.0f}")
        self.r_temp = W.Readout("Temp", "°C", size=13, fmt="{:.1f}")
        self.r_pabs = W.Readout("Press abs", "mbar", size=13, fmt="{:.0f}")
        self.r_pdif = W.Readout("Press diff", "mbar", size=13, fmt="{:.1f}")
        for i, w in enumerate((self.r_volt, self.r_curr, self.r_batt,
                               self.r_temp, self.r_pabs, self.r_pdif)):
            g.addWidget(w, i // 2, i % 2)
        p.body_layout.addLayout(g)
        return p

    def _panel_vio(self):
        p = W.Panel('VIO / sim state', "VINS-Fusion", domain="digital")
        g = QtWidgets.QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(2)
        self.r_vins_hz = W.Readout("VINS rate", "Hz", size=13, fmt="{:.1f}")
        self.r_gt_hz = W.Readout("GT rate", "Hz", size=13, fmt="{:.0f}")
        self.r_imu_hz = W.Readout("IMU rate", "Hz", size=13, fmt="{:.0f}")
        self.r_err = W.Readout("Error now", "m", size=13, fmt="{:.3f}")
        self.r_rmse = W.Readout("RMSE", "m", size=13, fmt="{:.3f}")
        self.r_scale = W.Readout("Scale VIO/GT", "", size=13, fmt="{:.3f}")
        for i, w in enumerate((self.r_vins_hz, self.r_gt_hz, self.r_imu_hz,
                               self.r_err, self.r_rmse, self.r_scale)):
            g.addWidget(w, i // 2, i % 2)
        p.body_layout.addLayout(g)
        return p

    def _panel_tuning(self):
        """Panel AUTO-PID: status baris TUNE| terakhir + riwayat + hint parameter ArduSub."""
        p = W.Panel("Auto-PID (ArduSub)", "Tuning ▸ Auto-PID · Ctrl+T", domain="session")
        self.tune_lab = QtWidgets.QLabel('not running yet — Tuning ▸ Auto-PID menu')
        self.tune_lab.setObjectName("MicroLabel")
        theme.set_peran(self.tune_lab, "dim")
        self.tune_lab.setWordWrap(True)
        p.body_layout.addWidget(self.tune_lab)
        self.tune_rows = []
        for i in range(5):
            lab = QtWidgets.QLabel("—" if i == 0 else "")
            lab.setObjectName("Faint")
            theme.set_peran(lab, "faint")
            lab.setWordWrap(True)           # baris TUNE| panjang: membungkus di rel sempit
            lab.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            p.body_layout.addWidget(lab)
            self.tune_rows.append(lab)
        hint = QtWidgets.QLabel('parameters being tuned = the genuine ArduSub parameters (PSC_D_* depth · ATC_* '
'yaw/roll/pitch) → also applies on the real vehicle')
        hint.setObjectName("Faint")
        theme.set_peran(hint, "faint")
        hint.setWordWrap(True)
        hint.setMinimumWidth(0)          # boleh menyusut: teks membungkus, panel tidak melar
        hint.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        p.body_layout.addWidget(hint)
        # REALTIME (permintaan pemilik 27 Sep): "tampilkan data respon sistemnya gimana dan
        # data errornya gimana … window baru saat masuk ke mode tuning PID".
        baris = QtWidgets.QHBoxLayout()
        baris.setSpacing(4)
        self.btn_respon = QtWidgets.QPushButton('RESPONSE + ERROR ▸')   # teks pendek: rel 340 px
        self.btn_respon.setCursor(Qt.PointingHandCursor)
        self.btn_respon.setToolTip('Open a SEPARATE PYTHON WINDOW: system response y(t) + reference r(t) and error e(t) '
'realtime. While Auto-PID runs, each ITERATION is drawn as its own trace (different '
'colour & line style, MATLAB step() style) with stepinfo per iteration. During a '
'mission it shows target_z vs depth.')
        self.btn_respon.clicked.connect(self._buka_respon)
        baris.addWidget(self.btn_respon)
        self.lbl_respon = QtWidgets.QLabel('not opened yet')
        self.lbl_respon.setObjectName("Faint")
        theme.set_peran(self.lbl_respon, "faint")
        baris.addWidget(self.lbl_respon, 1)
        p.body_layout.addLayout(baris)
        sumber = QtWidgets.QLabel(
            'data source: /rov/tuning/sampel (tuner) · /rov/target_z + /rov/depth (mission) · '
            'depth feedback = the ArduSub depth sensor (Bar30); h-fusion/stereo NOT used here')
        sumber.setObjectName("Faint")
        theme.set_peran(sumber, "faint")
        sumber.setWordWrap(True)
        sumber.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        p.body_layout.addWidget(sumber)
        return p

    def _buka_respon(self, otomatis=False):
        """Buka jendela REALTIME respon sistem + galat (gui/pid_view.py) di PROSES TERPISAH.

        Permintaan pemilik 27 Sep: "tampilkan data respon sistemnya gimana dan data errornya
        gimana … window baru saat masuk ke mode tuning PID". Plot realtime tidak digambar di
        dalam proses Qt (pelajaran yang sama dengan peta 3D §12: GUI jadi tersendat) — GUI
        hanya meluncurkan prosesnya.
        """
        import subprocess
        if otomatis and getattr(self, "_respon_pid", None):
            try:
                if self._respon_pid.poll() is None:              # sudah terbuka, jangan dobel
                    self.lbl_respon.setText('already open (pid %d)' % self._respon_pid.pid)
                    return self._respon_pid
            except Exception:                                     # noqa: BLE001
                pass
        alat = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pid_view.py")
        # KELUARAN JENDELA DICATAT KE BERKAS (bukan dibuang): 27 Sep jendela otomatis mati tanpa
        # jejak karena stdout/stderr diarahkan ke /dev/null sehingga kita buta soal sebabnya.
        import time as _t
        log = "/tmp/rov_pidview_%s.log" % _t.strftime("%H%M%S")
        try:
            fh = open(log, "w")
        except Exception:                                          # noqa: BLE001
            fh = subprocess.DEVNULL
        try:
            proc = subprocess.Popen([sys.executable, alat], start_new_session=True,
                                    stdout=fh, stderr=subprocess.STDOUT)
            self._respon_pid = proc
            self._respon_log = log
            self.lbl_respon.setText('open (pid %d)' % proc.pid)
            self.lbl_respon.setToolTip('window output: %s' % log)
            self._log('RESPONSE: realtime window opened (pid %d) — output to %s'
                      % (proc.pid, log))
            return proc
        except Exception as exc:                                   # noqa: BLE001
            self.lbl_respon.setText('failed to open')
            self._log('RESPONSE: failed to open the window: %r' % (exc,))
            return None

    def _banner_respon(self):
        """Sebut di baris Auto-PID bahwa jendela respon sudah terbuka (jejak yang bisa dibaca)."""
        try:
            self._tuning_line('TUNE|RESPONSE + ERROR window opened in a separate process (gui/pid_view.py) — each '
'iteration has its own colour & line style')
        except Exception:                                          # noqa: BLE001
            pass

    def _tuning_line(self, line):
        """Umpankan satu baris log komponen `tuning` ke panel AUTO-PID."""
        if not self._di_gui():                    # dari thread komponen tuning
            self._ui(lambda: self._tuning_line(line))
            return
        txt = str(line).strip()
        if not hasattr(self, "tune_rows"):
            return
        if txt.startswith("TUNE|"):
            txt = txt.split("|", 1)[1].strip()
            self.tune_lab.setText(txt[:160])
            self.tune_rows = getattr(self, "tune_rows", [])
            for i in range(len(self.tune_rows) - 1, 0, -1):
                self.tune_rows[i].setText(self.tune_rows[i - 1].text())
            if self.tune_rows:
                self.tune_rows[0].setText(txt[:120])
        elif "ARMED" in txt.upper() or "GAGAL" in txt.upper() or "ABORT" in txt.upper():
            self.tune_lab.setText(txt[:160])

    def _panel_events(self):
        p = W.Panel("ArduSub messages", "statustext", domain="neutral")
        self.events = []
        for i in range(4):
            lab = QtWidgets.QLabel('no messages' if i == 0 else "—")
            lab.setObjectName("Faint")
            theme.set_peran(lab, "faint")
            lab.setWordWrap(True)           # statustext panjang jangan melebarkan rel kiri
            lab.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
            p.body_layout.addWidget(lab)
            self.events.append(lab)
        return p

    def _build_statusbar(self):
        """Status bar BERSEMEN (garis pemisah 1 px antar kelompok) dan MEMBUNGKUS.

        Deretan pipa lama diganti kelompok bersegmen — dan karena tingginya tidak
        dipatok + memakai FlowLayout, pada jendela sempit kelompoknya turun ke baris
        kedua, bukan terpotong.
        """
        b = StatusBar()
        self._statusbar = b
        lay = b.lay
        self.status = QtWidgets.QLabel('initialising …')
        self.status.setObjectName("StatusText")
        theme.set_peran(self.status, "dim")
        lay.addWidget(self.status)
        # Kelompok lain dibangun ulang setiap kali status berubah: [garis 1 px][teks]
        # (rancangan §9). Semuanya di SATU FlowLayout → barisnya membungkus sendiri,
        # tanpa wadah bersarang yang tingginya sulit diprediksi Qt.
        # Kelompok status dibuat SEKALI di sini (bukan saat status berubah): membuat widget
        # di dalam slot yang bisa dipanggil dari thread lain adalah penyebab crash 28 Sep.
        self.status_grup = None          # nama lama; kelompoknya kini di _grup_widget
        self._grup_widget = []
        for _ in range(self.MAKS_GRUP):
            garis = QtWidgets.QFrame()
            garis.setObjectName("StatusSep")
            garis.setFixedSize(1, 12)
            lab = QtWidgets.QLabel("")
            lab.setObjectName("StatusText")
            theme.set_peran(lab, "faint")
            lay.addWidget(garis)
            lay.addWidget(lab)
            self._grup_widget += [garis, lab]
        self.status_catatan = QtWidgets.QLabel("")
        self.status_catatan.setObjectName("StatusNote")
        theme.set_peran(self.status_catatan, "warn")
        lay.addWidget(self.status_catatan)
        return b

    MAKS_GRUP = 8        # jumlah kelompok status yang disiapkan (dibuat sekali, dipakai ulang)

    def _pasangan_grup(self):
        """[(garis pemisah, label), ...] kelompok status yang sudah dibuat saat build."""
        w = getattr(self, "_grup_widget", [])
        return [(w[i], w[i + 1]) for i in range(0, len(w) - 1, 2)]

    def _set_status(self, teks):
        """Isi status bar: pisahkan deretan pipa lama jadi kelompok bersegmen.

        Sumber teksnya (rosiface/maviface) mengirim satu baris dengan pemisah '·' atau
        '|'; di sini dipisah jadi kelompok supaya terbaca sebagai segmen, bukan deretan
        pipa. Tidak ada teks yang dibuang.

        Dipanggil dari thread pekerja (ROS/MAVLink) → pekerjaannya DITITIPKAN ke thread GUI
        dan widget kelompok DIPAKAI ULANG (tidak ada widget baru dibuat di sini).
        """
        if not self._di_gui():
            self._ui(lambda: self._set_status(teks))
            return
        teks = str(teks or "")
        if teks == self._status_terakhir:          # hemat: emitor sering mengulang teks sama
            return
        self._status_terakhir = teks
        bagian = [teks]
        for pemisah in (" | ", "·"):
            if pemisah in teks:
                bagian = [s.strip() for s in teks.split(pemisah) if s.strip()]
                break
        self.status.setText(bagian[0] if bagian else "")
        for i, (garis, lab) in enumerate(self._pasangan_grup()):
            isi = bagian[1 + i] if 1 + i < len(bagian) else ""
            lab.setText(isi)
            garis.setVisible(bool(isi))
            lab.setVisible(bool(isi))
        if hasattr(self, "_statusbar"):
            self._statusbar.sesuaikan()

    # ================================================================== #
    # Sumber data
    # ================================================================== #
    def _start_ros(self):
        from rosiface import RosBridge
        self.ros = RosBridge(preview_capture=self.args.capture_preview)
        self.ros.sig_image.connect(self._on_image, Qt.QueuedConnection)
        self.ros.sig_capture.connect(self._on_capture, Qt.QueuedConnection)
        self.ros.sig_gt.connect(self._on_gt, Qt.QueuedConnection)
        self.ros.sig_vio.connect(self._on_vio, Qt.QueuedConnection)
        self.ros.sig_depth.connect(self._on_depth, Qt.QueuedConnection)
        self.ros.sig_imu.connect(self._on_imu, Qt.QueuedConnection)
        self.ros.sig_rates.connect(self._on_rates, Qt.QueuedConnection)
        self.ros.sig_status.connect(self._set_status, Qt.QueuedConnection)
        # pilot/manual (23 Sep 2026)
        self.ros.sig_agl.connect(self._on_agl, Qt.QueuedConnection)
        self.ros.sig_calib.connect(self._on_calib, Qt.QueuedConnection)
        self.ros.sig_mode.connect(self._on_mode, Qt.QueuedConnection)
        self.ros.sig_pad.connect(self._on_pad, Qt.QueuedConnection)
        self.ros.sig_joy.connect(self._on_joy, Qt.QueuedConnection)
        self.ros.sig_cam_fb.connect(self._on_cam_fb, Qt.QueuedConnection)
        self.ros.sig_plan.connect(self._on_plan, Qt.QueuedConnection)
        self.ros.start()

    def _start_mav(self):
        from maviface import MavBridge
        self.mavbridge = MavBridge(url=self.args.mavlink)
        self.mavbridge.sig_attitude.connect(self._on_attitude, Qt.QueuedConnection)
        self.mavbridge.sig_vehicle.connect(self._on_vehicle, Qt.QueuedConnection)
        self.mavbridge.sig_text.connect(self._on_text, Qt.QueuedConnection)
        self.mavbridge.sig_status.connect(self._set_status, Qt.QueuedConnection)
        self.mavbridge.start()

    # ---------------- slot ROS ---------------- #
    def _on_sim_frame(self, img):
        if self.sim_tile is None:
            return
        data = img.tobytes("raw", "RGB")
        qimg = QtGui.QImage(data, img.width, img.height, img.width * 3,
                            QtGui.QImage.Format_RGB888).copy()
        self.sim_tile.set_qimage(qimg)

    def _on_image(self, side, arr):
        if side == "front":
            if hasattr(self, "cam_front"):
                self.cam_front.set_frame(arr, rate=self.rates.get("front"))
            return
        tile = self.cam_l if side == "left" else self.cam_r
        tile.set_frame(arr, rate=self.rates.get("vio_left" if side == "left" else "vio_right"))
        if not hasattr(self, "cam_cap"):
            self.chip_vio.set_state(theme.OK if self.rates.get("vio_left", 0) > 5 else theme.WARN,
                                    f"{self.rates.get('vio_left', 0):.0f} Hz")

    # ---------------- pilot/manual (23 Sep 2026) ----------------
    def _kunci_tooltip(self, label, teks):
        """Simpan teks penuh di tooltip untuk label yang boleh membungkus/menyusut."""
        try:
            label.setToolTip(teks)
        except Exception:                                     # noqa: BLE001
            pass

    def _on_agl(self, h, status):
        """Altitude AGL dari fusi (Tahap C). Status: OK/DEGRADED/INVALID/STANDBY.

        STANDBY (28 Sep) = sumber stereo /rov/stereo/* belum pernah ada (relay hanya
        hidup saat misi autonomous; sesi tuning PID memakai sensor depth, bukan fusi).
        Chip-nya netral, BUKAN galat — pemilik membacanya sebagai error selama ini.
        """
        self.r_alt.set_value(h)
        st = (status or "STANDBY").upper()
        if st == "OK":
            self.chip_fusi.set_state("ok", "OK")
        elif st == "DEGRADED":
            self.chip_fusi.set_state("warn", "DEGRADED")
        elif st == "STANDBY":
            # token netral (bukan hex): chip redup, bukan merah/galat
            self.chip_fusi.set_state("ink_faint", "STANDBY")
        else:
            self.chip_fusi.set_state("err", "INVALID")
        self.horizon.anchor_b = self.r_alt
        self.horizon.update()

    def _on_calib(self, text):
        """Ringkas /rov/altitude/calib -> indikator hover/kalibrasi pilot."""
        if not hasattr(self, "l_hover"):
            return
        # Tampilkan ringkas (kolom telemetri sempit); teks penuh di tooltip, karena
        # teks panjang di sini dulu terpotong di tepi kolom.
        tok = (text or "").split()
        self.l_hover.setText(" ".join(tok[:2]) + ("…" if len(tok) > 2 else ""))
        self.l_hover.setToolTip(text or "")
        stable = "hover=STABIL" in text
        theme.set_peran(self.l_hover, "ok" if stable else "dim")

    def _init_sfm(self):
        """Tahap H-map: pantau berkas orthomosaic + muat ulang bila berubah."""
        t = QtCore.QTimer(self)
        t.timeout.connect(self._reload_sfm)
        t.start(2000)
        self._reload_sfm()

    def _set_tampilan_sfm(self, kunci):
        """Pilih apa yang ditampilkan di SATU jendela SFM: mozaik 2D (bawaan) atau peta gabungan."""
        self._tampilan_sfm = kunci
        self._sfm_mtime = None                    # paksa muat ulang gambar
        self._reload_sfm()

    def _dem_gagal(self):
        """Isi `<run>/dem/dem_gagal.json` (sebab DEM tidak dibangun) atau None.

        Dipakai tombol SHOW 3D MODEL + label panel SFM supaya GUI menyebut SEBAB, ANGKA, dan
        cara memperbaiki yang SAMA dengan `dem_bathimetri.py`, bukan hanya "no DEM yet".
        """
        import json
        p = os.path.join(getattr(self, "_run_dir_sfm", ""), "dem", "dem_gagal.json")
        try:
            with open(p) as fh:
                return json.load(fh)
        except Exception:                                          # noqa: BLE001
            return None

    def _teks_dem_gagal(self, g):
        """Satu baris (bahasa Inggris — aturan GUI) dengan angka identik dari dem_gagal.json.

        Kata "failed" sengaja ada: pengklasifikasi kategori di `stackui._log` menandai baris ini
        sebagai WARN, sehingga pilot menemukannya lewat saring "Warn" tanpa membaca semua baris.
        """
        n, butuh = g.get("titik_dipakai", "?"), g.get("titik_dibutuhkan", "?")
        t = '3D DEM NOT BUILT (the 3D map failed for this run): %s capture points, %s required' % (
            n, butuh)
        if g.get("cukup_dengan_leg_semua"):
            t += (' — re-run the DEM tool with --leg semua: %s points would qualify'
                  % g.get("titik_semua_kaki"))
        else:
            t += (' — enlarge the mission AREA (spinbox in the Autonomous mission panel, half-side '
                  '>= 2.0 m = 4x4 m); --leg semua alone is not enough (%s points)'
                  % g.get("titik_semua_kaki"))
        t += (' · of %s captures, %s dropped as diagonal/turn legs'
              % (g.get("baris_capture"), g.get("titik_transit_dibuang")))
        return t

    def _segarkan_status_dem(self):
        """Tambahkan status 3D ke label panel SFM — DEM dibuat SESUDAH mozaik, jadi dicek ulang.

        Teks label idempoten (sufiks hanya ditambahkan sekali); baris LOG hanya ditulis saat berkas
        sebabnya BARU (mtime berubah), bukan tiap timer. Tanpa ini, run yang DEM-nya gagal tetap
        terlihat "wajar" (mozaik 2D tampil) sampai tombol 3D ditekan.
        """
        g = self._dem_gagal()
        if not g:
            return
        tag = (' · 3D: NOT BUILT (%s/%s points)'
               % (g.get("titik_dipakai"), g.get("titik_dibutuhkan")))
        if '3D: NOT BUILT' not in self.lbl_sfm_status.text():
            self.lbl_sfm_status.setText(self.lbl_sfm_status.text() + tag)
        p = os.path.join(getattr(self, "_run_dir_sfm", ""), "dem", "dem_gagal.json")
        try:
            mt = os.path.getmtime(p)
        except OSError:
            return
        if mt == getattr(self, "_dem_gagal_mtime", None):
            return
        self._dem_gagal_mtime = mt
        self._log(self._teks_dem_gagal(g))

    def _buka_3d(self):
        """Buka DEM/peta 3D di JENDELA PYTHON TERPISAH (lihat gui/dem3d_viewer.py).

        Permintaan pemilik 27 Sep: kanvas matplotlib 3D yang ditanam di GUI bikin GUI ngelag,
        jadi 3D keluar dari proses ini. Tombol ini hanya meluncurkan proses baru.
        """
        import subprocess
        run = getattr(self, "_run_dir_sfm", "")
        if not run:
            self._log('3D: run folder not known yet — launch via rovgui / --log-dir')
            return
        p_dem = os.path.join(run, "dem", "dem.npy")
        if not os.path.exists(p_dem):
            g = self._dem_gagal()
            if g:
                # DEM SUDAH dicoba dan gagal: sebut SEBAB + ANGKA + cara memperbaiki. Dulu hanya
                # "this run has no DEM yet", yang pada run yang rantainya sudah selesai terbaca
                # sebagai "tunggu sebentar" padahal menunggu tidak akan menolong (30 Sep 2026).
                self._log(self._teks_dem_gagal(g))
                self._log('3D: full message (same numbers) in %s'
                          % os.path.join(run, "dem", g.get("log", "dem_bathimetri.log")))
            else:
                self._log('3D: this run has no DEM yet (%s) — created automatically after the mission finishes; '
    'or open a run that already has dem/' % p_dem)
            return
        alat = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dem3d_viewer.py")
        try:
            proc = subprocess.Popen([sys.executable, alat, run], start_new_session=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._log('3D: separate Python window opened (pid %d) — GUI stays light'
                      % proc.pid)
        except Exception as exc:                                   # noqa: BLE001
            self._log('3D: failed to open the window: %r' % (exc,))

    def _buka_gabungan(self):
        """Buka gambar PETA GABUNGAN (foto + relief abu) dengan penampil gambar sistem.

        Bukan di dalam GUI: citra itu besar (1,4 MB, 3200 px) dan tidak perlu jadi sub-window.
        """
        import subprocess
        kandidat = [os.path.join(getattr(self, "_run_dir_sfm", ""), "sfm", "peta_gabungan.png"),
                    getattr(self, "gabung_preview", "")]
        p = next((q for q in kandidat if q and os.path.exists(q)), "")
        if not p:
            self._log('combined map not available yet (created automatically after the mission finishes)')
            return
        try:
            proc = subprocess.Popen(["xdg-open", p], start_new_session=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._log('combined map opened with the system image viewer (pid %d): %s' % (proc.pid, p))
        except Exception as exc:                                   # noqa: BLE001
            self._log('failed to open the combined map: %r' % (exc,))

    def _reload_sfm(self):
        """Muat MOZAIK 2D ke jendela "MOZAIK MAPPING RESULT" (satu jendela, tanpa tab).

        Peta 3D TIDAK di sini — jendela Python terpisah (`_buka_3d`), supaya GUI tidak
        menggambar ulang figur matplotlib di dalam proses Qt (penyebab ngelag, 27 Sep).
        """
        p = getattr(self, "sfm_path", "")
        if not p:
            if getattr(self, "_sfm_kunci", None) != "2d":
                self._sfm_kunci = "2d"
                self._sfm_mtime = None
                self.lbl_sfm_status.setText('waiting for mosaic…')
            self._segarkan_status_dem()            # DEM dibuat SESUDAH mozaik: cek tiap timer
            return
        try:
            mt = os.path.getmtime(p)
        except OSError:
            return
        if mt == self._sfm_mtime:
            self._segarkan_status_dem()            # mozaik tidak berubah, sebab DEM bisa baru
            return
        img = QtGui.QImage(p)
        if img.isNull():
            return
        note = self._sfm_note(p)
        self._sfm_mtime = mt
        self._sfm_kunci = "2d"
        self.sfm_tile.set_static_image(img, note)
        self.p_sfm.set_hint(note)
        kual = self._kualitas_sfm()
        self.lbl_sfm_status.setText(('2D MOSAIC · %s' % kual) if kual else '2D MOSAIC')
        self._log('SFM: mosaic loaded from %s' % p)
        self._segarkan_status_dem()                # + baris sebab bila DEM sudah gagal dibangun

    def _kualitas_sfm(self):
        """Baris status RINGKAS kualitas DEM (teks penuh di tooltip).

        Ringkas karena kolom tengah sempit: dulu "kualitas DEM: GAGAL · derau 0.043 m ·
        variasi/derau 9.2x" terpotong di tepi panel (terlihat di tangkapan layar verifikasi).
        """
        import json
        p = os.path.join(getattr(self, "_run_dir_sfm", ""), "dem", "dem_kualitas.json")
        try:
            d = json.load(open(p))
        except Exception:                                          # noqa: BLE001
            return ""
        # Sebutkan BUTIR yang gagal/peringatan (permintaan pemilik 27 Sep: "kenapa ada
        # tulisan DEM GAGAL?" — verdict global tanpa nama butir mudah dibaca sebagai
        # "peta rusak", padahal yang gagal bisa satu butir sistematik saja).
        verd = d.get("verdict") or {}
        # urutkan: GAGAL lebih dulu daripada PERINGATAN (jangan sampai butir GAGAL tersembunyi)
        nakal = sorted((k for k, v in verd.items() if v in ("GAGAL", "PERINGATAN")),
                       key=lambda k: 0 if verd[k] == "GAGAL" else 1)
        tag = ("%s%s" % (d.get("keseluruhan", "?"),
                         (" (" + ", ".join(k.split("_")[0] for k in nakal[:2]) + ")")
                         if nakal else ""))
        singkat = 'DEM: %s · noise %d mm · variation %.1fx' % (
            tag, round(1000.0 * float(d.get("derau_rms_m") or 0)),
            float(d.get("rasio_variasi_derau") or 0))
        lengkap = ('DEM quality %s · noise %.3f m · variation/noise %.1fx · %s'
                   % (d.get("keseluruhan", "?"), float(d.get("derau_rms_m") or 0),
                      float(d.get("rasio_variasi_derau") or 0),
                      'bias per AGL band: ' + ", ".join("%+.2f" % b for b in
                                                        (d.get("bias_per_pita_agl") or []))))
        try:
            self.lbl_sfm_status.setToolTip(lengkap)
        except Exception:                                          # noqa: BLE001
            pass
        return singkat

    def _sfm_note(self, p):
        """Ringkas sfm_report.json -> satu baris status jujur untuk panel + hint."""
        import json
        try:
            rep = json.load(open(os.path.join(os.path.dirname(p), "sfm_report.json")))
        except Exception:                             # noqa: BLE001
            return "orthomosaic loaded"
        m = rep.get("mozaik", {}) or {}
        c = rep.get("colmap", {}) or {}
        ext = m.get("extent_m") or ["?", "?"]
        bits = ['%s photos' % m.get("n_images_dipakai", "?"),
                "%sx%s m" % (ext[0], ext[1]),
                "GSD %.1f mm/px" % (1000.0 * float(m.get("gsd_m_per_px") or 0)),
                'coverage %.0f%%' % (100.0 * float(m.get("coverage_frac") or 0))]
        if c.get("ok"):
            bits.append("COLMAP %s/%s (%.2f px)" % (c.get("n_registered"), c.get("n_images"),
                                                    c.get("mean_reproj_error") or -1))
        elif c.get("dijalankan"):
            bits.append('COLMAP failed — geometric mosaic used')
        return " · ".join(bits)

    def _on_mode(self, mode):
        m = (mode or "").strip().upper()
        self._mode = m
        if m == "MAPPING":
            self.chip_mode.set_state(theme.OK, "MAPPING")
            if hasattr(self, "cam_front"):
                self.cam_front.set_muted("front camera active in MANUAL only")
            for t in (self.cam_l, self.cam_r):
                t.set_unmuted()
            self._set_banner("MAPPING mode: VIO stereo preview on, VINS can initialise",
                             theme.TEXT_DIM)
            # offset alignment lama tidak berlaku setelah VINS kehilangan citra
            self.align = None
        elif m == "MANUAL":
            self.chip_mode.set_state(theme.ACCENT, "MANUAL")
            if hasattr(self, "cam_front"):
                self.cam_front.set_unmuted()
            # Preview stereo DIMATIKAN, tetapi kameranya TETAP JALAN untuk VINS
            # (permintaan pemilik 23 Sep 2026: VINS tetap hidup di mode manual).
            for t in (self.cam_l, self.cam_r):
                t.set_muted("cameras still streaming for VINS")
            self._set_banner("MANUAL mode: pilot in control. Stereo preview is off, but the "
                             "cameras keep streaming so VINS stays locked", theme.ACCENT)
        elif m == "FALLBACK":
            # Tahap F (25 Sep): VIO gagal saat mapping -> kamera depan ON (untuk pilot),
            # capture berhenti, misi dihentikan. Preview stereo dimatikan tetapi
            # kameranya TETAP jalan supaya VINS bisa pulih tanpa restart.
            self.chip_mode.set_state(theme.WARN, "FALLBACK")
            if hasattr(self, "cam_front"):
                self.cam_front.set_unmuted()
            for t in (self.cam_l, self.cam_r):
                t.set_muted("fallback: stereo streaming for VINS")
            self._set_banner("FALLBACK mode: VIO failed during mapping - mission stopped, "
                             "front camera on, cameras keep streaming for VINS",
                             theme.WARN)
        else:
            self.chip_mode.set_state(theme.WARN, m or "?")

    def _on_pad(self, status):
        s = (status or "").strip().upper()
        dm = self.ax["deadman"]
        if s == "AKTIF":
            self.chip_pad.set_state(theme.OK, 'ACTIVE')
            self.p_sim.set_hint("front camera · MANUAL mode")
            self._set_banner("pilot ACTIVE — left stick: forward/strafe · right stick: "
                             "heave & yaw · dpad: camera · L2: depth hold", theme.OK)
        elif s == "DEADMAN":
            # Penyebab #1 "ROV tidak mau bergerak": deadman belum ditahan. Pesan panjang
            # ditaruh di banner (wrap selebar jendela), bukan di kepala panel yang sempit.
            self.chip_pad.set_state(theme.WARN, "HOLD L1/LB")
            self._set_banner("deadman not held: HOLD L1/LB (button %d) while moving the "
                             "sticks — otherwise RC stays neutral" % dm, theme.WARN)
        elif s == "TIDAK-ARM":
            self.chip_pad.set_state(theme.ERR, "NOT ARMED")
            self._set_banner("VEHICLE NOT ARMED: thrusters are dead, sticks do nothing — "
                             "check the teleop log (re-arming is retried automatically "
                             "while the deadman is held, or press ARM in the Vehicle menu)",
                             theme.ERR)
        elif s == "KALIBRASI-0":
            self.chip_pad.set_state(theme.WARN, "stick calibrate")
            self._set_banner("measuring stick rest position (1.5 s) — release all controls",
                             theme.WARN)
        elif s == "JOY-BASI":
            self.chip_pad.set_state(theme.ERR, "joy stale")
            self._set_banner("/joy stream stalled (gamepad unplugged?) — RC sent neutral",
                             theme.ERR)
        elif s in ("MATI", "OFF"):
            self.chip_pad.set_state(theme.ERR if self._pad_was_on else theme.TEXT_FAINT, "off")
            if not self._pad_was_on:
                # Gejala paling sering: pilot mendorong stick padahal teleop belum jalan.
                self._set_banner("gamepad teleop NOT running: click START MANUAL PILOT in the "
                                 "STACK panel — switching the camera mode to MANUAL does not "
                                 "start joy/teleop", theme.TEXT_DIM)
        elif s == "MENUNGGU-DEVICE":
            self.chip_pad.set_state(theme.WARN, "no device")
            self._set_banner("waiting for /joy: is the gamepad connected? (check "
                             "/dev/input/js0)", theme.WARN)
        else:
            self.chip_pad.set_state(theme.WARN, s or "?")
        self._pad_was_on = s in ("AKTIF", "DEADMAN")
        # pesan penting tidak boleh langsung ditimpa hint berkala (_poll_embed)
        self._hint_prio = s in ("DEADMAN", "TIDAK-ARM", "JOY-BASI", "KALIBRASI-0",
                                "MENUNGGU-DEVICE")

    def _on_cam_fb(self, pan, tilt):
        """Umpan balik pan/tilt: teks ringkas di label, teks LENGKAP (rad + derajat) di tooltip.

        Teks label sengaja pendek supaya tidak membungkus di rel 340 px; nilai derajat tetap
        tersedia di tooltip. Jumlah argumen format HARUS sama dengan jumlah placeholder —
        kesalahan di sini membuat galat setiap frame (kejadian 28 Sep).
        """
        self.pan, self.tilt = pan, tilt
        if hasattr(self, "r_cam"):
            self._kunci_tooltip(self.r_cam, "pan %+.2f rad · tilt %+.2f rad (%+.0f° / %+.0f°)"
                               % (pan, tilt, pan * R2D, tilt * R2D))
            self.r_cam.setText("pan %+.2f   tilt %+.2f rad" % (pan, tilt))

    def _cam_nudge(self, axis, sign):
        """Geser/naik-turunkan kamera depan satu langkah (mekanisme pan/tilt)."""
        if self.ros is None:
            return
        step = float(self.args.cam_step) * (1.0 if sign > 0 else -1.0)
        if axis == "pan":
            self.pan = max(-self.args.pan_limit, min(self.args.pan_limit, self.pan + step))
            self.ros.set_cam(pan=self.pan)
        else:
            self.tilt = max(-self.args.tilt_limit, min(self.args.tilt_limit, self.tilt + step))
            self.ros.set_cam(tilt=self.tilt)

    def _cam_center(self):
        if self.ros is None:
            return
        self.pan = 0.0
        self.tilt = 0.0
        self.ros.set_cam(pan=0.0, tilt=0.0)

    def _on_capture(self, side, arr):
        if hasattr(self, "cam_cap") and arr.ndim == 3:
            h, w = arr.shape[:2]
            small = arr[::4, ::4]              # 480x300 cukup untuk pratinjau
            # tobytes(): QImage butuh bytes, bukan memoryview (small.data non-kontigu)
            qimg = QtGui.QImage(small.tobytes(), small.shape[1], small.shape[0],
                                small.shape[1] * 3, QtGui.QImage.Format_RGB888).copy()
            self.cam_cap._img = qimg            # noqa: SLF001 (pratinjau sederhana)
            self.cam_cap.update()

    def _on_plan(self, plan):
        """Garis petunjuk (guide) dari node misi -> peta (permintaan pemilik 2 Okt 2026).

        Dua hal yang dijaga:
          * rencana PROVISIONAL dibuang. Node autonomous membangun jalur sekali di kerangka
            DUNIA saat __init__ (sebelum ada pose), lalu membangun ULANG di kerangka POSE
            START begitu odom pertama tiba; yang benar hanya yang kedua. Penandanya:
            `start_frame == 'robot'` tanpa `frame` = pembangunan awal.
          * rencana lama diganti begitu yang baru tiba, jadi kotak misi sebelumnya tidak
            pernah tertinggal di layar seolah-olah masih berlaku.
        """
        if not isinstance(plan, dict):
            return
        if plan.get("start_frame") == "robot" and not plan.get("frame"):
            return
        if getattr(self, "peta", None) is None:
            return
        self.peta.set_rencana(plan)
        kotak = plan.get("kotak_m") or []
        ukuran = ("%.1f x %.1f m" % (kotak[0], kotak[1])) if len(kotak) == 2 else "?"
        self._log("map guide: planned path received — %d waypoints, area %s, line %s m "
                  "(dashed = path to fly, orange = area bound)"
                  % (len(plan.get("wp") or []), ukuran, plan.get("sp", "?")))

    def _on_gt(self, t, n, e, d, yaw):
        self.gt_hist.append((t, n, e, d))
        self.plot.add_gt(n, e)
        self.plot.set_pose(n, e, yaw)
        # peta OSM (26 Sep): GT + pose robot ikut dikirim ke tab PETA
        if getattr(self, "peta", None) is not None:
            self.peta.add_gt(n, e)
            self.peta.set_pose(n, e, yaw)
        self._acc_len("gt", n, e, self.plot.gt)

    def _on_vio(self, t, vx, vy, vz):
        # VIO valid di kedua mode: stereo tetap jalan di MANUAL (untuk VINS), hanya
        # preview-nya yang dimatikan. Tidak ada gating angka VIO di sini.
        if self.align is None:
            gt = self._gt_at(t, tol=1.0)
            if gt is None:
                return
            self.align = (gt[1] - vx, gt[2] + vy, gt[3] + vz)
        a, b, c = self.align
        n, e, d = vx + a, -vy + b, -vz + c
        self.plot.add_vio(n, e)
        if getattr(self, "peta", None) is not None:
            self.peta.add_vio(n, e)
        self._acc_len("vio", n, e, self.plot.vio)
        gt = self._gt_at(t, tol=0.25)
        if gt is not None:
            err = math.dist((n, e, d), (gt[1], gt[2], gt[3]))
            self.errs.append(err)
            self.last_vio = (t, n, e, d, err)
            if len(self.gt_hist) % 4 == 0:
                self.r_err.set_value(err)
                self.r_rmse.set_value(math.sqrt(sum(x * x for x in self.errs) / len(self.errs)))

    def _on_depth(self, d):
        self.last_depth = d
        self.r_depth.set_value(d)
        # AGL TIDAK lagi dihitung dari seabed_z - depth (asumsi dasar datar, salah
        # di atas koral). Sumber AGL = /rov/altitude (fusi, Tahap C) lewat _on_agl.
        # lebar angka bisa berubah -> pusat lingkaran ikut digambar ulang
        self.horizon.update()

    def _on_imu(self, ax, ay, az, gx, gy, gz):
        self.m_gx.set_value(gx)
        self.m_gy.set_value(gy)
        self.m_gz.set_value(gz)
        self.r_acc.set_value(ax, fmt="{:+.2f}")
        self.last_imu = (ax, ay, az, gx, gy, gz)

    def _on_rates(self, rates):
        self.rates = rates
        self.r_vins_hz.set_value(rates.get("vins"))
        self.r_gt_hz.set_value(rates.get("gt"))
        self.r_imu_hz.set_value(rates.get("imu"))
        v = rates.get("vio_left", 0)
        self.chip_vio.set_state(theme.OK if v > 15 else (theme.WARN if v > 3 else theme.ERR),
                                f"{v:.0f} Hz")
        sim_ok = rates.get("gt", 0) > 5
        self.chip_sim.set_state(theme.OK if sim_ok else theme.WARN, "run" if sim_ok else "idle")
        # kamera depan (mode MANUAL) + kesegaran fusi altitude
        fr = rates.get("front", 0.0)
        if hasattr(self, "cam_front"):
            self.cam_front.set_status('standby (active in MANUAL mode)'
                                      if fr < 0.5 else None)
        # chip fusi: 'off' HANYA bila node fusi memang tidak mengirim apa pun (mati/tidak
        # diluncurkan). Kalau statusnya segar (mis. STANDBY karena sesi tuning PID tanpa
        # relay stereo), chip dibiarkan apa adanya — 28 Sep: watchdog laju dulu menimpanya
        # jadi "off" merah tiap detik sehingga terbaca sebagai galat fusi.
        umur_st = rates.get("agl_st_age")
        fusi_mati = (umur_st is None) or (umur_st > 10.0)
        if hasattr(self, "chip_fusi") and rates.get("agl", 0.0) < 0.2 and fusi_mati:
            # fusi tidak mengalir (node belum jalan / stack lama) -> jujur "—"
            self.chip_fusi.set_state(theme.ERR, 'off')
            self.r_alt.set_value(None)

    # ---------------- slot MAVLink ---------------- #
    def _on_attitude(self, roll, pitch, yaw):
        self.horizon.set_attitude(roll, pitch)
        self.m_roll.set_value(roll)
        self.m_pitch.set_value(pitch)
        self.m_yaw.set_value(yaw)

    def _on_vehicle(self, st):
        self.mav = st
        self.r_mode.set_value(st.get("mode") or "--", fmt="{}")
        armed = st.get("armed")
        self.r_armed.set_value("ARMED" if armed else "DISARMED", fmt="{}")
        theme.set_peran(self.r_armed.val, "warn" if armed else "dim")
        self.r_heading.set_value(st.get("heading"))
        self.r_throttle.set_value(st.get("throttle"))
        self.r_volt.set_value(st.get("volt"))
        self.r_curr.set_value(st.get("curr"))
        self.r_batt.set_value(st.get("remaining"))
        self.r_temp.set_value(st.get("temp"))
        self.r_pabs.set_value(st.get("press_abs"))
        self.r_pdif.set_value(st.get("press_diff"))
        if not self.last_depth and st.get("depth") is not None:
            self.r_depth.set_value(st["depth"])
            self.horizon.update()            # jaga-jaga bila angka MAVLink lebih dulu tampil
        link = bool(st.get("msgs"))
        ekf = st.get("ekf_ok")
        state = "ok" if (link and ekf is not False) else ("warn" if link else "err")
        self.chip_mav.set_state(state, f"{st.get('msgs', 0)} msg")

    def _on_text(self, text, sev):
        # Peran warna dari TINGKAT pesan ArduSub (bukan hex keras): teksnya tetap ikut
        # berganti tema, dan teks yang panjang TIDAK dipotong di tengah (panel rel kiri
        # kini lebih lebar: 300-360 px).
        peran = {0: "err", 1: "err", 2: "warn", 3: "warn"}.get(sev, "dim")
        labs = self.events
        for i in range(len(labs) - 1, 0, -1):
            labs[i].setText(labs[i - 1].text())
            theme.set_peran(labs[i], labs[i - 1].property("peran") or "faint")
        labs[0].setText(text[:72])
        theme.set_peran(labs[0], peran)

    # ================================================================== #
    # util
    # ================================================================== #
    def _gt_at(self, t, tol=0.25):
        best, bd = None, tol
        for s in reversed(self.gt_hist):
            d = abs(s[0] - t)
            if d < bd:
                best, bd = s, d
            if s[0] < t - tol:
                break
        return best

    def _acc_len(self, key, n, e, track):
        if len(track) >= 2:
            (n0, e0) = track[-2]
            step = math.hypot(n - n0, e - e0)
            if step > 1e-4:
                self.lens[key] += step

    def _refresh_plot_stats(self):
        g, v = self.lens["gt"], self.lens["vio"]
        rmse = math.sqrt(sum(x * x for x in self.errs) / len(self.errs)) if self.errs else None
        self.plot.set_stats(
            gt_len=f"{g:.1f} m",
            vio_len=f"{v:.1f} m",
            err=f"{self.last_vio[4]*100:.0f} cm" if self.last_vio else None,
            rmse=f"{rmse*100:.0f} cm" if rmse is not None else None,
            scale=f"{v/g:.3f}" if g > 1.0 and v > 0 else None,
        )
        self.r_scale.set_value(v / g if g > 1.0 else None)
        self.plot.update()

        sim = "SIM ok" if self.rates.get("gt", 0) > 5 else "SIM idle"
        vins = f"VINS {self.rates.get('vins', 0):.1f} Hz" if self.rates.get("vins", 0) > 1 else "VINS idle"
        mav = f"MAV {self.mav.get('mode', '--')} {'ARMED' if self.mav.get('armed') else 'disarmed'}" \
            if self.mav else "MAV not connected"
        v = self.last_vio
        pos = f"N {v[1]:+.2f} E {v[2]:+.2f} D {v[3]:+.2f}" if v else "pos --"
        up = self.rates.get("uptime", 0)
        # Lewat _set_status(): deretan pipa lama dipecah jadi KELOMPOK BERSEMEN yang
        # membungkus (rancangan §9) — bukan satu baris panjang yang dipotong di jendela sempit.
        self._set_status(
            f"{sim}   |   {vins}   |   {mav}   |   VIO {pos}   |   "
            f"path GT {self.lens['gt']:.1f} m / VIO {self.lens['vio']:.1f} m   |   "
            f"uptime {int(up // 60):02d}:{int(up % 60):02d}   |   DDS SHM 16 MB active")

    def _poll_embed(self):
        if self.embed_mode == "off":
            # panel kiri = kamera depan pilot (mode mirror dihapus 23 Sep 2026;
            # opsi --embed reparent tetap ada sebagai opt-in berisiko)
            self.chip_sim.set_state(theme.WARN, "sim window")
            if not self._hint_prio:
                # jangan timpa pesan penting (deadman / TIDAK-ARM / joy basi)
                self.p_sim.set_hint('front camera pilot · active in MANUAL mode (the sim is watched directly in its own '
'window)')
            return
        if self.sim is None:
            return
        was_empty = self.sim.window is None
        ok = self.sim.attach_once() if was_empty else self.sim.check_alive()
        if was_empty and ok:
            # jaga-jaga: kalau reparent membuat jendela utama kolaps, lepas kembali
            QtWidgets.QApplication.processEvents()
            if not self.sim.embed_sane(int(self.winId())):
                self.sim.detach()
                # pulihkan geometri jendela (beberapa display tidak mengembalikannya sendiri)
                try:
                    self.showNormal()
                    self.resize(self._orig_size)
                    QtWidgets.QApplication.processEvents()
                except Exception:
                    pass
                self.p_sim.set_hint('embed failed · sim stays in a separate window')
                self.chip_sim.set_state(theme.WARN, 'embed failed')
                return
            self.p_sim.set_hint(f"{self.sim.container.width()}×{self.sim.container.height()} · embedded")
        self.chip_sim.set_state(theme.OK if ok else theme.WARN, "window" if ok else "searching")

    def _take_shot(self):
        """Simpan screenshot jendela dashboard (fallback: area root display)."""
        scr = QtWidgets.QApplication.primaryScreen()
        g = self.geometry()
        pix = scr.grabWindow(int(self.winId()))
        if pix.isNull() or pix.width() < 200:
            pix = scr.grabWindow(0, g.x(), g.y(), g.width(), g.height())
        ok = pix.save(self.args.shot)
        print(f"screenshot {'saved' if ok else 'FAILED'}: {self.args.shot} ({pix.width()}x{pix.height()})")
        # tutup lewat closeEvent (di situ jendela simulator dilepas dulu) supaya
        # simulator tidak kehilangan permukaan OpenGL-nya
        self.close()
        QtCore.QTimer.singleShot(800, QtWidgets.QApplication.quit)

    def closeEvent(self, ev):
        try:                       # ingat geometri + preset untuk sesi berikutnya
            self.settings.setValue("geometry", self.saveGeometry())
            self.settings.setValue("preset", getattr(self, "_preset", self.PRESET_BAWAAN))
        except Exception:
            pass
        # lepas jendela simulator dari dashboard SEBELUM jendela Qt hancur,
        # kalau tidak simulator ikut kehilangan permukaan OpenGL-nya
        try:
            if self.sim is not None:
                self.sim.detach()
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass
        try:                       # hentikan thread unduh ubin peta (kalau tidak: proses abort)
            if getattr(self, "peta", None) is not None:
                self.peta.hentikan()
        except Exception:
            pass
        try:                       # misi selalu dihentikan saat GUI ditutup
            if self.pm.is_running('lawnmower'):
                self.pm.stop('lawnmower')
            # selesaikan log run secara sinkron (logger -> summary.txt, lalu analisis
            # Tahap B) SEBELUM stack dimatikan, supaya isi run tetap lengkap
            if getattr(self, '_mission_ran', False):
                self._finalize_mission_logs(blocking=True)
            if not self.args.keep_stack:
                # komponen `sfm` DIBIARKAN hidup: mozaik bisa butuh menit-an dan kalau
                # dibunuh di tengah, petanya tidak pernah muncul (26 Sep). Keluarannya
                # ditulis ke berkas, jadi tidak macet setelah GUI tutup.
                if self.pm.is_running("sfm"):
                    print('[gui] SFM is still building the mosaic — left running to completion (the result '
"appears in the run folder's sfm/ part)", flush=True)
                self.pm.stop_all(kecuali=("sfm",))
        except Exception:
            pass
        if self.ros:
            self.ros.stop()
        if self.mavbridge:
            self.mavbridge.stop()
        ev.accept()


def build_parser():
    """Parser argumen GUI — dipisah supaya uji offscreen (uji_gui_sfm.py) bisa
    memakai default yang sama tanpa menjalankan seluruh main()."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--seabed-z", type=float, default=5.3,
                    help='base depth (m NED) — FALLBACK only; the main AGL comes from /rov/altitude (fusion)')
    # batas mekanis kamera depan — sinkron dengan scenarios/bluerov2.scn +
    # config/bluerov2/mission_params.yaml (blok rov_cam). Lihat catatan di sana.
    ap.add_argument("--pan-limit", type=float, default=1.5708,
                    help='front camera pan limit (rad, ±90°)')
    ap.add_argument("--tilt-limit", type=float, default=1.0472,
                    help='front camera tilt limit (rad, ±60°)')
    ap.add_argument("--cam-step", type=float, default=0.05,
                    help='pan/tilt button step (rad)')
    ap.add_argument("--no-ros", action="store_true")
    ap.add_argument("--no-mav", action="store_true")
    ap.add_argument("--mavlink", default="auto",
                    help="endpoint MAVLink: auto | udpin:0.0.0.0:14550 | tcp:127.0.0.1:5760")
    ap.add_argument("--sfm-image", default="",
                    help='orthomosaic PNG for the SFM · MAP RESULT panel. DEFAULT EMPTY (owner request 25 Sep: '
"the mosaic window starts empty). Without this flag the panel waits for THIS run's "
'result: <run>/sfm/mosaic.png, created automatically by Progress/tools/sfm_auto.py '
'after the mission finishes. Set this flag only if you deliberately want to look at an '
'old map.')
    ap.add_argument("--capture-preview", action="store_true",
                    help='show a preview of the 1920x1200 RGB capture photos')
    ap.add_argument("--shot", help='save a screenshot to this path then exit')
    ap.add_argument("--shot-after", type=float, default=10.0)
    ap.add_argument("--no-embed", action="store_true",
                    help='do not embed the simulator window (for display/design testing)')
    ap.add_argument("--embed", choices=["auto", "reparent", "off"], default="auto",
                    help='auto: embed if a window manager is present; reparent: force embed; off: never')
    ap.add_argument("--size", help='window size, e.g. 1400x860')
    ap.add_argument("--preset", choices=("mission-ops", "pilot-focus", "legacy", "mapping",
                                         "pilot", "diagnostik"),
                    help='initial panel preset (default: mapping — the same layout for all modes)')
    ap.add_argument("--no-autostart", action="store_true",
                    help='do not open the stack automatically; start manually from the STACK panel')
    ap.add_argument("--keep-stack", action="store_true",
                    help='do not stop the stack when the GUI closes (the mission is still stopped)')
    ap.add_argument("--log-dir", default=None,
                    help='run folder for stack logs + data logger (default: RUN '
'SIMULASI/RUN_<DD>_<MONTH>_<HHMM>)')
    ap.add_argument("--autostart-mission", action="store_true",
                    help='toggle START AUTONOMOUS as soon as the stack is ready')
    ap.add_argument("--debug-stack", action="store_true",
                    help='print the stack gating state to stdout every second')
    return ap


def main():
    args = build_parser().parse_args()

    # Setiap run dapat foldernya sendiri di arsip RUN SIMULASI (format RUN_<DD>_<BULAN>_<HHMM>).
    # Layout datar: log komponen (sim/sitl/vins/logger/lawnmower.log) + data logger Tahap B
    # (gt/vins/imu/depth.csv, frames/, summary.txt, aligned.csv, trajektori.png) di folder yang
    # sama — identik dengan isi run jalur manual (RUN SIMULASI/RUN_21_SEPTEMBER_1354).
    if not args.log_dir:
        stamp = datetime.now().strftime('RUN_%d_%B_%H%M').upper()
        args.log_dir = os.path.join(RUN_ARCHIVE, stamp)
    os.makedirs(args.log_dir, exist_ok=True)
    run_dir = os.path.normpath(args.log_dir)
    try:                                  # jejak singkat isi run
        with open(os.path.join(run_dir, 'run_info.txt'), 'a', encoding='utf-8') as fh:
            fh.write(f"{datetime.now().astimezone().strftime('%d %b %Y %H:%M:%S %Z')} "
                     f"args: {' '.join(sys.argv[1:]) or '(no arguments)'}\n")
        rm = os.path.join(run_dir, 'README.md')
        if not os.path.exists(rm):
            with open(rm, 'w', encoding='utf-8') as fh:
                fh.write(f"# Run GUI — {os.path.basename(run_dir)}\n\n"
                         f"- Start: {datetime.now().astimezone().strftime('%d %b %Y %H:%M:%S %Z')}\n"
                         "- How to run: `rovgui` (the dashboard starts sim → SITL → VINS → logger itself), "
                         "START AUTONOMOUS for the mission.\n"
                         "- Contents: `sim/sitl/vins/logger/lawnmower.log`, Stage B data logger "
                         "(`gt/vins/imu/depth.csv`, `frames/`, `summary.txt`), "
                         "`aligned.csv` + `trajektori.png` + `analysis.log` (post-mission analysis), "
                         "`run_info.txt` — the same as a manual-path run (RUN_.../RUN_1354).\n\n"
                         "Put the run results here (waypoints reached, ATE, notes).\n")
    except Exception:
        pass
    print(f'log run: {args.log_dir}')

    QtWidgets.QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, False)
    app = QtWidgets.QApplication(sys.argv)
    # Rupa huruf: muat varian turunan (digit tabular) SEBELUM jendela dibuat, lalu pilih
    # palet tersimpan (QSettings) — tema berjalan tanpa restart, jadi stylesheet di sini
    # hanya nilai awal. Palet bisa dipaksa lewat env ROVGUI_THEME (dipakai proses anak).
    theme.muat_font()
    nilai = QtCore.QSettings("BRIN", "ROVMissionConsole").value("appearance")
    if nilai:
        theme.aktif(str(nilai))
    theme.apply(theme.aktif(), app)
    win = Dashboard(args)
    if args.size:
        w, _, h = args.size.partition("x")
        win._orig_size = QtCore.QSize(int(w), int(h))
        win.resize(win._orig_size)
    else:
        # ingat posisi/ukuran jendela seperti aplikasi desktop biasa
        g = win.settings.value("geometry")
        if g is not None:
            try:
                win.restoreGeometry(g)
            except Exception:
                pass
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
