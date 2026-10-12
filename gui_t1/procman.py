"""Manajer proses stack untuk ROV Mission Console.

GUI membuka stack sendiri: simulator + relay → ArduSub SITL → VINS-Fusion, lalu
tombol START AUTONOMOUS menjalankan controller lawnmower.

Prinsip keselamatan:
  * setiap proses dijalankan dalam group sendiri (`start_new_session`), sehingga
    penghentian memakai SIGINT ke SELURUH group (seperti Ctrl+C) — anak-anak proses
    `ros2 launch` tidak jadi yatim;
  * SIGINT dulu, SIGKILL hanya kalau tidak mau berhenti (grace period);
  * `cleanup_stale()` membunuh sisa proses uji lama sebelum start (dengan pola bracket
    supaya tidak cocok dengan proses dashboard ini sendiri).
"""
import os
import signal
import subprocess
import threading
import time

from PySide6 import QtCore

REPO_SCRIPTS = "/home/zayd/ros2_ws/src/stonefish_bluerov2/scripts"
TOOLS = "/home/zayd/Project BRIN VIO/Progress/tools"
PARAMS = ("/home/zayd/ros2_ws/install/stonefish_bluerov2/share/stonefish_bluerov2/"
          "config/bluerov2/mission_params.yaml")

# urutan start yang benar (sim harus siap sebelum SITL/VINS/logger)
# logger = log_stage_b.py Tahap B -> gt/vins/imu/depth.csv + frames/ + summary.txt
#          supaya isi run GUI sama dengan run jalur manual (RUN_<...>/RUN_1354).
# mode   = rov_mode_manager: pemilik kebijakan kamera per mode (spec §8). Sensor
#          yang punya service set_enabled MULAI DISABLED, jadi node ini harus
#          jalan tepat setelah sim — kalau tidak, VINS tidak pernah dapat gambar.
# sfm    = sfm_auto.py: memantau misi -> memproses SFM pasca-misi (permintaan pemilik
#          25 Sep: jendela mozaik kosong saat mulai, terisi setelah run selesai).
# Logger VINS & SFM TIDAK ikut autostart (permintaan pemilik): logger hanya merekam saat
# mode autonomous aktif (GUI menyalakannya pada START AUTONOMOUS dan mematikannya saat misi
# berhenti), dan SFM baru dijalankan SETELAH misi selesai. Sebelumnya keduanya hidup sejak GUI
# dibuka padahal hanya idle.
ORDER = ["sim", "mode", "sitl", "vins", "lawnmower"]

# komponen tambahan mode PILOT (manual). Sengaja TIDAK di-autostart: hanya hidup
# saat pilot menekan START MANUAL PILOT (dan dihentikan saat START AUTONOMOUS),
# karena teleop dan lawnmower sama-sama mengirim RC override di port 5762
# (dua pengirim = last-writer-wins).
MANUAL_COMPONENTS = ["joy", "teleop"]

# Baris tambahan di panel STACK yang TIDAK ikut autostart (dinyalakan dari menu):
#   tuning = rov_pid_autotune.py (menu Tuning → Auto-PID). Butuh 5762 (link controller),
#            jadi GUI menghentikan lawnmower/teleop dulu supaya tidak rebutan socket.
PANEL_EXTRA = ["relay", "logger", "guide", "sfm", "tuning"]
# `guide` = node rov_plan_guide.py: hanya menerbitkan RENCANA JALUR + kotak area (garis
# petunjuk di peta) untuk mode MAPPING MANUAL; hidup saat mode itu menyala.

