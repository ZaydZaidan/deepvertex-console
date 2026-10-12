"""Jembatan ROS 2 <-> Qt: satu node rclpy di thread terpisah, hasil dikirim lewat signal Qt.

Topik:
  /rov/stereo/left|right/image_raw   (mono8 720p)  -> gambar kamera VIO
  /bluerov2/odometry                 (nav_msgs)    -> ground truth NED (sim)
  /odometry                          (nav_msgs)    -> keluaran VINS-Fusion
  /rov/depth                         (Float64)     -> kedalaman (m)
  /rov/imu/data                      (Imu)         -> accel + gyro
  /rov/capture/image/left            (rgb8)        -> pratinjau foto SfM (opsional)
  --- pilot/manual (23 Sep 2026) ---
  /bluerov2/cam_front/image_raw/image_color (rgb8 640x480) -> tile kamera depan
  /rov/altitude (Float32) + /rov/altitude/status (String)  -> AGL fusi + chip
  /rov/altitude/calib (String)                             -> indikator hover
  /rov/mode (String, transient_local)                      -> chip MODE
  /rov/gamepad/status (String)                             -> chip PAD
  /bluerov2/cam/feedback (JointState)                      -> readout pan/tilt
  /rov/lawnmower/plan (String JSON, transient_local)       -> garis petunjuk (guide): jalur
                                                              rencana + kotak area di peta
  keluaran: /rov/mode/set (String), /bluerov2/cam_pan|cam_tilt/command (Float64)
"""
import json
import threading
import time

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import (qos_profile_sensor_data, QoSProfile, DurabilityPolicy,
                       HistoryPolicy, ReliabilityPolicy)
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image, Imu, JointState, Joy
from std_msgs.msg import Float32, Float64, String

from PySide6 import QtCore


def _yaw_of(q):
    return np.arctan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))


class RosBridge(QtCore.QObject):
    sig_image = QtCore.Signal(str, object)       # side, mono8 array (720p)
    sig_capture = QtCore.Signal(str, object)     # side, rgb8 array (1920x1200)
    sig_gt = QtCore.Signal(float, float, float, float, float)   # t, north, east, down, yaw
    sig_vio = QtCore.Signal(float, float, float, float)         # t, x, y, z (frame VINS)
    sig_depth = QtCore.Signal(float)
    sig_imu = QtCore.Signal(float, float, float, float, float, float)
    sig_rates = QtCore.Signal(dict)
    sig_status = QtCore.Signal(str)
    # --- pilot/manual (23 Sep 2026) ---
    # Nilai h memakai `object`, bukan `float` (perbaikan 29 Sep 2026): status fusi kini
    # juga dikirim saat h BELUM ada (STANDBY) sehingga nilai pertama bisa `None`.
    # Dengan `Signal(float, str)`, `emit(None, ...)` melempar TypeError DI DALAM
    # callback ROS -> executor rclpy mati -> GUI kehilangan SELURUH data ROS (rates
    # odometry tetap 0) -> autostart macet di "simulator (odometry)" dan hanya
    # simulator yang hidup. Gejala nyata pada run pemilik 29 Sep 09:12 & 09:13.
    sig_agl = QtCore.Signal(object, str)         # altitude fusi (m atau None), status
    sig_calib = QtCore.Signal(str)               # ringkas /rov/altitude/calib
    sig_mode = QtCore.Signal(str)                # MAPPING/MANUAL/FALLBACK
    sig_pad = QtCore.Signal(str)                 # status gamepad teleop
    sig_cam_fb = QtCore.Signal(float, float)     # pan, tilt (rad)
    sig_joy = QtCore.Signal(object, object)      # axes[], buttons[] mentah dari /joy
    # Rencana jalur misi (guide): JSON dari node misi/guide -> peta menggambar jalur
    # DIRENCANAKAN + kotak area seketika, tanpa menunggu robot bergerak (2 Okt 2026).
    sig_plan = QtCore.Signal(object)             # dict rencana (hasil json.loads)

    def __init__(self, preview_capture=False, parent=None):
        super().__init__(parent)
        self.preview_capture = preview_capture
        self.node = None
        self.thread = None

    # ---------------- lifecycle ----------------
    def start(self):
        rclpy.init(args=None)
        self.node = _DashboardNode(self, self.preview_capture)
        self.thread = threading.Thread(target=rclpy.spin, args=(self.node,), daemon=True)
        self.thread.start()
        self.sig_status.emit("ROS 2 connected")

    def set_mode(self, mode: str):
        """Minta mode baru ke rov_mode_manager (bukan menyalakan kamera langsung)."""
        if self.node is None:
            return False
        m = String()
        m.data = str(mode)
        self.node.pub_mode_set.publish(m)
        return True

    def set_cam(self, pan=None, tilt=None):
        """Kirim target pan/tilt (rad) ke joint kamera depan."""
        if self.node is None:
            return
        if pan is not None:
            f = Float64(); f.data = float(pan); self.node.pub_pan.publish(f)
        if tilt is not None:
            f = Float64(); f.data = float(tilt); self.node.pub_tilt.publish(f)

    def stop(self):
        if self.node is not None:
            self.node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass


