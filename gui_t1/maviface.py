"""Telemetri ArduSub/Pixhawk lewat MAVLink (pymavlink) untuk dashboard.

Endpoint:
  --mavlink auto (default)  : coba udpin:0.0.0.0:14550 lalu tcp:127.0.0.1:5760
  --mavlink udpin:0.0.0.0:14551 (bila SITL dijalankan dengan --serial2 udpclient:…:14551)

Catatan penting:
  * ArduPilot hanya membuka stream telemetri ke link UDP setelah menerima paket dari
    GCS -> bridge ini mengirim HEARTBEAT tipe GCS setiap 1 s (seperti QGroundControl).
  * SERIAL0 (TCP 5760) hanya menerima SATU klien. Saat controller lawnmower memakai
    5760, dashboard harus lewat UDP (14550/14551).
"""
import contextlib
import os
import threading
import time

from PySide6 import QtCore

try:
    from pymavlink import mavutil
except ImportError:            # pragma: no cover
    mavutil = None

PIXHAWK = {
    "mode": "--", "armed": False, "ekf_ok": None, "volt": None, "curr": None,
    "remaining": None, "temp": None, "press_abs": None, "press_diff": None,
    "heading": None, "throttle": None, "depth": None, "last_text": "", "last_text_sev": 6,
    "msgs": 0, "link": False, "endpoint": "--",
}

# pesan yang diminta (nama perintah, rate Hz, nama pesan)
WANTED = [
    ("MAV_CMD_SET_MESSAGE_INTERVAL", 30, "ATTITUDE"),
    ("MAV_CMD_SET_MESSAGE_INTERVAL", 2, "SYS_STATUS"),
    ("MAV_CMD_SET_MESSAGE_INTERVAL", 1, "BATTERY_STATUS"),
    ("MAV_CMD_SET_MESSAGE_INTERVAL", 5, "SCALED_PRESSURE2"),
    ("MAV_CMD_SET_MESSAGE_INTERVAL", 5, "VFR_HUD"),
    ("MAV_CMD_SET_MESSAGE_INTERVAL", 2, "EKF_STATUS_REPORT"),
    ("MAV_CMD_SET_MESSAGE_INTERVAL", 5, "STATUSTEXT"),
    ("MAV_CMD_SET_MESSAGE_INTERVAL", 2, "HEARTBEAT"),
]


