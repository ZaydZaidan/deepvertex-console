"""Panel STACK + tombol START AUTONOMOUS + autostart untuk ROV Mission Console.

Ditulis sebagai mixin supaya rov_dashboard.py hanya perlu sisipan kecil.
Semua proses dijalankan lewat ProcManager (procman.py).
"""
import os
import subprocess
import threading
import time
from collections import deque

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt

import theme
import widgets as W
from procman import COMPONENTS, ORDER, PANEL_EXTRA
import re

# analisis Tahap B (aligned.csv + trajektori.png) — dijalankan setelah misi berhenti
# supaya isi folder run GUI sama dengan run jalur manual (RUN_.../RUN_1354).
ANALYZE = "/home/zayd/Project BRIN VIO/Progress/tahapB/analyze_stage_b.py"
# evaluasi fusi altitude (Tahap C, §12.4.1) — bila altitude.csv ada di run
ANALYZE_ALT = "/home/zayd/Project BRIN VIO/Progress/tahapB/analyze_altitude_fusion.py"

# HASIL YANG MILIK SATU MISI (bukan sesi). Sebelum misi berikutnya dimulai, isinya dipindah ke
# <run>/misi_XX/ (lihat `_putar_hasil_misi`): logger (log_stage_b.py `"w"`) dan relay mengulang
# berkasnya saat dijalankan ulang sehingga tanpa pemindahan ini data misi sebelumnya HILANG.
# Terukur di RUN_30_SEPTEMBER_2002: gt.csv misi-1 37.383 baris (374 s) → 13.076 baris (130 s)
# milik misi-2, imu.csv 7.677 → 927 baris, dan segmen S01 dievaluasi ulang dengan CSV kosong
# sehingga verdict per-segmennya palsu.
HASIL_MISI_BERKAS = ("gt.csv", "vins.csv", "imu.csv", "depth.csv", "altitude.csv", "summary.txt",
                     "aligned.csv", "trajektori.png", "analysis.log", "altitude.png")
HASIL_MISI_FOLDER = ("capture", "frames", "sfm", "dem")
# Folder yang harus TETAP ADA sesudah pemindahan (komponen menulis ke situ; relay/logger
# membuatnya sendiri, tetapi folder kosong lebih ramah untuk pembaca berikutnya).
HASIL_MISI_FOLDER_KOSONG = ("capture", "frames")


# --------------------------------------------------------------------------- #
# Gaya tombol: sejak redesign, SELURUH gaya ada di theme.qss() (objectName +
# properti dinamis). Fungsi di bawah HANYA tinggal sebagai jalur kompatibilitas
# untuk pemanggil lama yang masih mengimpor BTN_QSS / AUTO_QSS.
# --------------------------------------------------------------------------- #
def btn_qss():
    """Gaya tombol kecil (kini dari theme.qss(); dipertahankan untuk pemanggil lama)."""
    return ("QPushButton { background: %s; color: %s; border: 1px solid %s;"
            " border-radius: 2px; padding: 2px 8px; font-size: 10px; }"
            % (theme.warna("raise_"), theme.warna("ink"), theme.warna("line_hi")))


def auto_qss():
    """Gaya tombol misi besar (kini dari theme.qss(); dipertahankan untuk pemanggil lama)."""
    return ("QPushButton { background: %s; color: %s; border: 1px solid %s;"
            " border-radius: 2px; padding: 10px 14px; font-size: 12px;"
            " font-weight: 600; letter-spacing: 2px; }"
            % (theme.warna("raise_"), theme.warna("ink_dim"), theme.warna("line_hi")))


def __getattr__(nama):
    """BTN_QSS / AUTO_QSS tetap bisa diimpor (dihitung dari palet aktif saat diakses)."""
    if nama == "BTN_QSS":
        return btn_qss()
    if nama == "AUTO_QSS":
        return auto_qss()
    raise AttributeError("stackui has no attribute %r" % nama)