# ---- PROFIL OPERASI (26 Sep, permintaan pemilik: "atur arsitekturnya supaya ringan") ----
# Prinsip yang TERUKUR (uji_profil_gpu.sh): beban Stonefish ≈ JUMLAH KAMERA AKTIF ×
# rate-nya (tiap kamera = satu pass scene ~2 juta segitiga; resolusinya tidak dominan),
# ditambah sensor tak terpakai (multibeam) yang kini dimatikan di bluerov2.scn.
#   MAPPING/OTONOM : sim+mode+sitl+vins+logger (+lawnmower; +sfm hanya pasca-misi)
#                    kamera: cam_left + cam_right      (kamera depan OFF)
#   MANUAL RINGAN  : sim+mode+sitl+joy+teleop          (VINS OFF, stereo OFF)
#                    kamera: camera_front saja
#   MANUAL + VIO   : sama + vins                       (untuk merekam lintasan VIO)
#                    kamera: camera_front + cam_left + cam_right  (kebijakan lama)
PROFIL = {
    "mapping": dict(nama="MAPPING/AUTONOMOUS",
                    comp=["sim", "mode", "sitl", "vins", "logger", "lawnmower"],
                    kamera="cam_left cam_right"),
    "manual_ringan": dict(nama="MANUAL LIGHT (without VIO)",
                          comp=["sim", "mode", "sitl", "joy", "teleop"],
                          kamera="camera_front"),
    "manual_vio": dict(nama="MANUAL + VIO",
                       comp=["sim", "mode", "sitl", "joy", "teleop", "vins"],
                       kamera="camera_front cam_left cam_right"),
    # MAPPING MANUAL (2 Okt 2026): pilot mengemudi (joy+teleop), tetapi CAPTURE dan
    # RENCANA JALUR (guide) jalan seperti misi autonomous -> run-nya punya bahan SFM
    # yang sama. Kamera 3 (depan + stereo) seperti MANUAL + VIO: pilot butuh melihat
    # depan dan VINS butuh stereo terus-menerus.
    "mapping_manual": dict(nama="MAPPING MANUAL (pilot + guide + capture)",
                           comp=["sim", "mode", "sitl", "joy", "teleop", "vins", "logger"],
                           kamera="camera_front cam_left cam_right"),
}
# BAWAAN = MANUAL + VIO (VINS nyala) — permintaan pemilik 28 Sep 2026. Profil hemat
# ("manual_ringan") tetap tersedia lewat menu Operator ▸ Manual profile.
MANUAL_PROFIL = "manual_vio"


def set_manual_profile(nama):
    """Pilih profil saat MANUAL (manual_ringan | manual_vio) — dipakai menu GUI."""
    global MANUAL_PROFIL
    MANUAL_PROFIL = nama if nama in ("manual_ringan", "manual_vio") else "manual_vio"
    return MANUAL_PROFIL


def profil(nama):
    return PROFIL.get(nama, PROFIL[MANUAL_PROFIL])

