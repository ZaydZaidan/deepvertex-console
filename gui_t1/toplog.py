"""toplog.py — logger SENSOR lengkap per sesi, DI TOPSIDE (robot tak dibebani).

Menulis `sensors.csv` (10 Hz) ke folder sesi, dari state MAVLink yang SUDAH
diterima konsol — tidak ada proses tambahan di robot (permintaan pemilik
17 Okt: logging topside saja). File dibiarkan TERBUKA (fd) sehingga tetap
valid walau folder sesi di-rename di tengah dive (pola sama dengan
mavlink.tlog). Pelengkap `mavlink.tlog` (byte mentah MAVLink): di sini baris
TERBACA per sensor.
"""
import csv
import os


KOLOM = ("t_iso", "t_unix", "link", "mode", "armed", "volt_v", "curr_a",
         "batt_pct", "depth_m", "water_c", "press_abs", "roll_deg",
         "pitch_deg", "yaw_deg", "ekf_ok", "sys_healthy", "msgs")


class SensorLog:
    """Writer CSV tahan-rename: buka sekali, flush berkala, tutup saat keluar."""

    def __init__(self, path, flush_setiap=20):
        self.path = path
        self._n = 0
        self._flush_setiap = max(1, int(flush_setiap))
        self._f = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self._f = open(path, "w", newline="", encoding="utf-8")
            self._w = csv.DictWriter(self._f, fieldnames=KOLOM,
                                     extrasaction="ignore")
            self._w.writeheader()
            self._f.flush()
        except OSError:
            self._f = None

    def tulis(self, baris):
        if self._f is None:
            return
        try:
            self._w.writerow(baris)
            self._n += 1
            if self._n % self._flush_setiap == 0:
                self._f.flush()
        except Exception:                                   # noqa: BLE001
            pass

    def aktif(self):
        return self._f is not None

    def tutup(self):
        if self._f is not None:
            try:
                self._f.flush()
                self._f.close()
            except Exception:                               # noqa: BLE001
                pass
            self._f = None