class MavBridge(QtCore.QObject):
    sig_attitude = QtCore.Signal(float, float, float)   # roll, pitch, yaw (deg)
    sig_vehicle = QtCore.Signal(dict)
    sig_text = QtCore.Signal(str, int)                  # teks, severity
    sig_status = QtCore.Signal(str)

    def __init__(self, url="auto", parent=None):
        super().__init__(parent)
        self.candidates = {
            # 5760 DULUAN: SITL ArduSub berhenti di "Waiting for connection ...." sampai ada
            # klien di SERIAL0 — dashboard yang membuka blokir itu sekaligus memakai link-nya.
            # 14550 (SERIAL1/UDP) cadangan. 5762 sengaja TIDAK dipakai: itu milik controller.
            "auto": ["tcp:127.0.0.1:5760", "udpin:0.0.0.0:14550"],
        }.get(url, [url])
        self.url = url
        self.mav = None
        self._stop = threading.Event()
        self._thread = None
        self.state = dict(PIXHAWK)
        # antrian perintah dari GUI (mode/arm) -> dikirim dari thread MAVLink ini
        self._cmdq = []
        self._cmd_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def start(self):
        if mavutil is None:
            self.sig_status.emit("pymavlink not available")
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self.mav is not None:
                self.mav.close()
        except Exception:
            pass

    def send_neutral_rc(self):
        """Netralkan RC override (ch1..ch8 = 1500). Dipakai saat misi dihentikan
        supaya ROV tidak terus melaju dengan perintah terakhir."""
        try:
            self.mav.mav.rc_channels_override_send(1, 1, 1500, 1500, 1500, 1500,
                                                  1500, 1500, 1500, 1500)
            self.sig_status.emit("RC neutralized (mission stopped)")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # Perintah kendaraan dari GUI (menu Vehicle): ganti mode ArduSub, arm/disarm.
    # Dikirim dari thread MAVLink sendiri supaya tidak berebut socket.
    def queue_mode(self, mode_num):
        with self._cmd_lock:
            self._cmdq.append(("mode", int(mode_num)))

    def queue_arm(self, on):
        with self._cmd_lock:
            self._cmdq.append(("arm", bool(on)))

    def _drain_cmds(self):
        with self._cmd_lock:
            cmds, self._cmdq = self._cmdq, []
        for kind, val in cmds:
            try:
                if kind == "mode":
                    # MAV_CMD_DO_SET_MODE (set_mode_send bisa ACK sukses tapi mode tidak berubah)
                    self.mav.mav.command_long_send(
                        self.mav.target_system, self.mav.target_component,
                        mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
                        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, val,
                        0, 0, 0, 0, 0)
                    self.sig_status.emit("flight mode %d requested" % val)
                elif kind == "arm":
                    self.mav.mav.command_long_send(
                        self.mav.target_system, self.mav.target_component,
                        mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
                        1 if val else 0, 0, 0, 0, 0, 0, 0)
                    self.sig_status.emit("%s requested" % ("ARM" if val else "DISARM"))
            except Exception as e:
                self.sig_status.emit("vehicle command failed: %r" % e)

    def _send_gcs_heartbeat(self):
        """WAJIB: tanpa paket dari GCS, ArduPilot tidak membuka stream telemetri."""
        try:
            self.mav.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_GCS,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                0, 0, mavutil.mavlink.MAV_STATE_ACTIVE)
        except Exception:
            pass

    def _request_rates(self):
        for name, hz, msgname in WANTED:
            try:
                mid = getattr(mavutil.mavlink, f"MAVLINK_MSG_ID_{msgname}")
                self.mav.mav.command_long_send(
                    1, 1, getattr(mavutil.mavlink, name), 0,
                    mid, int(1e6 / max(hz, 1)), 0, 0, 0, 0, 0)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    def _run(self):
        """Coba seluruh kandidat; kalau semuanya gagal, ulangi lagi.

        WAJIB: SITL baru muncul setelah simulator siap, dan ArduSub sendiri berhenti di
        'Waiting for connection ....' sampai ada klien di SERIAL0 (5760). Karena itu bridge
        tidak boleh menyerah sekali coba — ia mengulang sampai berhasil atau dihentikan.
        """
        while not self._stop.is_set():
            for url in self.candidates:
                if self._stop.is_set():
                    return
                self.sig_status.emit(f"trying MAVLink {url} …")
                if self._stream(url):
                    return
                self.sig_status.emit(f"no telemetry at {url}")
            self.sig_status.emit("MAVLink not connected — waiting for SITL (retry in 5 s)")
            for _ in range(10):
                if self._stop.is_set():
                    return
                time.sleep(0.5)
        return

    def _stream(self, url) -> bool:
        try:
            self.mav = mavutil.mavlink_connection(url, source_system=250,
                                                  source_component=190,
                                                  dialect="ardupilotmega")
            self.mav.mav.srcSystem = 250
            self.mav.mav.srcComponent = 190
        except Exception as exc:
            self.sig_status.emit(f"failed to open {url}: {exc}")
            return False

        # fase handshake: kirim heartbeat GCS sampai ArduSub menjawab (maks 8 s).
        # Catatan: pymavlink menulis "EOF on TCP socket" ke stderr setiap kali koneksi
        # gagal; pesan itu wajar dan TIDAK boleh membanjiri log aplikasi.
        got = None
        t_end = time.monotonic() + 8.0
        devnull = os.devnull
        while time.monotonic() < t_end and not self._stop.is_set():
            self._send_gcs_heartbeat()
            try:                                  # redam spam EOF dari pymavlink
                with open(devnull, "w") as dn, contextlib.redirect_stderr(dn):
                    got = self.mav.recv_match(type="HEARTBEAT", blocking=True, timeout=0.7)
            except Exception:
                got = None
            if got is not None:
                break
        if got is None:
            self._close()
            return False

        self.state["link"] = True
        self.state["endpoint"] = url
        self.sig_status.emit(f"MAVLink connected via {url} (sys {got.get_srcSystem()})")
        self._request_rates()
        t_req = t_hb = time.monotonic()
        t_att = t_veh = 0.0

        while not self._stop.is_set():
            now = time.monotonic()
            if now - t_hb > 1.0:                     # jaga link tetap hidup
                self._send_gcs_heartbeat()
                self._drain_cmds()
                t_hb = now
            if now - t_req > 20.0:                   # segarkan permintaan rate
                self._request_rates()
                t_req = now
            try:
                msg = self.mav.recv_match(blocking=True, timeout=1.0)
            except Exception:
                msg = None
            if msg is None:
                continue
            self.state["msgs"] += 1
            t = msg.get_type()
            now = time.monotonic()

            if t == "HEARTBEAT" and msg.get_srcSystem() == self.mav.target_system:
                self.state["mode"] = mavutil.mode_string_v10(msg)
                self.state["armed"] = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)

            elif t == "ATTITUDE":
                if now - t_att > 0.05:
                    t_att = now
                    self.sig_attitude.emit(*[x * 57.29578 for x in (msg.roll, msg.pitch, msg.yaw)])

            elif t == "SYS_STATUS":
                v = msg.voltage_battery / 1000.0
                self.state["volt"] = v if v > 1.0 else None      # sim sering kirim 0
                c = msg.current_battery / 100.0
                self.state["curr"] = c if c > 0.05 else None
                self.state["remaining"] = msg.battery_remaining if msg.battery_remaining != -1 else None
                ekf = getattr(msg, "ekf_ok", None)
                if ekf is not None:
                    self.state["ekf_ok"] = bool(ekf)

            elif t == "BATTERY_STATUS":
                if self.state["volt"] is None and msg.voltages and msg.voltages[0] != 65535:
                    self.state["volt"] = msg.voltages[0] / 1000.0
                if msg.current_battery != -1:
                    self.state["curr"] = msg.current_battery / 100.0
                if msg.battery_remaining != -1:
                    self.state["remaining"] = msg.battery_remaining

            elif t == "SCALED_PRESSURE2":
                # di sim Stonefish nilai tekanan bisa tidak realistis -> simpan apa adanya
                # tapi jangan tampilkan angka mustahil sebagai kedalaman
                raw_abs = msg.press_abs if msg.press_abs > 100.0 else None
                self.state["press_abs"] = raw_abs
                self.state["press_diff"] = msg.press_diff
                t_c = msg.temperature / 100.0
                self.state["temp"] = t_c if -50.0 < t_c < 90.0 else None
                rho_g = 1025.0 * 9.81
                if abs(msg.press_diff) > 1.0:
                    d = msg.press_diff * 100.0 / rho_g
                elif raw_abs is not None and msg.press_abs > 900.0:
                    d = (msg.press_abs - 1013.25) * 100.0 / rho_g
                else:
                    d = None
                self.state["depth"] = d if (d is not None and 0.0 < d < 200.0) else None

            elif t == "VFR_HUD":
                self.state["heading"] = msg.heading
                self.state["throttle"] = msg.throttle

            elif t == "EKF_STATUS_REPORT":
                flags = msg.flags
                need = (mavutil.mavlink.EKF_ATTITUDE | mavutil.mavlink.EKF_VELOCITY_HORIZ |
                        mavutil.mavlink.EKF_POS_HORIZ_ABS)
                self.state["ekf_ok"] = bool(flags & need == need)

            elif t == "STATUSTEXT":
                text = msg.text.strip() if isinstance(msg.text, str) else str(msg.text)
                self.state["last_text"] = text
                self.state["last_text_sev"] = msg.severity
                self.sig_text.emit(text, msg.severity)

            if now - t_veh > 0.2:
                t_veh = now
                self.sig_vehicle.emit(dict(self.state))
        self._close()
        return True

    def _close(self):
        try:
            if self.mav is not None:
                self.mav.close()
        except Exception:
            pass
        self.mav = None