COMPONENTS = {
    "sim": {
        "label": "Simulator + relay",
        "cmd": ["ros2", "launch", "stonefish_bluerov2", "bluerov2_sim_spec.launch.py"],
        # CAPTURE_DIR: launch meneruskan env ini ke rov_capture_relay sebagai
        # `capture_dir` -> relay menulis still full-res + captures.csv (sidecar pose/
        # altitude) ke folder run. WAJIB supaya tiap run punya bahan SFM: tanpa ini
        # tidak ada captures.csv dan sfm_colmap.py menolak memproses (temuan 25 Sep:
        # run GUI 16:31 punya frames/ dari logger saja, tanpa sidecar -> tanpa peta).
        "env": {"CAPTURE_DIR": "{run}/capture", "RELAY_AKTIF": "0"},
        "ready": "sim odometry streaming",
    },
    "mode": {
        "label": "Mode manager (cam)",
        "cmd": ["ros2", "run", "stonefish_bluerov2", "rov_mode_manager.py",
                "--ros-args", "--params-file", PARAMS],
        "ready": "MODE MAPPING",
    },
    "sitl": {
        "label": "ArduSub SITL",
        "cmd": ["bash", f"{REPO_SCRIPTS}/start_ardusub.sh"],
        "ready": "TCP 5760 + telemetry 5762",
    },
    "vins": {
        "label": "VINS-Fusion",
        "cmd": ["ros2", "launch", "vins_fusion_ros2", "vins_bluerov2.launch.py"],
        "ready": "/odometry publishing",
    },
    "relay": {
        "label": "Relay capture (camera→PNG)",
        # 26 Sep: relay DIPISAH dari paket simulator. Dulu ia node di dalam launch sim
        # ("Simulator + relay") sehingga hidup sepanjang GUI dibuka padahal gunanya hanya
        # merekam capture saat misi autonomous. Sekarang GUI menyalakannya saat START
        # AUTONOMOUS dan mematikannya saat misi berhenti (pemilik: "cuman guna pas saya
        # nyalain misi autonomousnya").
        "cmd": ["ros2", "run", "stonefish_bluerov2", "rov_capture_relay.py",
                "--ros-args",
                "--params-file", PARAMS],
        "env": {"CAPTURE_DIR": "{run}/capture"},
        "ready": "writes PNG + captures.csv to {run}/capture",
    },
    "logger": {
        "label": "Logger (VINS/GT)",
        # {run} diganti ProcManager.start() dengan folder run tempat log ditulis,
        # sehingga CSV logger menempati folder yang sama dengan log komponen.
        "cmd": ["python3", "/home/zayd/Project BRIN VIO/Progress/tahapB/log_stage_b.py",
                "--out", "{run}", "--frame-period", "3.0"],
        "ready": "gt/vins/imu/depth.csv filling",
    },
    "lawnmower": {
        "label": "Lawnmower (auto)",
        # SERIAL2/TCP 5762 = link khusus controller (SERIAL0/5760 dipegang dashboard untuk
        # membuka blokir boot SITL; SERIAL0 hanya menerima satu klien).
        # 25 Sep: cmd dibangun `_lawn_cmd()` supaya memuat --params-file (YAML) DAN pilihan
        # mode autonomous (adaptif / WP tetap); sebelumnya node diluncurkan TANPA params-file
        # sehingga hanya default declare yang terbang.
        "cmd": ["ros2", "run", "stonefish_bluerov2", "bluerov2_lawnmower.py",
                "--ros-args", "--params-file", PARAMS,
                "-p", "mavlink_url:=tcp:127.0.0.1:5762",
                # BAWAAN sejak 2 Okt 2026 = LEGACY (lihat catatan di LAWN_PLANNER). Cmd ini
                # dibangun ULANG oleh set_lawn_planner()/set_lawn_area() yang dipanggil di
                # bawah, jadi nilai di sini hanya placeholder sampai panggilan itu.
                "-p", "fixed_line_spacing_m:=1.00",
                "-p", "no_reverse:=false", "-p", "start_frame:=world"],
        "ready": "mission running",
    },
    "guide": {
        # LABEL PENDEK & STATIS (2 Okt 2026): baris panel STACK tidak boleh menuntut lebar
        # lebih dari rel (uji R9: label panjang = rel kanan terpotong), dan label yang memuat
        # AREA akan mengubah tata letak setiap kali operator mengubah area. Info area ada di
        # panel MISSION CONTROL + legenda peta.
        "label": "Guide (manual mapping)",
        # Node GUIDE: hanya menerbitkan rencana jalur + kotak area dari pose ROV saat tombol
        # START MAPPING MANUAL ditekan (garis petunjuk di peta). TIDAK ADA MAVLink di node
        # itu — pilot yang mengemudi. Dijalankan dari POHON SUMBER (seperti logger/SFM),
        # bukan `ros2 run`, supaya berkas baru tidak menuntut `colcon build` lebih dulu;
        # cmd-nya dibangun `_guide_cmd()` supaya AREA & jarak baris SELALU sama dengan misi
        # autonomous (kedua mode menggambar kotak yang sama di peta).
        "cmd": ["python3", REPO_SCRIPTS + "/rov_plan_guide.py",
                "--ros-args", "--params-file", PARAMS],
        "ready": "guide plan published",
    },
    "sfm": {
        "label": "SFM mission result (auto)",
        # Permintaan pemilik 25 Sep: jendela mozaik dibuka KOSONG; begitu run autonomous
        # selesai, SFM diproses otomatis dan tampil di panel. Komponen ini hanya
        # memantau (idle, ~0 % CPU) sampai penanda misi selesai muncul di log run.
        "cmd": ["python3", TOOLS + "/sfm_auto.py",
                "--log-dir", "{run}", "--capture-dir", "{run}/capture",
                "--out", "{run}/sfm"],
        "ready": "waiting for mission to finish",
    },
    "joy": {
        "label": "joy_node",
        # autorepeat: stik diam tetap terkirim -> device-hidup terdeteksi.
        # Tanpa /dev/input/js* node ini bisa keluar; teleop tetap aman (netral).
        "cmd": ["ros2", "run", "joy", "joy_node", "--ros-args",
                "-p", "autorepeat_rate:=20.0", "-p", "deadzone:=0.05"],
        "ready": "joy: gamepad",
    },
    "teleop": {
        "label": "Teleop (manual)",
        "cmd": ["ros2", "run", "stonefish_bluerov2", "rov_gamepad_teleop.py",
                "--ros-args", "--params-file", PARAMS,
                "-p", "mavlink_url:=tcp:127.0.0.1:5762"],
        "ready": "teleop ready",
    },
}