def _esc(teks):
    """Amankan teks log yang ditampilkan sebagai HTML (baris log bisa memuat <>&)."""
    return (str(teks).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class StackUI:
    """Mixin: butuh self.pm, self.args, self.rates, self.mav, self.status dari Dashboard."""

    # ------------------------------------------------------------------ #
    # THREAD: semua sentuhan widget dari thread pekerja dititipkan ke thread GUI
    # (kejadian 28 Sep 2026: `rovgui` mencetak "Cannot create children for a parent that is
    #  in a different thread" lalu SEGFAULT — sumbernya UI disentuh dari thread ROS/ProcManager)
    # ------------------------------------------------------------------ #
    def _di_gui(self):                                        # pragma: no cover - fallback
        return True

    def _ui_aman(self, aksi):
        """Jalankan `aksi` di thread GUI bila objek induk menyediakannya."""
        ui = getattr(self, "_ui", None)
        return aksi() if ui is None else ui(aksi)

    def _perlu_titip(self):
        """True bila kita TIDAK sedang di thread GUI dan harus menitipkan pekerjaan."""
        return not bool(getattr(self, "_di_gui", lambda: True)())

    # ------------------------------------------------------------------ #
    # Bangun UI
    # ------------------------------------------------------------------ #
    def _panel_stack(self):
        p = W.Panel("STACK · PROCESSES", "sim → sitl → vins → mission", domain="session")
        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(6)
        self.btn_start_all = QtWidgets.QPushButton("START ALL")
        self.btn_stop_all = QtWidgets.QPushButton("STOP ALL")
        for b in (self.btn_start_all, self.btn_stop_all):
            b.setMinimumHeight(22)
            b.setCursor(Qt.PointingHandCursor)
        self.btn_start_all.clicked.connect(self._start_all_clicked)
        self.btn_stop_all.clicked.connect(self._stop_all_clicked)
        bar.addWidget(self.btn_start_all)
        bar.addWidget(self.btn_stop_all)
        bar.addStretch(1)
        p.body_layout.addLayout(bar)

        self.stack_rows = {}
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(6)
        # spasi 0 + tombol #BtnRow yang ringkas: daftar 10 komponen tetap muat di rel kanan
        # TANPA scrollbar di jendela pemilik 1620x978 (uji R9_kanan_1620x978).
        grid.setVerticalSpacing(0)
        grid.setColumnStretch(1, 1)
        # ORDER = komponen stack (autostart). PANEL_EXTRA = komponen yang hanya dinyalakan
        # dari menu (mis. Auto-PID) tetapi tetap punya baris di sini supaya bisa dipantau.
        for row, name in enumerate(list(ORDER) + [n for n in PANEL_EXTRA
                                                  if n in COMPONENTS and n not in ORDER]):
            dot = QtWidgets.QLabel("●")
            dot.setObjectName("ProcDot")
            dot.setMaximumHeight(14)
            theme.set_peran(dot, "faint")
            dot.setFixedWidth(10)
            lab = QtWidgets.QLabel(COMPONENTS[name]["label"])
            lab.setObjectName("ProcName")
            state = QtWidgets.QLabel("off")
            state.setObjectName("ProcState")
            theme.set_peran(state, "faint")
            state.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            btn = QtWidgets.QPushButton("▶")
            btn.setObjectName("BtnRow")          # ringkas, lihat theme.qss
            btn.setFixedSize(26, 14)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _, n=name: self._toggle_component(n))
            grid.addWidget(dot, row, 0)
            grid.addWidget(lab, row, 1)
            grid.addWidget(state, row, 2)
            grid.addWidget(btn, row, 3)
            self.stack_rows[name] = {"dot": dot, "state": state, "btn": btn}
        p.body_layout.addLayout(grid)
        return p

    # ------------------------------------------------------------------ LOG
    def _panel_log(self):
        """Panel STACK LOG — sekarang di KOLOM TENGAH (lebar ~900 px) sesuai rancangan.

        Baris log di kolom kanan selebar 12 % selalu terpotong; di sini baris terpanjang
        pun terbaca utuh. Isi dibungkus QScrollArea supaya baris yang MEMBUNGKUS tidak
        pernah keluar dari kotak panel (aturan keras: tidak ada yang terpotong).
        """
        p = W.Panel("Stack log", "last lines · Ctrl+L", domain="session")
        self._log_lines = deque(maxlen=400)      # (ts, komponen, isi, kategori)
        self._log_filter = "all"
        # Saringan + tombol lipat di KEPALA panel (mockup), bukan baris tersendiri di badan:
        # hemat tinggi dan sebaris dengan judul.
        kontrol = QtWidgets.QWidget()
        kl = QtWidgets.QHBoxLayout(kontrol)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(6)
        from rov_dashboard import Segmen          # impor tertunda (hindari impor berputar)
        self.seg_log = Segmen([("all", "ALL", "show every line"),
                               ("warn", "WARN",
                                "only warnings/errors (fail, timeout, abort, fallback)"),
                               ("mission", "MISSION",
                                "only mission-related lines (controller, logger, mode, SFM)")])
        self.seg_log.set_dipilih("all")
        self.seg_log.dipilih.connect(self._set_log_filter)
        kl.addWidget(self.seg_log)
        self.btn_log_buka = QtWidgets.QPushButton("▾")
        self.btn_log_buka.setObjectName("BtnTiny")
        self.btn_log_buka.setToolTip("collapse / expand the log rows (the panel itself stays)")
        self.btn_log_buka.setCursor(Qt.PointingHandCursor)
        self.btn_log_buka.clicked.connect(self._toggle_log_body)
        kl.addWidget(self.btn_log_buka)
        p.sisip_kontrol(kontrol)

        self.log_labels = []
        self._log_rows = QtWidgets.QWidget()
        self._log_rows_lay = QtWidgets.QVBoxLayout(self._log_rows)
        self._log_rows_lay.setContentsMargins(0, 0, 0, 0)
        self._log_rows_lay.setSpacing(2)
        for i in range(8):
            lab = QtWidgets.QLabel("—" if i == 0 else "")
            lab.setObjectName("LogLine")
            lab.setTextFormat(Qt.RichText)
            lab.setWordWrap(True)
            self._log_rows_lay.addWidget(lab)
            self.log_labels.append(lab)
        self._log_rows_lay.addStretch(1)
        self._log_scroll = QtWidgets.QScrollArea()
        self._log_scroll.setWidgetResizable(True)
        self._log_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._log_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._log_scroll.setWidget(self._log_rows)
        p.body_layout.addWidget(self._log_scroll, 1)
        return p

    def _build_autonomous(self):
        """Panel MISSION CONTROL: dua tombol misi + AREA ± m + PLANNER + estimasi durasi.

        Fitur PLANNER, baris "Lines · waypoints" dan "Est. mission time" adalah FITUR BARU
        (tidak ada di GUI lama) → ditandai "+ NEW" dan disembunyikan di preset Legacy.
        """
        box = W.Panel("MISSION CONTROL", "applies to the NEXT mission", domain="session")
        self._chip_misi_dibuat = False
        self.p_autonomous = box
        lay = box.body_layout
        self.btn_auto = QtWidgets.QPushButton("START AUTONOMOUS")
        self.btn_auto.setCheckable(True)
        self.btn_auto.setEnabled(False)
        self.btn_auto.setMinimumHeight(34)
        self.btn_auto.setCursor(Qt.PointingHandCursor)
        self.btn_auto.setObjectName("BtnPrimary")
        self.btn_auto.setToolTip("waiting for simulator + ArduSub SITL")
        # (dipindah ke bawah: mockup menaruh START MANUAL PILOT di atas)
        # mode PILOT: gamepad + kamera depan saja (stereo VIO dimatikan oleh mode
        # manager). Eksklusif dengan START AUTONOMOUS — dua pengirim RC override
        # di port 5762 akan saling menimpa (last-writer-wins).
        self.btn_manual = QtWidgets.QPushButton("START MANUAL PILOT")
        self.btn_manual.setCheckable(True)
        self.btn_manual.setEnabled(False)
        self.btn_manual.setMinimumHeight(28)
        self.btn_manual.setCursor(Qt.PointingHandCursor)
        self.btn_manual.setObjectName("BtnGhost")
        self.btn_manual.setToolTip("gamepad piloting + front camera (mode MANUAL); "
                                   "waiting for simulator + SITL")
        # MAPPING MANUAL (permintaan pemilik 2 Okt 2026): "saya mau buat satu mode lagi
        # selain autonomous yaitu mapping tapi manual. cara kerja capturingnya sama,
        # cuman yang gerakin kontrolnya pilot. algoritma hanya membantu untuk ngeguide
        # pathnya" — jadi: pilot memegang stick (deadman L1), capture relay + logger
        # jalan, dan node guide menerbitkan rencana jalur + kotak area untuk digambar di
        # peta. TIDAK ada RC dari algoritma (beda dari autonomous).
        self.btn_manual_map = QtWidgets.QPushButton("START MAPPING MANUAL")
        self.btn_manual_map.setCheckable(True)
        self.btn_manual_map.setEnabled(False)
        self.btn_manual_map.setMinimumHeight(24)
        self.btn_manual_map.setCursor(Qt.PointingHandCursor)
        self.btn_manual_map.setObjectName("BtnGhost")
        self.btn_manual_map.setToolTip("pilot flies (gamepad, deadman L1) while the mapping "
                                       "session runs: capture + planned-path guide; "
                                       "waiting for simulator + SITL")
        lay.addWidget(self.btn_manual)
        lay.addWidget(self.btn_manual_map)
        lay.addWidget(self.btn_auto)          # mockup: tombol misi utama di BAWAH
        # AREA MISI (permintaan pemilik 27 Sep): "adain input mau mapping areanya berapa,
        # jangan selalu 10x10 — biar tesnya lebih cepat." Nilai = SETENGAH-SISI (m);
        # 5,0 = 10x10 m (bawaan lama). Berlaku untuk misi BERIKUTNYA.
        baris_area = QtWidgets.QHBoxLayout()
        baris_area.setSpacing(6)
        lbl_area = QtWidgets.QLabel("AREA ± m")
        lbl_area.setObjectName("MicroLabel")
        theme.set_peran(lbl_area, "dim")
        self.sp_area = QtWidgets.QDoubleSpinBox()
        self.sp_area.setRange(0.5, 25.0)
        self.sp_area.setSingleStep(0.5)
        self.sp_area.setDecimals(1)
        self.sp_area.setValue(5.0)
        self.sp_area.setMinimumWidth(72)
        self.sp_area.setToolTip("Half-side of survey area (m). 5.0 = 10x10 m (default). "
                                "Small (e.g. 2.0 = 4x4 m) for a quick test.")
        self.sp_area.valueChanged.connect(self._set_area)
        self.lbl_area = QtWidgets.QLabel("10.0x10.0 m")
        self.lbl_area.setObjectName("Faint")
        theme.set_peran(self.lbl_area, "faint")
        baris_area.addWidget(lbl_area)
        baris_area.addWidget(self.sp_area)
        baris_area.addWidget(self.lbl_area)
        baris_area.addStretch(1)
        lay.addLayout(baris_area)

        # ---- FITUR BARU (+ NEW): pemilih PLANNER + estimasi durasi misi ----
        self.box_baru = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(self.box_baru)
        bl.setContentsMargins(0, 2, 0, 0)
        bl.setSpacing(4)
        # Baris PLANNER: MEMBUNGKUS (FlowBox) supaya tiga tombolnya tidak memaksa rel melebar
        # di layar sempit — di rel 260 px (laptop 1280) ia turun ke baris kedua.
        from rov_dashboard import FlowBox
        baris_plan = FlowBox(spacing=6)
        lbl_plan = QtWidgets.QLabel("PLANNER")
        lbl_plan.setObjectName("MicroLabel")
        theme.set_peran(lbl_plan, "dim")
        baris_plan.tambah(lbl_plan)
        self.plan_btns = {}
        for mode, teks, tip in (("fixed_wp", "FIXED WP",
                                 "fixed depth, line spacing 1.0 m (default)"),
                                ("fixed_wp_lama", "LEGACY",
                                 "old WP motion (omni, no turn-around) — fallback if the free "
                                 "mode misbehaves"),
                                ("fixed_wp_dense", "DENSE",
                                 "line spacing 0.55 m → map overlap ≥3, mission ~1.67x longer")):
            b = QtWidgets.QPushButton(teks)
            b.setObjectName("SegBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.clicked.connect(lambda _, m=mode: self._set_auto_mode_gui(m))
            baris_plan.tambah(b)
            self.plan_btns[mode] = b
        bl.addWidget(baris_plan)

        baris_est = QtWidgets.QHBoxLayout()
        baris_est.setSpacing(6)
        self.lbl_lines = QtWidgets.QLabel("Lines · waypoints")
        self.lbl_lines.setObjectName("RowLabel")
        theme.set_peran(self.lbl_lines, "dim")
        self.lbl_lines_val = QtWidgets.QLabel("—")
        self.lbl_lines_val.setObjectName("RowValue")
        theme.set_peran(self.lbl_lines_val, "ink")
        self.lbl_lines_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # chip misi ditempelkan di baris "Lines · waypoints" (satu baris, hemat tinggi rel)
        self.chip_misi = W.Chip("mission")
        self.chip_misi.set_state("ink_faint", "idle")
        baris_est.addWidget(self.lbl_lines)
        baris_est.addWidget(self.chip_misi)
        baris_est.addStretch(1)
        baris_est.addWidget(self.lbl_lines_val)
        bl.addLayout(baris_est)

        baris_waktu = QtWidgets.QHBoxLayout()
        baris_waktu.setSpacing(6)
        self.lbl_est = QtWidgets.QLabel("Est. mission time")
        self.lbl_est.setObjectName("RowLabel")
        theme.set_peran(self.lbl_est, "dim")
        self.lbl_baru = QtWidgets.QLabel("+ NEW")
        self.lbl_baru.setObjectName("Faint")
        theme.set_peran(self.lbl_baru, "brass")
        self.lbl_baru.setToolTip("new feature — the old GUI had no mission time estimate")
        self.lbl_est_val = QtWidgets.QLabel("—")
        self.lbl_est_val.setStyleSheet(theme.mono(15, 600))
        theme.set_peran(self.lbl_est_val, "accent")
        self.lbl_est_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        baris_waktu.addWidget(self.lbl_est)
        baris_waktu.addWidget(self.lbl_baru)
        baris_waktu.addStretch(1)
        baris_waktu.addWidget(self.lbl_est_val)
        bl.addLayout(baris_waktu)

        lay.addWidget(self.box_baru)
        self._refresh_estimasi()
        return box

    # ------------------------------------------------------------------ ESTIMASI
    # Nama mode menu (<->) nama planner di panel: menu memakai (mode, baris) seperti
    # _set_auto_mode, panel memakai satu nama mode tiap tombol.
    def _set_auto_mode_gui(self, mode):
        """PLANNER di panel MISSION CONTROL -> jalur yang SAMA dengan menu Operator."""
        baris = {"fixed_wp": 1.0, "fixed_wp_dense": 0.55, "fixed_wp_lama": 1.0}.get(mode, 1.0)
        self._set_auto_mode("fixed_wp" if mode == "fixed_wp_dense" else mode, baris)
        # _set_auto_mode mengabungkan mode+kerapatan; simpan nama tombol yang ditekan
        self._planner_gui = mode
        self._refresh_estimasi()

    def _mode_planner(self):
        """Mode planner yang sedang berlaku untuk estimasi (nama tombol panel)."""
        mode, baris = getattr(self, "_auto_mode", ("fixed_wp", 1.0))
        if mode in ("fixed_wp_lama", "legacy", "omni"):
            return "fixed_wp_lama"
        return "fixed_wp_dense" if abs(float(baris) - 0.55) < 0.05 else "fixed_wp"

    def _refresh_estimasi(self):
        """Hitung ulang estimasi SAAT AREA/PLANNER berubah (bukan saat misi mulai)."""
        try:
            import estimasi_misi as EM
            half = float(self.sp_area.value()) if hasattr(self, "sp_area") else 5.0
            mode = self._mode_planner()
            baris, waktu = EM.ringkas(half, mode)
            if hasattr(self, "lbl_lines_val"):
                self.lbl_lines_val.setText(baris)
                self.lbl_est_val.setText(waktu)
                e = EM.estimasi(half, mode)
                self.lbl_est_val.setToolTip(
                    "estimate: %d lines x (%.1f m / %.1f m/s + %.1f s turn) = %.0f s"
                    % (e["baris"], e["sisi"], EM.V_MPS, EM.T_BELOK, e["detik"]))
            for m, b in getattr(self, "plan_btns", {}).items():
                b.setChecked(m == mode)
        except Exception as exc:                                   # noqa: BLE001
            try:
                self._log("estimator: failed (%r)" % (exc,))
            except Exception:                                     # noqa: BLE001
                pass

    # ------------------------------------------------------------------ #
    # Aksi
    # ------------------------------------------------------------------ #
    def _start_all_clicked(self):
        threading.Thread(target=self._start_sequence, daemon=True).start()

    def _stop_all_clicked(self):
        if self.btn_auto.isChecked():
            self.btn_auto.setChecked(False)      # memicu penghentian misi
        self.pm.stop_all()

    def _toggle_component(self, name):
        if self.pm.is_running(name):
            if name == "lawnmower" and self.btn_auto.isChecked():
                self.btn_auto.setChecked(False)
            if name in ("teleop", "joy") and getattr(self, "btn_manual", None) is not None \
                    and self.btn_manual.isChecked():
                self.btn_manual.setChecked(False)
            self.pm.stop(name)
            return
        deps = {"mode": ["sim"], "sitl": ["sim"], "vins": ["sim"], "logger": ["sim"],
                "lawnmower": ["sim", "sitl"], "joy": ["sim"], "teleop": ["sim", "sitl"]
                }.get(name, [])
        for d in deps:
            if not self.pm.is_running(d):
                self._log(f"cannot start {name}: {d} not running")
                return
        threading.Thread(target=self.pm.start, args=(name,), daemon=True).start()

    # ------------------------------------------------------------------ #
    # Mode pilot (manual) vs autonomous (mapping) — spec §8
    # ------------------------------------------------------------------ #
    def _request_mode(self, mode):
        """Minta mode ke rov_mode_manager (pemilik kebijakan kamera)."""
        ros = getattr(self, "ros", None)
        if ros is None:
            self._log(f"mode {mode.upper()} cannot be requested: ROS bridge not running")
            return False
        try:
            ros.set_mode(mode)
            self._log(f"mode request → {mode.upper()} (camera set by mode manager)")
            return True
        except Exception as exc:
            self._log(f"failed to request mode {mode}: {exc}")
            return False

    def _terapkan_profil(self, nama):
        """Terapkan PROFIL operasi (26 Sep) — komponen + set kamera per mode.

        Prinsip yang terukur: setiap komponen/kamera yang hidup memakan anggaran
        render (tiap kamera = satu pass scene) dan CPU. Jadi: MISSION memakai stereo
        tanpa kamera depan; MANUAL memakai kamera depan saja (profil ringan) tanpa
        VINS; profil "MANUAL + VIO" mengembalikan kebijakan lama (3 kamera + VINS).
        Semua di satu tempat supaya tidak ada dua kebijakan yang saling bertabrakan.
        """
        from procman import profil, MANUAL_PROFIL
        p = profil(nama)
        man_cam = p["kamera"]
        # Kamera untuk MODE manual/fallback SELALU dari PROFIL MANUAL yang sedang dipilih —
        # bukan dari profil yang baru diterapkan. Terukur di RUN_1449: saat START AUTONOMOUS,
        # tabel berubah menjadi {manual: cam_left cam_right, ...} sehingga pilot kehilangan
        # kamera depan (dan profil manual jadi tak berarti).
        cam_manual = man_cam if nama == MANUAL_PROFIL else profil(MANUAL_PROFIL)["kamera"]
        # MAPPING MANUAL (2 Okt): kamera mode `mapping_manual` SELALU dari profil
        # mapping_manual (depan + stereo) — tidak pernah ikut profil manual, karena mode itu
        # selalu sesi pemetaan dan VINS butuh stereo.
        cam_mmap = profil("mapping_manual")["kamera"]
        # Tabel kamera mode manager diubah lewat TOPIK (bukan `ros2 param set`): nilai
        # tabel berisi spasi ("camera_front cam_left cam_right") dan CLI param
        # memperlakukannya sebagai YAML -> gagal senyap (terbukti 26 Sep: profil
        # "MANUAL + VIO" tidak mengembalikan stereo). Payload topik ini bebas spasi.
        payload = ("manual=%s;mapping=%s;mapping_manual=%s;fallback=%s"
                   % (cam_manual, profil("mapping")["kamera"], cam_mmap, cam_manual))
        try:
            subprocess.run(["ros2", "topic", "pub", "--once", "/rov/mode/kamera",
                            "std_msgs/String", "{data: '%s'}" % payload],
                           timeout=8, capture_output=True)
        except Exception as exc:                                     # noqa: BLE001
            self._log("profile: set camera table failed: %r" % (exc,))
        # komponen yang tidak diperlukan profil ini dimatikan (hemat CPU)
        for nm in ("vins", "logger"):
            if nm not in p["comp"] and self.pm.is_running(nm):
                self._log("profile %s: stopping %s (save CPU)" % (p["nama"], nm))
                self.pm.stop(nm)
        for nm in ("vins",):
            if nm in p["comp"] and not self.pm.is_running(nm):
                self._log("profile %s: starting %s" % (p["nama"], nm))
                self.pm.start(nm)
        # RELAY mengikuti KEBUTUHAN profil (28 Sep malam, disetujui pemilik).
        # Relay adalah SATU-SATUNYA sumber /rov/stereo/* — jadi tanpa relay, VINS tidak
        # menerima satu gambar pun. Terukur 28 Sep: profil "MANUAL + VIO" (bawaan) memasang
        # VINS tetapi relay tidak pernah ikut hidup, sehingga `vins.csv` hanya berisi header
        # (21 byte) di 9 run non-misi, sedangkan 2 run yang relay-nya hidup punya
        # 64.239 / 216.248 byte data. Relay hanya merepublish citra yang SUDAH dirender
        # kamera (tidak menambah pass scene), jadi menyalakannya saat VINS diminta tidak
        # menambah beban kamera; capture-nya sendiri tetap di-gate ke mode mapping.
        perlu_relay = self._relay_diperlukan(p["comp"])
        if perlu_relay and not self.pm.is_running("relay"):
            self._log("profile %s: starting relay (stereo source for VINS / altitude fusion)"
                      % p["nama"])
            self.pm.start("relay")
        elif not perlu_relay and self.pm.is_running("relay"):
            self._log("profile %s: stopping relay (no VINS in this profile)" % p["nama"])
            self.pm.stop("relay")
        self._log("PROFILE %s · camera: %s · required components: %s"
                  % (p["nama"], man_cam, ", ".join(p["comp"])))
        self._set_banner("Profile %s · camera: %s" % (p["nama"], man_cam), theme.ACCENT)

    def _relay_diperlukan(self, comp_profil=None):
        """True bila relay masih dibutuhkan — SATU aturan untuk profil DAN akhir misi.

        Relay adalah SATU-SATUNYA sumber `/rov/stereo/*` **dan** `/rov/imu/data` untuk VINS
        (catatan panjangnya ada di `_terapkan_profil`). Karena itu relay tidak boleh dimatikan
        selama VINS hidup: aliran IMU berhenti, dan saat relay hidup lagi VINS menerima lompatan
        stempel waktu —

            E... estimator.cpp:1463] Abnormal IMU timestamp jump detected: 103.876

        — lalu posenya meledak ke kilometer (RUN_30_SEPTEMBER_2002: VIO "bergerak sangat jauh"
        tepat saat misi autonomous KEDUA dimulai; 103,876 s = selang relay mati 20:08:45→20:10:27).
        """
        if self.pm.is_running("vins") or self.pm.is_running("lawnmower"):
            return True
        return bool(comp_profil) and "vins" in comp_profil

    def _putar_hasil_misi(self):
        """Pindahkan hasil misi SEBELUMNYA ke `<run>/misi_XX/` sebelum misi baru mengisi root.

        -> nama folder (mis. "misi_01") atau None bila tidak ada yang dipindah.

        Alasan: logger (`log_stage_b.py`) dan relay membuka CSV-nya dengan mode "w" saat
        dijalankan ULANG, jadi misi kedua di sesi yang sama menghapus data misi pertama. Hasil
        dipindah (bukan disalin) supaya root selalu = misi TERAKHIR (semua alat dan panel GUI
        membaca path root) dan `misi_XX/` tetap berupa "run kecil" yang sah untuk alat analisis
        Tahap B/C, SFM, dan DEM (gt/vins/imu/depth/altitude + capture/ + sfm/ + dem/).
        """
        import shutil
        run = getattr(getattr(self, "pm", None), "log_dir", "") or ""
        if not run or not os.path.isdir(run):
            return None
        # Jangan memindahkan berkas selagi analisis pasca-misi masih menulis ke root (pemilik bisa
        # langsung menekan START AUTONOMOUS lagi). Tunggu maksimal 10 s; berkas yang tetap dipindah
        # masih terbaca oleh proses yang sudah memegang handle-nya (POSIX).
        t0 = time.time()
        while getattr(self, "_finalizing", False) and time.time() - t0 < 10.0:
            time.sleep(0.2)
        ada = any(os.path.exists(os.path.join(run, nm)) for nm in HASIL_MISI_BERKAS)
        if not ada:
            for nm in HASIL_MISI_FOLDER:
                p = os.path.join(run, nm)
                if os.path.isdir(p) and os.listdir(p):
                    ada = True
                    break
        if not ada:
            return None                        # misi pertama (atau misi sebelumnya tanpa data)
        n = 1
        while os.path.exists(os.path.join(run, "misi_%02d" % n)):
            n += 1
        tujuan = os.path.join(run, "misi_%02d" % n)
        try:
            os.makedirs(tujuan, exist_ok=True)
            for nm in HASIL_MISI_BERKAS + HASIL_MISI_FOLDER:
                p = os.path.join(run, nm)
                if os.path.exists(p):
                    shutil.move(p, os.path.join(tujuan, nm))
            for nm in HASIL_MISI_FOLDER_KOSONG:
                os.makedirs(os.path.join(run, nm), exist_ok=True)
            with open(os.path.join(tujuan, "info.txt"), "w") as f:
                f.write("results of the PREVIOUS mission (moved by %s)\n"
                        "segment  : %s\n"
                        "why      : the next mission rewrites gt/vins/imu/depth/altitude.csv, "
                        "capture/, frames/, sfm/ and dem/ inside the run folder\n"
                        "note     : the run ROOT always holds the NEWEST mission; this folder keeps "
                        "the previous mission's whole material (CSVs, captures, mosaic, DEM) and "
                        "can be analysed directly, e.g.\n"
                        "           python3 <tahapB>/analyze_stage_b.py --out %s\n"
                        % (self.__class__.__name__, getattr(self, "_segmen_aktif", "-"), tujuan))
        except OSError as exc:
            self._log("previous mission results could NOT be moved (%r) — files stay in the run "
                      "root and WILL be overwritten by this mission" % (exc,))
            return None
        self._log("previous mission results moved to %s — root = newest mission "
                  "(logger/relay would otherwise overwrite the CSVs of the previous mission)"
                  % os.path.basename(tujuan))
        return tujuan

    def _on_manual_toggled(self, checked):
        if checked:
            if not (self.pm.is_running("sim") and self.pm.is_running("sitl")):
                self._log("manual mode cancelled: simulator/SITL not running")
                self.btn_manual.setChecked(False)
                return
            # eksklusif: hentikan misi autonomous dulu (dua pengirim RC = tabrakan)
            if self.pm.is_running("lawnmower"):
                self._log("manual mode: stopping lawnmower first (exclusive RC)")
                if self.btn_auto.isChecked():
                    self.btn_auto.setChecked(False)
                else:
                    self.pm.stop("lawnmower")
            # eksklusif juga dengan MAPPING MANUAL (teleop yang sama, RC 5762 yang sama)
            if (getattr(self, "btn_manual_map", None) is not None
                    and self.btn_manual_map.isChecked()):
                self._log("manual mode: stopping MAPPING MANUAL first (exclusive RC)")
                self.btn_manual_map.setChecked(False)
            # MANUAL lama BUKAN sesi pemetaan (permintaan pemilik 2 Okt: "untuk yang manual
            # lama, itu bukan buat mapping jadi gausah") -> tidak ada garis petunjuk; rencana
            # misi sebelumnya dibuang supaya tidak terbaca sebagai panduan yang aktif.
            self._bersihkan_guide("start MANUAL PILOT: no guide in this mode")
            self.btn_manual.setText("MANUAL PILOT ACTIVE · click to STOP")
            from procman import MANUAL_PROFIL
            self._terapkan_profil(MANUAL_PROFIL)      # profil manual (ringan/+VIO)
            self._request_mode("manual")
            self.pm.start("joy")
            self.pm.start("teleop")
            # REKAM SESI MANUAL (keputusan pemilik 3 Okt: "logger otomatis").
            # PENTING (perbaikan 3 Okt malam, dari run RUN_03_OCTOBER_1256/1258): stop logger dari
            # jalur "misi autonomous berhenti" bersifat ASINKRON. Versi pertama hanya memeriksa
            # is_running() sekali, dan saat itu prosesnya belum mati -> start dilewati, lalu logger
            # benar-benar mati -> gt terekam tetapi vins/imu/depth kosong (gejala yang berulang di
            # 6 run pemilik). Sekarang: TUNGGU logger benar-benar mati (maks 3 s), lalu nyalakan
            # ULANG (CSV bersih) — deterministik, tanpa balapan.
            for _ in range(30):
                if not self.pm.is_running("logger"):
                    break
                time.sleep(0.1)
            if self.pm.is_running("logger"):
                self.pm.stop("logger")
                for _ in range(30):
                    if not self.pm.is_running("logger"):
                        break
                    time.sleep(0.1)
            self._rec_manual = False
            try:
                self.pm.start("logger")
                self._rec_manual = True
                self._log("REC ● logger aktif — sesi MANUAL direkam "
                          "(gt/vins/imu/depth/altitude.csv + frames/)")
            except Exception as e:                               # noqa: BLE001
                self._log("REC gagal menyalakan logger: %s (rekam manual dari panel STACK)" % e)
            self._log("START MANUAL PILOT → joy + teleop gamepad (deadman required: L1/R1)")
            if hasattr(self, "_layout_mapping"):
                self._layout_mapping("START MANUAL")
        else:
            self.btn_manual.setText("START MANUAL PILOT")
            if self.pm.is_running("teleop") or self.pm.is_running("joy"):
                self._log("STOP MANUAL PILOT → stopping teleop (neutral + disarm)")
                self.pm.stop("teleop")
                self.pm.stop("joy")
            if getattr(self, "_rec_manual", False) and self.pm.is_running("logger"):
                self.pm.stop("logger")
                self._log("REC ■ logger dihentikan (sesi manual selesai, CSV sudah ditutup)")
                self._rec_manual = False
            self._request_mode("mapping")

    def _bersihkan_guide(self, sebab=""):
        """Buang garis petunjuk (jalur rencana) dari peta — dipanggil saat misi BARU mulai.

        Rencana lama TIDAK boleh tertinggal di layar saat node misi baru masih menyala:
        kotak misi sebelumnya terbaca seolah-olah masih berlaku. Sesudah misi berhenti,
        rencana sengaja DIBIARKAN (bukti jalur apa yang baru ditempuh).
        """
        peta = getattr(self, "peta", None)
        if peta is None or getattr(peta, "rencana", None) is None:
            return
        peta.bersihkan_rencana()
        self._log("map guide cleared%s (new plan will be drawn when the mission node "
                  "publishes it)" % ((" — %s" % sebab) if sebab else ""))

    def _on_manual_map_toggled(self, checked):
        """MAPPING MANUAL (2 Okt 2026): pilot mengemudi, capture + garis petunjuk jalan.

        Beda dari MANUAL lama: mode kamera `mapping_manual` (depan + stereo) dengan
        CAPTURE relay AKTIF, node guide menerbitkan rencana jalur (peta menunjukkan jalur
        + kotak area), dan saat di-STOP run-nya ditutup seperti misi autonomous (logger +
        analisis pasca-misi + SFM).
        Beda dari AUTONOMOUS: TIDAK ada controller yang mengirim RC — pilot memegang stick
        (deadman L1 wajib); yang jalan hanya guide (garis petunjuk) + capture.
        """
        b = getattr(self, "btn_manual_map", None)
        if b is None:
            return
        if checked:
            if not (self.pm.is_running("sim") and self.pm.is_running("sitl")):
                self._log("mapping manual cancelled: simulator/SITL not running")
                b.setChecked(False)
                return
            # EKSKLUSIF: ketiga tombol misi memakai link RC 5762 yang sama
            if getattr(self, "btn_auto", None) is not None and self.btn_auto.isChecked():
                self._log("mapping manual: stopping AUTONOMOUS first (exclusive RC)")
                self.btn_auto.setChecked(False)
            if getattr(self, "btn_manual", None) is not None and self.btn_manual.isChecked():
                self._log("mapping manual: stopping MANUAL PILOT first (exclusive RC)")
                self.btn_manual.setChecked(False)     # memicu netral + mode mapping
            b.setText("MAPPING MANUAL ACTIVE · click to STOP")
            # hasil misi SEBELUMNYA dipindah ke <run>/misi_XX sebelum logger/relay membuka
            # ulang CSV-nya (mode "w") — aturan yang sama dengan START AUTONOMOUS.
            if getattr(self, "_mission_ran", False):
                self._putar_hasil_misi()
            self._bersihkan_guide("start MAPPING MANUAL")
            self._terapkan_profil("mapping_manual")
            self._request_mode("mapping_manual")
            self.pm.start("joy")
            self.pm.start("teleop")
            self.pm.start("guide")
            if not self.pm.is_running("relay"):
                self._log("mapping manual → relay capture started (SFM material)")
                self.pm.start("relay")
            if not self.pm.is_running("logger"):
                self._log("mapping manual → logger started (run CSVs)")
                self.pm.start("logger")
            self._mission_ran = True
            self._mission_finalized = False
            # Peta: unduhan ubin baru DIPAUSE selama sesi (jangan berebut CPU dengan VINS).
            if getattr(self, "peta", None) is not None:
                self.peta.jeda = True
                self._log("map: tile downloads paused during the mapping session")
            self._log("START MAPPING MANUAL → pilot flies with the gamepad (deadman L1 must "
                      "be held); capture + planned-path guide active, no RC from any "
                      "controller")
            if hasattr(self, "_layout_mapping"):
                self._layout_mapping("START MAPPING MANUAL")
        else:
            b.setText("START MAPPING MANUAL")
            if self.pm.is_running("guide"):
                self._log("STOP MAPPING MANUAL → guide node stopped")
                self.pm.stop("guide")
            if self.pm.is_running("teleop") or self.pm.is_running("joy"):
                self._log("STOP MAPPING MANUAL → stopping teleop (neutral + disarm)")
                self.pm.stop("teleop")
                self.pm.stop("joy")
            try:                              # netralkan RC supaya ROV tidak lari
                if getattr(self, "mavbridge", None) is not None:
                    self.mavbridge.send_neutral_rc()
            except Exception:                                     # noqa: BLE001
                pass
            self._request_mode("mapping")
            if getattr(self, "peta", None) is not None and self.peta.jeda:
                self.peta.jeda = False
                self._log("map: tile downloads resumed (mapping session finished)")
            if self.pm.is_running("relay") and not self._relay_diperlukan():
                self._log("mapping stopped → relay turned off (no VINS needs it)")
                self.pm.stop("relay")
            elif self.pm.is_running("relay"):
                self._log("mapping stopped → relay KEPT RUNNING (VINS still needs stereo+IMU)")
            if hasattr(self, "_layout_mapping"):
                self._layout_mapping("STOP MAPPING MANUAL")
            # run ditutup seperti misi autonomous: logger distop (summary.txt) + analisis
            # pasca-misi + SFM otomatis (permintaan pemilik 2 Okt).
            if getattr(self, "_mission_ran", False):
                self._finalize_mission_logs()

    def _on_autonomous_toggled(self, checked):
        if os.environ.get("ROVGUI_DEBUG"):
            print(f"[dbg] toggled checked={checked} "
                  f"mower_running={self.pm.is_running('lawnmower')} "
                  f"logger_running={self.pm.is_running('logger')} "
                  f"mission_ran={getattr(self, '_mission_ran', None)}", flush=True)
        if checked:
            if not (self.pm.is_running("sim") and self.pm.is_running("sitl")):
                self._log("mission cancelled: simulator/SITL not running")
                self.btn_auto.setChecked(False)
                return
            # eksklusif dengan mode manual: teleop juga mengirim RC override 5762
            if getattr(self, "btn_manual", None) is not None and self.btn_manual.isChecked():
                self._log("autonomous mode: stopping MANUAL PILOT first (exclusive RC)")
                self.btn_manual.setChecked(False)      # memicu netral + mode mapping
            # eksklusif dengan MAPPING MANUAL (pilot + teleop mengirim RC yang sama)
            if (getattr(self, "btn_manual_map", None) is not None
                    and self.btn_manual_map.isChecked()):
                self._log("autonomous mode: stopping MAPPING MANUAL first (exclusive RC)")
                self.btn_manual_map.setChecked(False)
            # garis petunjuk lama dibuang; rencana BARU menyusul dari node lawnmower
            self._bersihkan_guide("start AUTONOMOUS")
            self._request_mode("mapping")
            self.btn_auto.setText("AUTONOMOUS RUNNING · click to STOP")
            # indikator misi (26 Sep, permintaan pemilik): tombol harus MEMBERI TANDA
            # saat misi jalan / selesai / dihentikan. Sumber: baris log lawnmower
            # ([WP k/N], "Survey COMPLETE", "MISSION STOPPED") lewat _on_proc_log.
            self.mission_state = "jalan"
            self.mission_wp = None
            self.mission_t0 = time.time()
            self._mower_seen = False     # penjaga "misi berhenti sendiri" (lihat _refresh)
            self._update_mission_ui()
            # MISI KEDUA di sesi yang sama (perbaikan 30 Sep): pindahkan hasil misi sebelumnya ke
            # <run>/misi_XX/ SEBELUM apa pun dinyalakan. Logger & relay membuka ulang CSV-nya
            # dengan mode "w" saat dijalankan kembali, dan relay juga mengulang penomoran
            # cap_00001 → tanpa pemindahan ini data misi pertama hilang/tertimpa.
            if getattr(self, "_mission_ran", False):
                self._putar_hasil_misi()
            # profil MAPPING (26 Sep): stereo ON, kamera depan OFF, VINS+logger ON
            self._terapkan_profil("mapping")
            self._log("START AUTONOMOUS → running lawnmower controller")
            if not self.pm.is_running("logger"):
                self._log("logger not running → starting it first (Stage B CSV)")
                self.pm.start("logger")
            self._mission_ran = True            # pasca-misi: stop logger + analisis
            self._mission_finalized = False
            # ANCHOR MISI = POSE SAAT TOMBOL AUTONOMOUS DIPENCET (penegasan pemilik, 3 Okt 2026):
            # "anchor dimulainya misi adalah titik disaat mode autonomous itu dipencet, bukan
            # balik lagi ke anchor pertama robot di deploy".
            # Masalahnya nyata: procman.start() TIDAK me-restart komponen yang sudah jalan
            # (`if self.is_running(name): return True`), sedangkan GUI meng-autostart node misi
            # saat dibuka. Akibatnya node memakai anchor POSE BOOT/DEPLOY (kode node mengasumsikan
            # "node diluncurkan tepat saat operator menekan START AUTONOMOUS"). Sekarang node misi
            # dimulai ULANG di sini supaya odom pertamanya = pose robot saat tombol ditekan.
            # CATATAN (3 Okt, koreksi): restart node DIHAPUS. Pemilik menegaskan ada DUA anchor:
            # (1) anchor deploy = acuan lokasi kapal, dan (2) anchor MISI = titik mulai kotak
            # survei (boleh berbeda). Restart node akan menghapus acuan deploy + state misi.
            # Sekarang node sendiri yang memasang ulang anchor MISI saat mode berpindah masuk ke
            # mode misi (lihat _mode_cb: "ANCHOR MISI (kotak survei)").
            # SFM OTOMATIS pasca-misi (permintaan pemilik 25 Sep): pemantau dinyalakan
            # bersama misi -> begitu misi selesai/berhenti, mozaik diproses dan
            # muncul di panel SFM (panel tetap kosong sampai itu terjadi).
            # SFM TIDAK dinyalakan sekarang (permintaan pemilik: jangan idle sejak awal);
            # dinyalakan saat MISI BERHENTI — lihat _finalize_worker.
            self.pm.start("lawnmower")
            # RELAY capture: hanya hidup selama misi autonomous (26 Sep). Dulu relay adalah
            # node di dalam launch simulator sehingga merekam sepanjang GUI dibuka padahal
            # gunanya hanya menyediakan bahan SFM untuk misi yang baru dijalankan.
            # 30 Sep: relay TIDAK lagi dimatikan saat misi berhenti bila VINS masih hidup
            # (lihat _relay_diperlukan) — memutus pasokan IMU membuat VINS meledak pada misi
            # berikutnya.
            if not self.pm.is_running("relay"):
                self._log("START AUTONOMOUS → relay capture started (SFM material for this mission)")
                self.pm.start("relay")
            # JEDA unduhan ubin peta selama misi: unduhan/uBin tidak boleh berebut CPU dan
            # jaringan dengan VINS (permintaan pemilik: jangan mengganggu VIO). Peta yang
            # sudah tercache tetap tampil; hanya unduhan BARU yang ditahan.
            if getattr(self, "peta", None) is not None:
                self.peta.jeda = True
                self._log("map: tile downloads paused during mission (save CPU for VIO)")
            # penanda SEGMEN autonomous: start point (26 Sep)
            self._segmen_aktif = self._penanda_segmen("mulai")
            # LAYOUT (permintaan pemilik 27 Sep): tampilan TIDAK BERUBAH antar mode — tetap
            # layout MAPPING (termasuk panel Autonomous mission Ctrl+7 + Mission log);
            # tombol misi sendiri menyala (teks "AUTONOMOUS RUNNING").
            if hasattr(self, "_layout_mapping"):
                self._layout_mapping("START AUTONOMOUS")
        else:
            self.btn_auto.setText("START AUTONOMOUS")
            if self.pm.is_running("lawnmower"):
                self._log("STOP AUTONOMOUS → stopping controller")
                self.pm.stop("lawnmower")
                try:                              # netralkan RC supaya ROV tidak lari
                    if getattr(self, "mavbridge", None) is not None:
                        self.mavbridge.send_neutral_rc()
                except Exception:
                    pass
            if getattr(self, "peta", None) is not None and self.peta.jeda:
                self.peta.jeda = False
                self._log("map: tile downloads resumed (mission finished)")
            # RELAY capture: dulu dimatikan saat misi berhenti (26 Sep) karena capture-nya sudah
            # lengkap untuk SFM. Sejak 30 Sep aturannya SATU dengan profil (_relay_diperlukan):
            # relay JANGAN dimatikan selama VINS hidup — relay-lah satu-satunya sumber
            # /rov/stereo/* DAN /rov/imu/data. Terukur di RUN_30_SEPTEMBER_2002: relay mati
            # 20:08:45 (akhir misi-1) dan hidup lagi 20:10:27 (awal misi-2) → VINS melihat
            # "Abnormal IMU timestamp jump detected: 103.876" → pose meledak ke kilometer
            # ("VIO bergerak sangat jauh"). Capture tetap tidak menumpuk: gate-nya berbasis jarak
            # (dist >= step) sehingga ROV yang diam tidak menghasilkan foto.
            if self.pm.is_running("relay") and not self._relay_diperlukan():
                self._log("mission stopped → relay turned off (no VINS/lawnmower needs it)")
                self.pm.stop("relay")
            elif self.pm.is_running("relay"):
                self._log("mission stopped → relay KEPT RUNNING (VINS still needs stereo+IMU; "
                          "a gap here makes the VIO diverge on the next mission)")
            # misi berhenti (manual ATAU controller selesai) -> tutup log run dengan rapi
            # LAYOUT: tetap MAPPING (permintaan pemilik 27 Sep: jangan berubah antar mode).
            if hasattr(self, "_layout_mapping"):
                self._layout_mapping("STOP AUTONOMOUS")
            # Penanda SEGMEN autonomous: ENDPOINT (26 Sep). Ditaruh SEBELUM penutupan log
            # supaya evaluasi per-segmen bisa membaca batas waktunya.
            if getattr(self, "_segmen_aktif", None):
                sebab = "SELESAI" if self.mission_state == "selesai" else "DIHENTIKAN"
                self._penanda_segmen("selesai", sebab=sebab,
                                     wp=str(getattr(self, "mission_wp", "-")))
            if getattr(self, "_mission_ran", False):
                self._finalize_mission_logs()

    # ------------------------------------------------------------------ #
    # Penutup log run: hentikan logger (SIGINT -> summary.txt) lalu analisis
    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # Penanda SEGMEN autonomous (26 Sep, permintaan pemilik)
    # "logger VINS dijadikan evaluasi untuk bagian saat mode autonomous mulai dan selesai;
    #  kalau mode autonomousnya sudah selesai, simpan lognya. Kalau satu run dua kali mode
    #  autonomous, jadi evaluasinya ada 2."
    # ------------------------------------------------------------------ #
    def _penanda_segmen(self, jenis, sebab="", wp=""):
        """Tulis penanda mulai/selesai segmen autonomous; kembalikan nama segmen (S01, ...)."""
        try:
            d = os.path.join(self.pm.log_dir, "segmen")
            os.makedirs(d, exist_ok=True)
            nama = None
            if jenis == "mulai":
                n = 1
                while os.path.exists(os.path.join(d, "S%02d_mulai.txt" % n)):
                    n += 1
                nama = "S%02d" % n
                am = getattr(self, "_auto_mode", None)
                mode = am[0] if isinstance(am, (tuple, list)) and am else "fixed_wp"
                baris_ = am[1] if isinstance(am, (tuple, list)) and len(am) > 1 else 1.0
                isi = "%f|%s|%.2f|autonomous start" % (time.time(), mode, float(baris_))
            else:
                nama = getattr(self, "_segmen_aktif", None)
                if not nama:
                    return None
                isi = "%f|%s|%s|autonomous end" % (time.time(), sebab or "SELESAI", wp or "-")
            with open(os.path.join(d, "%s_%s.txt" % (nama, jenis)), "w") as f:
                f.write(isi + "\n")
            self._log("segment %s: %s marker written (%s)" % (nama, jenis, isi.split("|", 1)[1]))
            return nama
        except OSError as exc:
            self._log("segment marker failed: %r" % (exc,))
            return None

    def _jalankan_sfm(self, run_dir, sebab):
        """Nyalakan komponen "SFM hasil misi" SEGERA saat bahannya lengkap.

        PERBAIKAN 27 Sep 2026 (keluhan pemilik: "setelah misi autonomous selesai, stack SFM
        hasil misi-nya masih off"). Dulu `_finalize_worker` menutup logger, menjalankan
        analisis Tahap B, lalu Tahap C, dan BARU menyalakan SFM. Analisis itu berat; terukur
        pada RUN_27_SEPTEMBER_1009: misi selesai 10:18:36 → logger berhenti 10:19:20 →
        analisis selesai 10:20:39 → SFM baru menyala 10:20:39. Selama ~2 menit itu baris
        "SFM hasil misi" berbunyi "off" (komponen belum pernah dijalankan), yang terbaca
        sebagai "SFM tidak jalan".

        Bahannya sendiri sudah lengkap begitu RELAY berhenti (capture/ + captures.csv), jadi
        SFM tidak perlu menunggu apa pun. Analisis dan mozaik sekarang berjalan PARALEL —
        keduanya praktis satu inti dan mesin ini 20 inti.

        Bila SFM run sebelumnya masih membangun mozaik, run ini DITANDAI ANTRE dan
        dijalankan otomatis oleh `_refresh_stack_panel` begitu komponennya bebas (tidak
        mungkin menjalankan dua sfm_auto sekaligus karena `procman.start` mengembalikan
        True tanpa start ulang bila masih jalan).
        """
        if getattr(self, "pm", None) is None:
            return
        andai = os.path.join(run_dir, "capture")
        try:
            n = len(os.listdir(andai)) if os.path.isdir(andai) else 0
        except OSError:
            n = 0
        if self.pm.is_running("sfm"):
            self._sfm_antre = True
            self.pm.sig_log.emit("logger", "previous SFM run still running → this SFM run is "
                                           "QUEUED (started automatically once the current "
                                           "one finishes)")
            return
        self.pm.sig_log.emit("logger", "%s → mission SFM result started NOW, in parallel "
                                       "with analysis (%d files in capture/)" % (sebab, n))
        self.pm.start("sfm")

    def _evaluasi_segmen(self, run_dir):
        """Jalankan tools/evaluasi_segmen.py: nilai tiap segmen + simpan lognya."""
        alat = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evaluasi_segmen.py")
        if not os.path.exists(alat):
            alat = "/home/zayd/Project BRIN VIO/Progress/tools/evaluasi_segmen.py"
        try:
            r = subprocess.run(["python3", alat, run_dir, "--plot", "--tutup-terbuka"],
                               capture_output=True, text=True, timeout=600)
            for ln in (r.stdout or "").strip().splitlines()[-8:]:
                self.pm.sig_log.emit("logger", "segmen| " + ln)
            self.pm.sig_log.emit("logger", "segment evaluation finished (rc=%d) → "
                                           "<run>/segmen/ringkasan_segmen.txt" % r.returncode)
        except Exception as exc:                              # noqa: BLE001
            self.pm.sig_log.emit("logger", "segment evaluation failed: %r" % (exc,))

    def _basi(self, run_dir, pasangan):
        """True bila CSV sumber lebih BARU (atau hasilnya belum ada) — 26 Sep.

        Dipakai untuk (a) melewati analisis yang sudah terbaru dan (b) MENYEGARKAN yang
        basi. Kejadian nyata RUN_1432: analisis terlanjur dijalankan saat CSV baru berisi
        9,4 s dari misi 743 s -> trajektori.png/altitude.png tidak mewakili misi walau
        datanya lengkap (pemilik: "trajektori ga jelas, beda dari window").
        """
        for src, hasil in pasangan:
            ps = os.path.join(run_dir, src)
            ph = os.path.join(run_dir, hasil)
            if os.path.exists(ps) and (not os.path.exists(ph)
                                       or os.path.getmtime(ps) > os.path.getmtime(ph) + 1.0):
                return True
        return False

    def _alasan_finalize(self, teks):
        """Catat alasan penutupan run DITUNDA — sekali saja per alasan (tidak spam 1 Hz).

        Panel memanggil `_finalize_mission_logs()` tiap detik sampai berhasil; tanpa penjaga
        ini baris log yang sama akan membanjiri panel.
        """
        if getattr(self, "_finalize_alasan", None) == teks:
            return
        self._finalize_alasan = teks
        self._log("run finalization postponed: %s (will retry every second)" % teks)

    def _finalize_mission_logs(self, blocking=False):
        """Hasilkan isi run lengkap seperti RUN_.../RUN_1354: CSV logger + summary.txt
        (logger dihentikan) + aligned.csv + trajektori.png (analyze_stage_b.py)."""
        if os.environ.get("ROVGUI_DEBUG"):
            print(f"[dbg] finalize dipanggil blocking={blocking} "
                  f"finalized={getattr(self, '_mission_finalized', None)} "
                  f"finalizing={getattr(self, '_finalizing', None)}", flush=True)
        if blocking:
            t = getattr(self, "_final_thr", None)
            if t is not None and t.is_alive():
                t.join(timeout=60)
            elif not getattr(self, "_mission_finalized", False):
                self._finalize_worker()
            return
        if getattr(self, "_mission_finalized", False) or getattr(self, "_finalizing", False):
            return
        # ── PENJAGA (26 Sep, disempurnakan 27 Sep) ──────────────────────────────────────
        # (a) lawnmower masih berjalan → penutupan DITUNDA, bukan dibatalkan permanen.
        #     `procman.stop()` mengirim SIGINT di THREAD LATAR lalu langsung kembali, sehingga
        #     beberapa ratus milidetik setelah perintah stop komponennya MASIH terlihat
        #     "running". Itulah race yang membuat RUN_27_SEPTEMBER_1026 tidak pernah ditutup:
        #     misi selesai 10:34:46 (penanda segmen tertulis), tetapi logger tidak dihentikan,
        #     analisis pasca-misi tidak jalan, dan SFM tidak pernah dinyalakan — sampai pemilik
        #     beralih ke MANUAL 71 s kemudian (profil manual yang mematikan logger). Sekarang
        #     `_refresh_stack_panel` mencoba ulang tiap detik sampai berhasil.
        if self.pm.is_running("lawnmower"):
            self._alasan_finalize("lawnmower still stopping (SIGINT just sent) — postponed")
            return
        # (b) misi baru mulai: tombol masih menyala & masih dalam masa tenggang 10 s → JANGAN
        #     menutup log (akar bug RUN_1449: logger mati di detik pertama misi).
        if not getattr(self, "_mower_seen", False):
            if self.btn_auto.isChecked() and (time.time()
                                              - getattr(self, "mission_t0", 0.0)) < 10.0:
                self._alasan_finalize("new mission starting (lawnmower not seen yet) — postponed")
                return
            self._log("log finalization: mission was never seen running, but the button is "
                      "already off → continuing anyway (nothing to record)")
        self._finalize_alasan = None
        self._finalizing = True
        self._final_thr = threading.Thread(target=self._finalize_worker, daemon=True)
        self._final_thr.start()

    def _finalize_worker(self):
        self._finalizing = True
        if os.environ.get("ROVGUI_DEBUG"):
            print(f"[dbg] finalize worker START logger_running="
                  f"{self.pm.is_running('logger')} run_dir={self.pm.log_dir}", flush=True)
        try:
            run_dir = self.pm.log_dir
            # SFM hasil misi dinyalakan PALING DULU (lihat _jalankan_sfm): bahannya sudah
            # lengkap begitu relay berhenti, dan menunggu analisis Tahap B/C membuat baris
            # "SFM hasil misi" tampak "off" ~2 menit setelah misi selesai.
            self._jalankan_sfm(run_dir, "mission stopped")

            if self.pm.is_running("logger"):
                self.pm.sig_log.emit("logger", "mission stopped → logger stopped (summary.txt written)")
                self.pm.stop("logger")
                t0 = time.time()
                while self.pm.is_running("logger") and time.time() - t0 < 15:
                    time.sleep(0.3)

            def rows(path):
                try:
                    with open(path) as f:
                        return max(sum(1 for _ in f) - 1, 0)
                except OSError:
                    return 0

            ng = rows(os.path.join(run_dir, "gt.csv"))
            nv = rows(os.path.join(run_dir, "vins.csv"))
            # ── Anti-basi (26 Sep) ──────────────────────────────────────────────────────
            # Analisis hanya dijalankan bila HASILNYA lebih tua dari CSV-nya. Kejadian
            # nyata RUN_1432: analisis terlanjur jalan saat CSV baru berisi 9,4 s dari misi
            # 743 s -> trajektori.png & altitude.png tidak mewakili misi dan pemilik melihat
            # plot "ga jelas / beda dari window trajektori". Sekarang finalize juga
            # MENYEGARKAN analisis yang basi (bukan cuma melewatinya).
            basi_b = self._basi(run_dir, (("gt.csv", "aligned.csv"), ("gt.csv", "trajektori.png")))
            basi_a = self._basi(run_dir, (("altitude.csv", "altitude.png"),))
            if ng >= 10 and nv >= 10 and basi_b:
                self.pm.sig_log.emit("logger", f"data ready (gt={ng}, vins={nv} rows) → Stage B analysis")
                with open(os.path.join(run_dir, "analysis.log"), "a", buffering=1) as lf:
                    lf.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} post-mission analysis =====\n")
                    rc = subprocess.call(["python3", ANALYZE, run_dir, "--plot"],
                                         stdout=lf, stderr=subprocess.STDOUT,
                                         env=self.pm.env or os.environ.copy())
                self.pm.sig_log.emit("logger", f"analysis finished (rc={rc}) → aligned.csv, trajektori.png")
            elif ng >= 10 and nv >= 10:
                self.pm.sig_log.emit("logger", "Stage B analysis already up to date → skipped")
            else:
                self.pm.sig_log.emit("logger", f"not enough data for analysis (gt={ng}, vins={nv} rows)")

            # evaluasi fusi altitude (Tahap C) — analyzer-nya sendiri yang
            # melewatkan dengan anggun bila altitude.csv tidak ada (run lama)
            if basi_a:
                with open(os.path.join(run_dir, "analysis.log"), "a", buffering=1) as lf:
                    lf.write(f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                             f"evaluasi fusi altitude (Tahap C) =====\n")
                    rc_alt = subprocess.call(
                        ["python3", ANALYZE_ALT, run_dir, "--plot"],
                        stdout=lf, stderr=subprocess.STDOUT,
                        env=self.pm.env or os.environ.copy())
                self.pm.sig_log.emit(
                    "logger", f"altitude fusion evaluation finished (rc={rc_alt}) → altitude.png")
            else:
                self.pm.sig_log.emit("logger", "altitude fusion evaluation already up to date → skipped")

            # SFM HASIL MISI: dinyalakan SEGERA saat misi berhenti — lihat _jalankan_sfm()
            # di awal worker ini (27 Sep). Blok lama di sini menyalakannya SETELAH analisis,
            # dan itulah sebab baris SFM tampak "off" ~2 menit setelah misi selesai.

            # evaluasi SEGMEN autonomous (26 Sep, permintaan pemilik): satu evaluasi per fase
            # autonomous (start point -> endpoint), log potongannya disimpan per segmen.
            self._evaluasi_segmen(run_dir)
        except Exception as exc:
            self.pm.sig_log.emit("logger", f"run log finalization failed: {exc}")
        finally:
            self._mission_finalized = True
            self._finalizing = False

    # ------------------------------------------------------------------ #
    # Autostart berurutan
    # ------------------------------------------------------------------ #
    def _autostart_stack(self):
        self.status.setText("opening stack: cleaning up leftover processes …")
        threading.Thread(target=self._start_sequence, daemon=True).start()

    def _start_sequence(self):
        self.pm.cleanup_stale()
        self._log("leftover processes cleaned up")
        self.pm.start("sim")
        if self._wait_for(lambda: self.rates.get("gt", 0) > 5, 150, "simulator (odometry)"):
            # mode manager: penentu kamera per mode (spec §8). Sensor ber-service
            # set_enabled MULAI DISABLED -> harus jalan sebelum VINS butuh gambar.
            self.pm.start("mode")
            self._wait_for(lambda: any("MODE MAPPING" in ln
                                       for ln in self.pm.log_lines("mode", 40)),
                           60, "mode manager (mapping)")
            self.pm.start("sitl")
            if self._wait_for(lambda: bool(self.mav.get("msgs")), 90, "MAVLink SITL"):
                self.pm.start("vins")
                # logger Tahap B (gt/vins/imu/depth.csv + frames) ikut start supaya
                # isi run GUI = isi run jalur manual (RUN_.../RUN_1354)
                self.pm.start("logger")
                self._log("stack ready — START AUTONOMOUS button enabled")
            else:
                self._log("MAVLink not connected; mission cannot be run yet")
        else:
            self._log("simulator not ready; see STACK LOG")

    def _wait_for(self, pred, timeout, label):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if pred():
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        self._log(f"timeout waiting for {label} ({timeout}s)")
        return False

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def _update_gating(self):
        if self._perlu_titip():
            self._ui_aman(self._update_gating)      # dipanggil dari thread ProcManager juga
            return
        if not hasattr(self, "btn_auto"):
            return
        sim_ok = self.pm.is_running("sim") and self.rates.get("gt", 0) > 5
        sitl_ok = self.pm.is_running("sitl") and bool(self.mav.get("msgs"))
        ready = sim_ok and sitl_ok
        if not self.btn_auto.isChecked():
            self.btn_auto.setEnabled(ready)
            self.btn_auto.setToolTip("simulator + SITL ready" if ready else
                                     "waiting: " +
                                     ", ".join([x for x, ok in (("simulator", sim_ok),
                                                                ("ArduSub SITL", sitl_ok)) if not ok]))
        if getattr(self, "btn_manual", None) is not None and not self.btn_manual.isChecked():
            self.btn_manual.setEnabled(ready)
            self.btn_manual.setToolTip(
                "gamepad piloting + front camera (mode MANUAL)" if ready else
                "waiting: " + ", ".join([x for x, ok in (("simulator", sim_ok),
                                                          ("ArduSub SITL", sitl_ok)) if not ok]))
        # MAPPING MANUAL (2 Okt): tombol misi ketiga — pilot mengemudi, capture + guide jalan.
        if (getattr(self, "btn_manual_map", None) is not None
                and not self.btn_manual_map.isChecked()):
            self.btn_manual_map.setEnabled(ready)
            self.btn_manual_map.setToolTip(
                "pilot flies (gamepad, deadman L1) while the mapping session runs: capture + "
                "planned-path guide on the map" if ready else
                "waiting: " + ", ".join([x for x, ok in (("simulator", sim_ok),
                                                          ("ArduSub SITL", sitl_ok)) if not ok]))
        # --autostart-mission: mulai misi otomatis begitu stack siap (sekali saja)
        if (getattr(self.args, "autostart_mission", False) and ready
                and not self.btn_auto.isChecked()
                and not getattr(self, "_mission_auto_started", False)):
            self._mission_auto_started = True
            self._log("autostart-mission: stack ready, starting mission automatically")
            self.btn_auto.setChecked(True)

    def _refresh_stack_panel(self):
        if self._perlu_titip():
            self._ui_aman(self._refresh_stack_panel)
            return
        if not hasattr(self, "stack_rows"):
            return
        self._update_gating()            # tombol misi ikut dinilai ulang tiap detik
        # SFM yang ANTRE (misi baru berhenti sementara sfm_auto run sebelumnya masih
        # membangun mozaik) dijalankan di sini — thread GUI 1 Hz, jadi proses panjang itu
        # tidak pernah tertinggal tanpa dinyalakan (perbaikan 27 Sep).
        if getattr(self, "_sfm_antre", False) and not self.pm.is_running("sfm"):
            self._sfm_antre = False
            self.pm.sig_log.emit("logger", "SFM queued → starting now "
                                           "(previous run has finished)")
            self.pm.start("sfm")
        st = self.pm.states()
        # ── JARING PENGAMAN PENUTUPAN RUN (27 Sep 2026) ─────────────────────────────────
        # Kejadian nyata RUN_27_SEPTEMBER_1026: misi 24 WP selesai 10:34:46, tetapi penutupan
        # run DITOLAK karena race (lawnmower masih terlihat "running" beberapa ratus ms setelah
        # SIGINT dikirim di thread latar) — dan karena tombol misi sudah mati, tidak ada apa pun
        # yang mencoba lagi. Akibatnya: logger tidak ditutup, analisis pasca-misi tidak jalan,
        # dan SFM tidak pernah dinyalakan (keluhan pemilik "SFM-nya ga nyala").
        # Sekarang: bila misi SUDAH berakhir tetapi run belum ditutup, coba lagi setiap detik.
        if (getattr(self, "_mission_ran", False)
                and not getattr(self, "_mission_finalized", False)
                and not getattr(self, "_finalizing", False)
                and not self.btn_auto.isChecked()
                and getattr(self, "mission_state", "idle") in ("selesai", "dihentikan")
                and not self.pm.is_running("lawnmower")):
            self._finalize_mission_logs()
        if getattr(self.args, "debug_stack", False):
            print(f"[gating] sim={self.pm.is_running('sim')} sitl={self.pm.is_running('sitl')} "
                  f"vins={self.pm.is_running('vins')} mower={self.pm.is_running('lawnmower')} | "
                  f"gt={self.rates.get('gt', 0):.2f} vio_l={self.rates.get('vio_left', 0):.2f} "
                  f"vins_hz={self.rates.get('vins', 0):.2f} imu={self.rates.get('imu', 0):.1f} | "
                  f"mav_msgs={self.mav.get('msgs', 0)} mode={self.mav.get('mode', '--')} | "
                  f"auto_enabled={self.btn_auto.isEnabled()} auto_checked={self.btn_auto.isChecked()}",
                  flush=True)
        for name, row in self.stack_rows.items():
            s = st.get(name, {})
            running = s.get("running")
            theme.set_peran(row["dot"], "ok" if running else "faint")
            if running:
                up = s.get("uptime", 0)
                row["state"].setText(f"{int(up // 60):02d}:{int(up % 60):02d}")
                theme.set_peran(row["state"], "dim")
                row["btn"].setText("■")
            elif name == "sfm" and getattr(self, "_sfm_antre", False):
                # SFM run ini menunggu run sebelumnya selesai — jangan tampil "off"
                # (27 Sep: "off" di sini dulu terbaca sebagai "SFM tidak jalan").
                row["state"].setText("queued")
                theme.set_peran(row["state"], "warn")
                row["btn"].setText("▶")
            else:
                rc = s.get("rc")
                if rc is None:
                    row["state"].setText("off")
                    theme.set_peran(row["state"], "faint")
                elif rc == 0:
                    # dihentikan normal (SIGINT/STOP) — BUKAN kegagalan. Sebelumnya
                    # ditampilkan "exit 0" yang mudah dibaca sebagai masalah.
                    row["state"].setText("done")
                    theme.set_peran(row["state"], "dim")
                else:
                    row["state"].setText(f"exit {rc}")
                    theme.set_peran(row["state"], "warn")
                row["btn"].setText("▶")
        # kalau misi selesai/berhenti sendiri -> tombol autonomous ikut mati
        # ── GUARD (26 Sep, keluhan pemilik "di WP rapat logger ga sampai misi selesai") ──
        # Jangan pernah menyimpulkan "misi berhenti" untuk misi yang BARU dimulai: proses
        # lawnmower butuh ~1 detik untuk muncul di snapshot. Tanpa guard ini panel langsung
        # mematikan tombol → handler STOP → _finalize_mission_logs() → **logger dihentikan**.
        # Terukur pada RUN_26_SEPTEMBER_1449: logger mati 14:50:07 (detik yang sama dengan WP
        # pertama) lalu misi lanjut 601 s TANPA logger. Syarat sekarang: misi harus sudah
        # TERLIHAT jalan (_mower_seen) dan lewat masa tenggang 10 s sejak tombol ditekan.
        mower_run = bool(st.get("lawnmower", {}).get("running"))
        if self.btn_auto.isChecked() and mower_run:
            self._mower_seen = True
        if (self.btn_auto.isChecked()
                and getattr(self, "_mower_seen", False)
                and not mower_run
                and time.time() - getattr(self, "mission_t0", 0.0) > 10.0):
            self.btn_auto.setChecked(False)
            self._log("mission stopped (controller finished/exited)")

    def _update_mission_ui(self):
        """TANDA di tombol START AUTONOMOUS (permintaan pemilik 26 Sep).

        idle       : "START AUTONOMOUS"
        jalan      : "AUTONOMOUS · RUNNING · wp k/N"     (hijau)
        selesai    : "AUTONOMOUS · COMPLETE · wp N/N"    (cyan, tetap tampil)
        dihentikan : "AUTONOMOUS · STOPPED (…)"          (kuning)
        Warna diambil dari PROPERI `status` (theme.qss), bukan gaya inline, supaya
        tombolnya ikut tema yang sedang aktif. Chip misi di panel MISSION CONTROL
        memakai status yang sama.
        Sumber status: baris log lawnmower (diparsing di _on_proc_log).
        """
        if self._perlu_titip():
            self._ui_aman(self._update_mission_ui)
            return
        b = getattr(self, "btn_auto", None)
        if b is None:
            return
        st = getattr(self, "mission_state", "idle")
        wp = getattr(self, "mission_wp", None)
        prog = (" · wp %d/%d" % wp) if wp else ""
        chip = getattr(self, "chip_misi", None)
        if st == "jalan":
            b.setText("AUTONOMOUS · RUNNING%s · click to STOP" % prog)
            theme.set_prop(b, "status", "run")
            b.setToolTip("Autonomous mission running%s." % (prog or ""))
            if chip is not None:
                chip.set_state("ok", "running%s" % prog)
        elif st == "selesai":
            b.setText("AUTONOMOUS · COMPLETE%s" % prog)
            theme.set_prop(b, "status", "done")
            b.setToolTip("Mission complete (Survey COMPLETE). Press to run again.")
            if chip is not None:
                chip.set_state("accent", "complete%s" % prog)
        elif st == "dihentikan":
            b.setText("AUTONOMOUS · STOPPED%s" % prog)
            theme.set_prop(b, "status", "stop")
            b.setToolTip("Mission stopped before completion (fallback / operator). "
                         "Check the Events & log run panel.")
            if chip is not None:
                chip.set_state("warn", "stopped%s" % prog)
        else:
            b.setText("START AUTONOMOUS")
            theme.set_prop(b, "status", "")
            b.setToolTip("Run the autonomous mission (lawnmower).")
            if chip is not None:
                chip.set_state("ink_faint", "idle")

    def _on_proc_log(self, name, line):
        if self._perlu_titip():
            self._ui_aman(lambda: self._on_proc_log(name, line))
            return
        self._log(f"{name}: {line}")
        # Controller TIDAK keluar sendiri setelah misi selesai, jadi tanpa deteksi ini
        # tombol misi tetap menyala dan log run tidak pernah difinalisasi.
        # "Survey COMPLETE" = misi selesai -> tutup misi (stop controller + finalize log).
        if os.environ.get("ROVGUI_DEBUG") and name == "lawnmower":
            print(f"[dbg] proc_log lawnmower: phrase={'Survey COMPLETE' in line} "
                  f"checked={self.btn_auto.isChecked()} line={line[:80]!r}", flush=True)
        # --- tanda misi di tombol START AUTONOMOUS (26 Sep) ---
        # Sumber paling andal = log controller sendiri: "[WP k/N]" (progres),
        # "Survey COMPLETE" (selesai), "MISSION STOPPED" (fallback/stop).
        if name == "lawnmower":
            m = re.search(r"\[WP (\d+)/(\d+)\]", line)
            if m:
                self.mission_wp = (int(m.group(1)), int(m.group(2)))
                if getattr(self, "mission_state", "idle") == "jalan":
                    self._update_mission_ui()
            if "Survey COMPLETE" in line:
                self.mission_state = "selesai"
                # LAYOUT (27 Sep): tetap MAPPING — hasil misi langsung diperiksa di susunan
                # yang sama, tidak ada perpindahan panel saat misi selesai.
                if hasattr(self, "_layout_mapping"):
                    self._layout_mapping("mission complete")
                self._update_mission_ui()
            elif "MISSION STOPPED" in line:
                self.mission_state = "dihentikan"
                # LAYOUT (27 Sep): fallback pun TIDAK mengubah susunan panel.
                if hasattr(self, "_layout_mapping"):
                    self._layout_mapping("fallback/stopped")
                self._update_mission_ui()
        if name == "lawnmower" and "Survey COMPLETE" in line and self.btn_auto.isChecked():
            self._log("Survey COMPLETE → mission closed, run log finalized")
            print("[dbg] DETECTED Survey COMPLETE -> setChecked(False)", flush=True)
            self.btn_auto.setChecked(False)   # memicu stop controller + RC netral + finalize
        # Auto-PID (26 Sep): baris bertanda "TUNE|" dari rov_pid_autotune.py diumpankan ke
        # panel AUTO-PID (status + riwayat) supaya pilot melihat progres tanpa buka log.
        if name == "tuning":
            self._tuning_line(line)

    def _log(self, text):
        """Simpan satu baris log ke penyangga, lalu gambar ulang baris yang tampil.

        Sejak redesign baris log menyimpan waktu + komponen + isi dan bisa DISARING
        (All / Warn / Mission), serta dibungkus (word-wrap) supaya tidak ada teks
        terpotong di kolom tengah.
        """
        if not hasattr(self, "log_labels"):
            return                                     # panel log belum dibangun
        if self._perlu_titip():
            self._ui_aman(lambda: self._log(text))
            return
        isi = str(text).strip()
        komp, pesan = "", isi
        if ":" in isi:
            depan, sisa = isi.split(":", 1)
            # hanya nama komponen stack yang dianggap TAG; pesan GUI biasa
            # ("next mission area: ...", "appearance: ...") tetap utuh di kolom pesan
            # supaya tidak tampil sebagai tag terpotong ("NEXT MI", "APPEARA").
            if depan.strip().lower() in COMPONENTS:
                komp, pesan = depan.strip(), sisa.strip()
        kat = "warn" if any(k in pesan.lower() for k in
                            ("fail", "timeout", "error", "abort", "fallback", "gagal")) else "info"
        if (komp.strip().lower() in ("lawnmower", "logger", "mode", "sitl", "sfm")
                or "mission" in pesan.lower() or "[WP " in pesan or "survey" in pesan.lower()):
            kat = "warn" if kat == "warn" else "mission"
        try:
            self._log_lines.append((time.strftime("%H:%M:%S"), komp.strip(), pesan, kat))
        except AttributeError:
            self._log_lines = deque([(time.strftime("%H:%M:%S"), komp.strip(), pesan, kat)],
                                    maxlen=400)
        self._render_log()

    def _set_log_filter(self, kunci):
        """Saring baris log yang tampil (isi log tetap utuh di penyangga)."""
        if self._perlu_titip():
            self._ui_aman(lambda: self._set_log_filter(kunci))
            return
        self._log_filter = kunci
        seg = getattr(self, "seg_log", None)
        if seg is not None:
            seg.set_dipilih(kunci)
        self._render_log()

    def _toggle_log_body(self):
        """Lipat/buka baris log (panelnya tetap ada, tingginya tidak melompat-lompat)."""
        buka = not self._log_scroll.isVisible()
        self._log_scroll.setVisible(buka)
        self.btn_log_buka.setText("▾" if buka else "▸")

    def _render_log(self):
        """Gambar ulang baris log dari penyangga (dipaksa juga saat tema berganti)."""
        labs = getattr(self, "log_labels", None)
        if not labs:
            return
        baris = getattr(self, "_log_lines", [])
        saring = getattr(self, "_log_filter", "all")
        if saring == "warn":
            baris = [b for b in baris if b[3] == "warn"]
        elif saring == "mission":
            baris = [b for b in baris if b[3] in ("mission", "warn")]
        baris = list(baris)[::-1][:len(labs)]           # terbaru di atas
        warna = {"ts": theme.warna("ink_faint"), "tag": theme.warna("ink_dim"),
                 "isi": theme.warna("ink_dim")}
        for i, lab in enumerate(labs):
            if i >= len(baris):
                lab.setText("<span style='color:%s'>%s</span>"
                            % (warna["ts"], "—" if i == 0 else ""))
                continue
            ts, komp, pesan, kat = baris[i]
            warna_isi = {"warn": theme.warna("warn"),
                         "mission": theme.warna("accent_text"),
                         "info": theme.warna("ink_dim")}[kat]
            warna_tag = {"warn": theme.warna("warn"),
                         "mission": theme.warna("accent_text"),
                         "info": theme.warna("ink_faint")}[kat]
            lab.setText(
                "<span style='color:%s'>%s</span>&nbsp;&nbsp;"
                "<span style='color:%s'>%s</span>&nbsp;&nbsp;"
                "<span style='color:%s'>%s</span>"
                % (warna["ts"], ts, warna_tag, _esc(komp.upper()[:7]), warna_isi, _esc(pesan)))

    # ------------------------------------------------------------------ #
    # (Dihapus 23 Sep 2026) Mirror jendela simulator — panel simulasi kini
    # kosong; pemilik sistem menonton jendela Stonefish secara langsung.
    # Bug lama: _poll_embed mereferensikan self.mirror yang tidak pernah ada
    # -> AttributeError tiap 2 detik di mode mirror.
    # ------------------------------------------------------------------ #