class _DashboardNode(Node):
    def __init__(self, bridge: RosBridge, preview_capture: bool):
        super().__init__("rov_dashboard")
        self.bridge = bridge
        self.counters = {"vio_l": 0, "vio_r": 0, "gt": 0, "vio": 0, "imu": 0, "depth": 0,
                         "cap": 0, "front": 0, "agl": 0, "calib": 0, "joy": 0}
        self._t_last_report = time.monotonic()
        self._t_boot = time.monotonic()
        self.min_period = 1.0 / 25.0      # batasi tampilan (GUI tidak perlu 20 Hz penuh)
        self.last_emit = {"vio_l": 0.0, "vio_r": 0.0, "cap": 0.0, "front": 0.0}

        q = qos_profile_sensor_data
        self.create_subscription(Image, "/rov/stereo/left/image_raw",
                                 lambda m: self._img(m, "left"), q)
        self.create_subscription(Image, "/rov/stereo/right/image_raw",
                                 lambda m: self._img(m, "right"), q)
        # kamera depan pilot (rgb8) — panel ex-mirror
        self.create_subscription(Image, "/bluerov2/cam_front/image_raw/image_color",
                                 lambda m: self._img(m, "front"), q)
        self.create_subscription(Odometry, "/bluerov2/odometry", self._gt, q)
        self.create_subscription(Odometry, "/odometry", self._vio, q)
        self.create_subscription(Float64, "/rov/depth", self._depth, 10)
        # /joy mentah -> bar input stick di panel pilot (baca langsung dari device,
        # jadi GUI tetap menunjukkan input walau teleop belum/tidak jalan)
        self.create_subscription(Joy, "/joy", self._joy, 10)
        self.create_subscription(Imu, "/rov/imu/data", self._imu, q)
        # --- pilot: fusi altitude, mode, gamepad, feedback kamera ---
        self.create_subscription(Float32, "/rov/altitude", self._agl, 10)
        self.create_subscription(String, "/rov/altitude/status", self._agl_status, 10)
        self.create_subscription(String, "/rov/altitude/calib", self._calib, 10)
        self.create_subscription(String, "/rov/gamepad/status", self._pad, 10)
        latched = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                             reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(String, "/rov/mode", self._mode, latched)
        # RENCANA JALUR (2 Okt 2026): node misi autonomous DAN node guide (MAPPING MANUAL)
        # menerbitkan JSON rencana di topik yang sama, latched — jadi peta bisa menggambar
        # garis petunjuk + kotak area begitu tombol misi ditekan. Kalau tidak ada penerbit,
        # peta hanya tidak menampilkan apa-apa (tidak ada galat).
        self.create_subscription(String, "/rov/lawnmower/plan", self._plan, latched)
        self.create_subscription(JointState, "/bluerov2/cam/feedback", self._cam_fb, 10)
        # publisher permintaan (GUI tidak menyalakan kamera langsung — mode manager yang urus)
        self.pub_mode_set = self.create_publisher(String, "/rov/mode/set", 10)
        self.pub_pan = self.create_publisher(Float64, "/bluerov2/cam_pan/command", 10)
        self.pub_tilt = self.create_publisher(Float64, "/bluerov2/cam_tilt/command", 10)
        self._agl_last = None
        # Bawaan SEBELUM pesan pertama: 'STANDBY' (belum ada sumber stereo) — bukan
        # 'INVALID', supaya sesi tuning PID / manual yang tidak punya relay tidak
        # langsung tampil sebagai galat di chip AGL. Node fusi sendiri yang akan
        # mengirim status sebenarnya (OK/DEGRADED/INVALID/STANDBY) tiap 5 s.
        self._agl_status_last = "STANDBY"
        # Kapan status fusi terakhir masuk (monotonik). Dipakai dashboard untuk membedakan
        # "node fusi tidak jalan" (chip 'off') dari "node jalan tapi h belum ada" (STANDBY).
        self._agl_st_t = None
        if preview_capture:
            self.create_subscription(Image, "/rov/capture/image/left",
                                     lambda m: self._img(m, "capture"), q)
        self.create_timer(1.0, self._report)
        self.get_logger().info("dashboard node ready")

    # ---------------- callbacks ----------------
    def _img(self, msg: Image, side: str):
        now = time.monotonic()
        key = {"left": "vio_l", "right": "vio_r", "capture": "cap", "front": "front"}[side]
        self.counters[key] = self.counters.get(key, 0) + 1
        if side == "capture":
            if now - self.last_emit["cap"] < 2.0:      # pratinjau foto cukup 0.5 Hz
                return
            self.last_emit["cap"] = now
            arr = np.frombuffer(msg.data, dtype=np.uint8)
            if msg.encoding == "rgb8":
                arr = arr.reshape(msg.height, msg.width, 3)
            else:
                arr = arr.reshape(msg.height, msg.width)
            self.bridge.sig_capture.emit(side, arr.copy())
            return
        if now - self.last_emit[key] < self.min_period:
            return
        self.last_emit[key] = now
        arr = np.frombuffer(msg.data, dtype=np.uint8)
        if msg.encoding == "mono8":
            arr = arr.reshape(msg.height, msg.width)
        elif msg.encoding in ("rgb8", "bgr8"):
            arr = arr.reshape(msg.height, msg.width, 3)
            if msg.encoding == "bgr8":
                arr = arr[..., ::-1]
            arr = np.ascontiguousarray(arr)
        elif msg.encoding == "yuv422_yuy2" or msg.encoding == "yuyv":
            y = arr.reshape(msg.height, msg.width, 2)[..., 0]
            arr = np.ascontiguousarray(y)
        else:
            return
        self.bridge.sig_image.emit(side, arr.copy())

    # ---------------- pilot (23 Sep 2026) ----------------
    def _agl(self, msg: Float32):
        self.counters["agl"] += 1
        self._agl_last = float(msg.data)
        self.bridge.sig_agl.emit(self._agl_last, self._agl_status_last)

    def _agl_status(self, msg: String):
        """Status fusi masuk tiap 5 s — TERMASUK saat h belum ada (STANDBY/INVALID).

        Dulu sinyal ke GUI ditahan selama h belum ada (`if self._agl_last is not None`),
        sehingga chip fusi TIDAK PERNAH menampilkan STANDBY: yang tampil hanya "off" merah
        dari watchdog laju di dashboard — inilah yang dibaca pemilik sebagai galat fusi
        pada sesi tuning PID. Sekarang status selalu diteruskan (h boleh None/—).
        """
        self._agl_status_last = str(msg.data)
        self._agl_st_t = time.monotonic()
        self.bridge.sig_agl.emit(self._agl_last, self._agl_status_last)

    def _calib(self, msg: String):
        self.counters["calib"] += 1
        self.bridge.sig_calib.emit(str(msg.data))

    def _pad(self, msg: String):
        self.bridge.sig_pad.emit(str(msg.data))

    def _mode(self, msg: String):
        self.bridge.sig_mode.emit(str(msg.data))

    def _plan(self, msg: String):
        """Rencana jalur (guide) dari node misi -> peta (lihat PetaPanel.set_rencana)."""
        try:
            self.bridge.sig_plan.emit(json.loads(str(msg.data)))
        except (ValueError, TypeError) as exc:                       # noqa: BLE001
            self.get_logger().warn(f"mission plan JSON not understood: {exc}")

    def _cam_fb(self, msg: JointState):
        pan = tilt = None
        for i, n in enumerate(msg.name):
            if i >= len(msg.position):
                break
            if "pan" in n:
                pan = float(msg.position[i])
            elif "tilt" in n:
                tilt = float(msg.position[i])
        if pan is not None or tilt is not None:
            self.bridge.sig_cam_fb.emit(pan if pan is not None else 0.0,
                                        tilt if tilt is not None else 0.0)

    def _gt(self, msg: Odometry):
        self.counters["gt"] += 1
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        p, o = msg.pose.pose.position, msg.pose.pose.orientation
        self.bridge.sig_gt.emit(t, p.x, p.y, p.z, _yaw_of(o))

    def _vio(self, msg: Odometry):
        self.counters["vio"] += 1
        t = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        p = msg.pose.pose.position
        self.bridge.sig_vio.emit(t, p.x, p.y, p.z)

    def _depth(self, msg: Float64):
        self.counters["depth"] += 1
        self.bridge.sig_depth.emit(float(msg.data))

    def _joy(self, msg: Joy):
        self.counters["joy"] += 1
        self.bridge.sig_joy.emit([float(v) for v in msg.axes], [int(v) for v in msg.buttons])

    def _imu(self, msg: Imu):
        self.counters["imu"] += 1
        a, g = msg.linear_acceleration, msg.angular_velocity
        self.bridge.sig_imu.emit(a.x, a.y, a.z, g.x, g.y, g.z)

    def _report(self):
        now = time.monotonic()
        dt = now - self._t_last_report
        self._t_last_report = now
        c = self.counters
        rates = {
            "vio_left": c.get("vio_l", 0) / dt,
            "vio_right": c.get("vio_r", 0) / dt,
            "gt": c.get("gt", 0) / dt,
            "vins": c.get("vio", 0) / dt,
            "imu": c.get("imu", 0) / dt,
            "depth": c.get("depth", 0) / dt,
            "capture": c.get("cap", 0) / dt,
            "front": c.get("front", 0) / dt,
            "agl": c.get("agl", 0) / dt,
            "calib": c.get("calib", 0) / dt,
            # umur status fusi (detik; None = belum pernah). Bukan rate: status datang tiap
            # 5 s sehingga rate per detik akan 0 pada 4 dari 5 laporan.
            "agl_st_age": (None if self._agl_st_t is None
                           else now - self._agl_st_t),
            "uptime": now - self._t_boot,
        }
        for k in ("vio_l", "vio_r", "gt", "vio", "imu", "depth", "cap", "front", "agl", "calib"):
            if k in self.counters:
                self.counters[k] = 0
        self.bridge.sig_rates.emit(rates)