# ---- Pilihan misi autonomous: SATU mode (WP TETAP) --------------------------------
# Mode "adaptive" (grid FOV+overlap, ketinggian adaptif, fill pass) DIHAPUS 30 Sep 2026
# atas permintaan pemilik ("mode autonomous adaptif apus aja karena ga bakal kepake").
# Yang tersisa hanya kerapatan baris: 1,0 m (bawaan) atau 0,55 m (DENSE, untuk overlap
# peta >= 3). Tidak ada lagi `planner:=` di cmd komponen.
LAWN_PLANNER = {"area_m": 5.0,      # setengah-sisi area misi (m); 5,0 = 10x10 m
                "line_m": 1.0,
                # BAWAAN = LEGACY (keputusan pemilik 2 Okt 2026: "motion untuk yang WP puter
                # balik itu belom bener, jadi pakai yang legacy aja dulu"). LEGACY = hukum
                # omni kerangka DUNIA (kotak di (0,0), WP home, tanpa fase putar/aligned).
                # Kembalikan ke mode bebas setelah gerakan tanpa-mundur diperbaiki — tinggal
                # tekan FIXED WP di panel MISSION CONTROL / menu Operator.
                "legacy": True}

# ---- Auto-PID (26 Sep): state komponen `tuning` (dijalankan dari menu Tuning) ----
# ArduSub tidak punya autotune internal (beda dengan ArduCopter), jadi tuning dijalankan
# dari LUAR lewat MAVLink (rov_pid_autotune.py) dan yang disetel adalah parameter ArduSub
# asli: PSC_D_* (kedalaman, cascade di mode ALT_HOLD) dan ATC_* (yaw/roll/pitch).
TUNER = dict(loop="depth", apply=False, arm=True, iters=3, amp=0.0, dur=3.0,
             real=False, out="")


def set_tuner_cmd(loop="depth", apply_=False, arm=True, iters=3, amp=0.0, dur=3.0,
                  real=False, out=None, metode="auto", t_pra=4.0, t_pasca=8.0,
                  z_uji_min=2.5, uji="gangguan", host=None):
    """Bangun `COMPONENTS["tuning"]["cmd"]` untuk peluncuran berikutnya (menu Tuning).

    `host` = endpoint MAVLink tuner (4 Okt): default SITL `tcp:127.0.0.1:5762`; untuk ROBOT
    ASLI isi endpoint link (mis. `udp:192.168.2.1:14550` atau `serial:/dev/ttyACM0:115200`).
    """
    TUNER.update(dict(loop=str(loop), apply=bool(apply_), arm=bool(arm),
                      iters=int(iters), amp=float(amp), dur=float(dur),
                      real=bool(real), metode=str(metode), t_pra=float(t_pra),
                      t_pasca=float(t_pasca), z_uji_min=float(z_uji_min), uji=str(uji)))
    TUNER["out"] = out or os.path.join(
        "/tmp/rov_pid_autotune", "run_%s_%s" % (loop, time.strftime("%Y%m%d_%H%M%S")))
    nama = {"depth": "depth (PSC_D_*)", "yaw": "heading (ATC_*YAW*)",
            "roll": "roll (ATC_*RLL*)", "pitch": "pitch (ATC_*PIT*)",
            "all": "all loops"}.get(str(loop), str(loop))
    skrip = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "scripts", "rov_pid_autotune.py")
    cmd = ["python3", skrip, "--host", (str(host) if host else "tcp:127.0.0.1:5762"),
           "--loop", str(loop), "--iters", str(int(iters)),
           "--dur", "%.1f" % float(dur), "--out", TUNER["out"],
           "--metode", str(metode),
           "--t-pra", "%.1f" % float(t_pra), "--t-pasca", "%.1f" % float(t_pasca),
           "--z-uji-min", "%.1f" % float(z_uji_min), "--uji", str(uji)]
    if amp:
        cmd += ["--amp", "%.0f" % float(amp)]
    if apply_:
        cmd += ["--apply"]
    if arm:
        cmd += ["--arm"]
    if real:
        cmd += ["--real"]
    COMPONENTS["tuning"] = {
        "label": "Auto-PID: %s%s · method %s · trial %s" % (
            nama, " · APPLY" if apply_ else " · measure-only", metode, uji),
        "cmd": cmd,
        "ready": "TUNE|",
    }
    return COMPONENTS["tuning"]


