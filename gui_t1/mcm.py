"""mcm.py — klien mavlink-camera-manager: NYALA/MATI stream kamera DI ROBOT.

Permintaan pemilik 17 Okt: tombol ON/OFF kamera = hemat CPU ROBOT (Pi) —
mematikan/menyalakan STREAM di robot, bukan cuma preview GUI.

API MCM yang terbukti di robot (diuji 17 Okt): GET /streams · POST /streams
(membuat = menyalakan) · DELETE /delete_stream?name= (menghapus = mematikan).
Tidak ada route start/stop khusus (PUT/PATCH/OPTIONS = 404), jadi:
  OFF → simpan objek stream (hasil GET) lalu DELETE.
  ON  → POST ulang objek tersimpan dengan BENTUK YANG TERBUKTI SEHAT
        (source = string "/dev/videoX", endpoint tunggal) — re-create 17 Okt
        dengan dua endpoint pernah memicu stall MCM.
Objek tersimpan juga ditulis ke folder data (mcm_streams.json) supaya ON
tetap bisa setelah konsol di-restart.
"""
import json
import os
import threading
import urllib.parse
import urllib.request

from PySide6 import QtCore

KAM_STREAM = {"front": "UDP Stream 0", "stereo L": "UDP Stream 1",
              "stereo R": "UDP Stream 2"}


def payload_dari_get(entry):
    """Objek GET /streams -> badan POST /streams (source = STRING path).

    INI bentuk yang TERBUKTI sehat di robot — jangan diubah tanpa uji ulang.
    """
    vas = dict((entry or {}).get("video_and_stream") or {})
    src = ""
    try:
        src = str(((vas.get("video_source") or {}).get("Local") or {})
                  .get("device_path", "") or "")
    except Exception:                                      # noqa: BLE001
        src = ""
    return {"name": vas.get("name", ""),
            "stream_information": dict(vas.get("stream_information") or {}),
            "source": src}


class McmClient(QtCore.QObject):
    """Klien async (thread) — hasil lewat sig_result(tag, dict)."""

    sig_result = QtCore.Signal(str, object)

    def __init__(self, host="192.168.2.2", parent=None):
        super().__init__(parent)
        self.host = host or "192.168.2.2"
        self.port = 6020
        self._cache = self._muat_cache()

    # -------------------------------------------------------------- cache
    @staticmethod
    def _path_cache():
        try:
            import paths
            return paths.aset_json("mcm_streams.json")
        except Exception:                                  # noqa: BLE001
            return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "mcm_streams.json")

    def _muat_cache(self):
        try:
            with open(self._path_cache(), encoding="utf-8") as f:
                return dict(json.load(f) or {})
        except Exception:                                  # noqa: BLE001
            return {}

    def _simpan_cache(self):
        try:
            with open(self._path_cache(), "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2, ensure_ascii=False)
        except Exception:                                  # noqa: BLE001
            pass

    # -------------------------------------------------------------- http
    def _url(self, path):
        return "http://%s:%d%s" % (self.host, self.port, path)

    def _async(self, tag, fn):
        def kerja():
            try:
                self.sig_result.emit(tag, fn())
            except Exception as exc:                       # noqa: BLE001
                self.sig_result.emit(tag, {"ok": False,
                                           "error": str(exc)[:140]})
        threading.Thread(target=kerja, daemon=True).start()

    def _get_streams(self):
        with urllib.request.urlopen(self._url("/streams"), timeout=6) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def status(self):
        """Cek cepat: stream apa saja yang HIDUP di robot."""
        def fn():
            arr = self._get_streams()
            hidup = []
            for e in arr:
                nm = (e.get("video_and_stream") or {}).get("name", "?")
                hidup.append((nm, bool(e.get("running"))))
            return {"ok": True, "streams": hidup}
        self._async("mcm_status", fn)

    # -------------------------------------------------------------- aksi
    def matikan(self, kam_or_name):
        """Matikan stream kamera DI ROBOT: simpan objeknya lalu DELETE."""
        nama = KAM_STREAM.get(kam_or_name, kam_or_name)

        def fn():
            arr = self._get_streams()
            ent = next((e for e in arr
                        if (e.get("video_and_stream") or {}).get("name")
                        == nama), None)
            if ent is None:
                return {"ok": False, "nama": nama,
                        "error": "no stream '%s' on the robot" % nama}
            self._cache[nama] = ent
            self._simpan_cache()
            req = urllib.request.Request(
                self._url("/delete_stream?name="
                          + urllib.parse.quote(nama)), method="DELETE")
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
            return {"ok": True, "nama": nama, "aksi": "stopped"}
        self._async("mcm_off", fn)

    def nyalakan(self, kam_or_name):
        """Nyalakan kembali stream DI ROBOT (POST objek tersimpan)."""
        nama = KAM_STREAM.get(kam_or_name, kam_or_name)

        def fn():
            ent = self._cache.get(nama)
            if ent is None:
                return {"ok": False, "nama": nama,
                        "error": "no saved settings for '%s' — matikan dulu "
                                 "sekali agar tersimpan" % nama}
            body = payload_dari_get(ent)
            req = urllib.request.Request(
                self._url("/streams"), data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=12) as r:
                r.read()
            return {"ok": True, "nama": nama, "aksi": "started"}
        self._async("mcm_on", fn)