def set_tuner_restore_cmd(snapshot):
    """Komponen `tuning` dalam mode KEMBALIKAN parameter dari snapshot JSON."""
    skrip = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "scripts", "rov_pid_autotune.py")
    COMPONENTS["tuning"] = {
        "label": "Auto-PID: restore parameters",
        "cmd": ["python3", skrip, "--host", "tcp:127.0.0.1:5762",
                "--restore", str(snapshot)],
        "ready": "restore:",
    }
    return COMPONENTS["tuning"]


# komponen `tuning` selalu ada (label idle) supaya barisnya tampil di panel STACK
COMPONENTS.setdefault("tuning", {"label": "Auto-PID (idle)", "cmd": [], "ready": "TUNE|"})


def set_lawn_planner(mode=None, line_m=1.0):
    """Set kerapatan baris misi autonomous untuk peluncuran lawnmower BERIKUTNYA (dipanggil GUI).

    `mode` hanya diterima demi kompatibilitas pemanggil lama dan DIABAIKAN: mode autonomous cuma
    satu (WP TETAP) sejak "adaptive" dihapus 30 Sep 2026.
    """
    try:
        LAWN_PLANNER["line_m"] = max(0.3, float(line_m))
    except (TypeError, ValueError):
        LAWN_PLANNER["line_m"] = 1.0
    line = LAWN_PLANNER["line_m"]
    # MODE LEGACY (permintaan pemilik 1 Okt: "sebagai cadangan ... buat tombol mode gerakan WP yang
    # lama aja (yang gada muter balik)"): hukum OMNI lama — kotak di kerangka DUNIA dengan WP home,
    # tanpa aturan tanpa-mundur, tanpa fase putar/aligned. Dipakai bila mode bebas bermasalah.
    legacy = str(mode).lower() in ("legacy", "fixed_wp_lama", "lama", "omni")
    LAWN_PLANNER["legacy"] = legacy
    COMPONENTS["lawnmower"]["label"] = ("Lawnmower (LEGACY omni, no turn-around)"
                                        if legacy else "Lawnmower (fixed WP %.1f m)" % line)
    a = float(LAWN_PLANNER.get("area_m", 5.0))
    COMPONENTS["lawnmower"]["cmd"] = [
        "ros2", "run", "stonefish_bluerov2", "bluerov2_lawnmower.py",
        "--ros-args", "--params-file", PARAMS,
        "-p", "mavlink_url:=tcp:127.0.0.1:5762",
        "-p", "fixed_line_spacing_m:=%.2f" % line,
        "-p", "no_reverse:=%s" % ("false" if legacy else "true"),
        "-p", "start_frame:=%s" % ("world" if legacy else "robot"),
        # AREA misi (permintaan pemilik 27 Sep: "adain input mau mapping areanya berapa,
        # jangan selalu 10x10, biar tesnya lebih cepat"). Nilai = setengah-sisi (m);
        # 5,0 = 10x10 m (bawaan lama). Ditulis ke cmd sehingga YAML ditimpa per peluncuran.
        "-p", "x_min:=%.2f" % -a, "-p", "x_max:=%.2f" % a,
        "-p", "y_min:=%.2f" % -a, "-p", "y_max:=%.2f" % a,
    ]
    _guide_cmd()          # MAPPING MANUAL memakai AREA + jarak baris yang SAMA
    return line          # (dulu `return m`: variabel mode dihapus 30 Sep 2026 → NameError yang
                         #  tertelan `except` di GUI sehingga label AREA tidak ikut berubah)


def _guide_cmd():
    """Bangun cmd node guide (MAPPING MANUAL) dari AREA & kerapatan baris yang berlaku.

    Dipanggil saat modul dimuat DAN setiap `set_lawn_planner` (AREA/garis berubah) supaya
    kotak + lajur di peta mode MAPPING MANUAL identik dengan yang akan dipakai misi
    autonomous: keduanya membaca LAWN_PLANNER yang sama.
    """
    a = float(LAWN_PLANNER.get("area_m", 5.0))
    line = float(LAWN_PLANNER.get("line_m", 1.0))
    COMPONENTS["guide"]["cmd"] = [
        "python3", REPO_SCRIPTS + "/rov_plan_guide.py",
        "--ros-args", "--params-file", PARAMS,
        "-p", "x_min:=%.2f" % -a, "-p", "x_max:=%.2f" % a,
        "-p", "y_min:=%.2f" % -a, "-p", "y_max:=%.2f" % a,
        "-p", "fixed_line_spacing_m:=%.2f" % line,
    ]
    return COMPONENTS["guide"]["cmd"]


def set_lawn_area(half_m):
    """Set SETENGAH-SISI area misi (m); mode & kerapatan baris yang sedang berlaku DIKUNCI.

    Dipakai GUI (spinbox di panel Autonomous mission). 5,0 = 10x10 m (perilaku lama).
    """
    try:
        LAWN_PLANNER["area_m"] = max(0.5, min(50.0, float(half_m)))
    except (TypeError, ValueError):
        LAWN_PLANNER["area_m"] = 5.0
    # line_m diambil dari cmd juga supaya tidak menimpa pilihan kerapatan baris operator
    line = LAWN_PLANNER.get("line_m", 1.0)
    for c in (COMPONENTS.get("lawnmower", {}).get("cmd") or []):
        if str(c).startswith("fixed_line_spacing_m:="):
            try:
                line = float(str(c).split(":=", 1)[1])
            except ValueError:
                pass
    return set_lawn_planner("fixed_wp", line)


# cmd node guide dibangun sekali saat modul dimuat (AREA bawaan), lalu disegarkan setiap
# AREA/kerapatan baris berubah — jadi komponen `guide` TIDAK PERNAH tanpa cmd.
_guide_cmd()
# cmd lawnmower juga dibangun sekali di sini supaya BAWAAN = LEGACY (2 Okt 2026) benar-benar
# terbawa ke peluncuran PERTAMA, bukan hanya setelah operator menyentuh panel/menu.
set_lawn_planner("fixed_wp_lama", 1.0)


# pola bracket: tidak cocok dengan command line dashboard ini sendiri
STALE_PATTERNS = [
    r"stonefish_simulato[r]",
    r"rov_capture_rela[y]",
    r"rov_altitude_fusi[o]n",
    r"rov_mode_manage[r]",
    r"rov_gamepad_teleo[p]",
    r"joy_nod[e]",
    r"ardusim_patc[h]",
    r"/home/zayd/ros2_ws/ardusu[b]",
    r"vins_fusion_ros2_nod[e]",
    r"bluerov2_lawnmowe[r]",
    r"bluerov2_sim_spe[c][.]launch",
    r"l[o]g_stage_b[.]py",
]


class _Proc:
    def __init__(self, name, popen, log_path):
        self.name = name
        self.popen = popen
        self.log_path = log_path
        self.started = time.time()
        self.rc = None


class ProcManager(QtCore.QObject):
    sig_log = QtCore.Signal(str, str)        # nama komponen, baris log
    sig_changed = QtCore.Signal()            # state berubah

    def __init__(self, log_dir, parent=None):
        super().__init__(parent)
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.procs = {}
        self.logs = {k: [] for k in COMPONENTS}
        self.env = None                              # None = warisi env dashboard
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def is_running(self, name) -> bool:
        p = self.procs.get(name)
        return bool(p and p.popen.poll() is None)

    def states(self) -> dict:
        out = {}
        for k in COMPONENTS:
            p = self.procs.get(k)
            out[k] = {
                "running": self.is_running(k),
                "pid": p.popen.pid if p else None,
                "uptime": (time.time() - p.started) if p and p.popen.poll() is None else 0.0,
                "rc": p.rc if p else None,
            }
        return out

    def log_lines(self, name, n=40):
        return self.logs[name][-n:]

    # ------------------------------------------------------------------ #
    def cleanup_stale(self):
        """Bunuh sisa proses lama sebelum start (aman: pola spesifik + bracket)."""
        killed = []
        for pat in STALE_PATTERNS:
            try:
                r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True)
                for pid in [int(x) for x in r.stdout.split() if x.strip()]:
                    if pid == os.getpid():
                        continue
                    try:
                        os.kill(pid, signal.SIGKILL)
                        killed.append(pid)
                    except ProcessLookupError:
                        pass
            except Exception:
                pass
        if killed:
            time.sleep(1.0)
        return killed

    # ------------------------------------------------------------------ #
    def start(self, name) -> bool:
        if name not in COMPONENTS:
            return False
        # Kunci checked+spawn: dulu cek is_running() dan Popen() di luar kunci, jadi
        # dua jalur (autostart sequence vs START AUTONOMOUS) bisa lolos cek bersamaan
        # -> logger/komponen ter-start 2x, dua proses menulis CSV yang sama -> file
        # korup (baris waktu mundur/dobel) dan analisis ATE jadi sampah (run 1453).
        with self._lock:
            if self.is_running(name):
                return True
            spec = COMPONENTS[name]
            # {run} = folder run (tempat log komponen + CSV logger berada)
            cmd = [c.replace("{run}", self.log_dir) for c in spec["cmd"]]
            # env per komponen (mis. CAPTURE_DIR untuk relay) + {run} disubstitusi
            env = dict(self.env or os.environ.copy())
            for k, v in (spec.get("env") or {}).items():
                env[str(k)] = str(v).replace("{run}", self.log_dir)
            log_path = os.path.join(self.log_dir, f"{name}.log")
            try:
                f = open(log_path, "ab", buffering=0)
                f.write(f"\n===== {time.strftime('%H:%M:%S')} start: {' '.join(cmd)} =====\n".encode())
                popen = subprocess.Popen(
                    cmd,
                    stdout=f, stderr=subprocess.STDOUT,
                    start_new_session=True,          # group sendiri -> bisa di-SIGINT serentak
                    env=env,
                )
            except Exception as exc:
                self._log(name, f"FAILED to start: {exc}")
                return False
            self.procs[name] = _Proc(name, popen, log_path)
        self._log(name, f"started pid {popen.pid}")
        threading.Thread(target=self._tail, args=(name, log_path, popen), daemon=True).start()
        self.sig_changed.emit()
        return True

    def _tail(self, name, path, popen):
        """Ikuti file log dan teruskan baris baru ke GUI."""
        try:
            with open(path, "r", errors="ignore") as f:
                f.seek(0, os.SEEK_END)
                while True:
                    line = f.readline()
                    if line:
                        self._log(name, line.rstrip())
                        continue
                    if popen.poll() is not None:
                        break
                    time.sleep(0.25)
        except Exception:
            pass
        with self._lock:
            p = self.procs.get(name)
            if p:
                p.rc = popen.poll()
        self._log(name, f"process stopped (exit {popen.poll()})")
        # JEJAK EXIT ke berkas run (26 Sep): panel STACK hanya menampilkan "exit N" dan
        # tidak tersimpan di mana pun — pemilik melihat "exit 1" tanpa cara menelusuri
        # komponen mana dan kenapa. Sekarang setiap berhenti dicatat ke
        # <run>/komponen_exit.log bersama 3 baris log terakhir komponen itu.
        try:
            with open(os.path.join(self.log_dir, "komponen_exit.log"), "a") as f:
                f.write("\n===== %s · %s stopped (exit %s) =====\n"
                        % (time.strftime("%H:%M:%S"), name, popen.poll()))
                try:
                    with open(path, "r", errors="ignore") as lf:
                        ekor = lf.readlines()[-3:]
                    for ln in ekor:
                        f.write("  | " + ln.rstrip() + "\n")
                except OSError:
                    pass
        except OSError:
            pass
        self.sig_changed.emit()

    def _log(self, name, text):
        self.logs.setdefault(name, []).append(f"[{name}] {text}")
        if len(self.logs[name]) > 400:
            self.logs[name] = self.logs[name][-200:]
        self.sig_log.emit(name, text)

    # ------------------------------------------------------------------ #
    def stop(self, name, grace=6.0, hard=False):
        p = self.procs.get(name)
        if os.environ.get("ROVGUI_DEBUG"):
            print(f"[dbg] stop({name}): p={'present' if p else 'NONE'} "
                  f"poll={p.popen.poll() if p else '-'}", flush=True)
        if not p or p.popen.poll() is not None:
            return True

        def _kill():
            try:
                pgid = os.getpgid(p.popen.pid)
            except ProcessLookupError:
                if os.environ.get("ROVGUI_DEBUG"):
                    print(f"[dbg] _kill({name}): ProcessLookupError getpgid", flush=True)
                return
            sig = signal.SIGKILL if hard else signal.SIGINT
            if os.environ.get("ROVGUI_DEBUG"):
                print(f"[dbg] _kill({name}): sending {sig} to pgid={pgid}", flush=True)
            try:
                os.killpg(pgid, sig)             # SIGINT ke seluruh group = seperti Ctrl+C
            except ProcessLookupError:
                return
            if not hard:
                t0 = time.time()
                while time.time() - t0 < grace and p.popen.poll() is None:
                    time.sleep(0.2)
                if p.popen.poll() is None:
                    if os.environ.get("ROVGUI_DEBUG"):
                        print(f"[dbg] _kill({name}): still alive -> SIGKILL", flush=True)
                    try:
                        os.killpg(pgid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if os.environ.get("ROVGUI_DEBUG"):
                print(f"[dbg] _kill({name}): selesai poll={p.popen.poll()}", flush=True)
            self._log(name, "stopped")
            self.sig_changed.emit()

        threading.Thread(target=_kill, daemon=True).start()
        return True

    def stop_all(self, hard=False, kecuali=()):
        """Hentikan SEMUA komponen; `kecuali` = nama yang DIBIARKAN hidup (26 Sep).

        Dipakai saat GUI ditutup: komponen `sfm` sedang menyusun mozaik (menit-an) dan
        mematikannya membuat pekerjaan hilang — pemilik melihat "peta SFM tidak muncul".
        Aman dibiarkan: keluaran tiap komponen ditulis ke BERKAS (bukan pipa), jadi
        prosesnya tidak macet setelah GUI keluar.

        PERBAIKAN 2 Okt 2026: dulu loop ini hanya menelusuri `ORDER` (sim/mode/sitl/vins/
        lawnmower), sehingga komponen di luar daftar itu — **relay, logger, guide, joy,
        teleop, tuning** — TIDAK PERNAH dimatikan saat GUI ditutup. Gejalanya relay yatim
        yang sudah berkali-kali dicatat (dokumen 30 Sep §11.3: relay yatim memegang kode
        lama + CAPTURE_DIR run terakhir dan memblokir uji berikutnya), dan itu terbukti lagi
        di uji end-to-end 2 Okt (E11: PID relay masih hidup setelah GUI ditutup).
        Sekarang: `ORDER` dibalik lebih dulu (urutan ketergantungan), lalu SELURUH komponen
        lain yang belum disebut.
        """
        urut = [n for n in reversed(ORDER) if n not in kecuali]
        urut += [n for n in COMPONENTS if n not in urut and n not in kecuali]
        for name in urut:
            if self.is_running(name):
                self.stop(name, hard=hard)
