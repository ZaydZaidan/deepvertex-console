"""peta_panel.py — panel PETA ala QGC: lintasan VIO di atas peta OSM.

Pengganti pilihan untuk jendela "Trajectory · VIO vs ground truth" (yang LAMA tetap ada).

Cara kerja:
  * Semua posisi robot datang dalam meter NED lokal (x=Utara, y=Timur) dari topik yang sama
    dengan jendela lama; panel ini mengubahnya ke lat/lon memakai ANCHOR (menu Operator →
    "Set anchor peta..."; sekarang manual, nanti dari GPS permukaan lewat peta_geo.anchor_dari_gps).
  * Ubin peta OpenStreetMap diunduh di thread terpisah (tanpa API key) dan disimpan di
    cache ~/.cache/rovgui_peta sehingga tampilan berikutnya langsung muncul tanpa jaringan.
  * Ketentuan OSM dihormati: User-Agent jelas + atribusi "© OpenStreetMap contributors"
    dicetak di sudut peta.
"""
import math
import os
import queue
import threading
import time

from PySide6 import QtCore, QtGui, QtWidgets

import peta_geo as G
import theme

CACHE = os.path.expanduser("~/.cache/rovgui_peta")
# Pilihan penyedia peta disimpan supaya sesi berikutnya tidak perlu dipilih ulang.
PENYEDIA_JSON = os.path.expanduser("~/.config/rovgui/peta_penyedia.json")
SUMBER_JSON = os.path.expanduser("~/.config/rovgui/peta_sumber.json")

# PENYEDIA PETA (26 Sep). Cara kerja sama seperti QGC: ubin 256x256 diunduh sesuai area
# yang terlihat + zoom, lalu disimpan PERMANEN di cache sehingga kunjungan berikutnya
# (dan pemakaian offline) langsung tampil. Tidak ada unduhan "seluruh dunia".
# Daftar penyedia ubin dipindah ke modul terpisah (peta_penyedia.py) yang disusun seperti
# QGC: penyedia + jenis, lengkap dengan batas zoom nyata dan catatan ketentuan layanan.
try:
    from peta_penyedia import PENYEDIA_DAFTAR as PENYEDIA
except Exception:                                          # noqa: BLE001
    PENYEDIA = {
        "Satelit (Esri World Imagery)": {
            "url": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/"
                    "MapServer/tile/{z}/{y}/{x}"),
            "attr": "Esri World Imagery", "sub": "satelit", "z_maks": 18, "grup": "Esri",
        },
    }
UA = "RovGuiVIO/1.0 (riset VIO BlueROV2; kontak zaydzihnizaidan@gmail.com)"


class PengunduhUbin(QtCore.QThread):
    """Unduh ubin yang belum ada di cache, satu per satu, di thread sendiri."""
    selesai = QtCore.Signal()
    # DIPANCARKAN SETIAP UBIN SELESAI: tanpa ini peta baru di-refresh saat SELURUH antrean
    # habis, sehingga penambahan ubin terlihat "sekali-kali" (terasa lambat seperti aplikasi
    # peta jadul). Dengan sinyal ini tiap ubin langsung tampil begitu tiba (pola QGC/GMaps).
    ubin_baru = QtCore.Signal(int, int, int)

    JUMLAH_WORKER = 8          # unduhan PARALEL (naik 4 -> 8): terukur ~3-9 s/ubin bila serial sehingga
                               # peta terasa kosong lama (lapisan kasar pun baru di detik 40);
                               # 4 koneksi masih sopan untuk kebijakan ubin OSM.

    def __init__(self, parent=None):
        super().__init__(parent)
        self.q = queue.Queue()
        self._stop = False
        self._sibuk = 0
        self._kunci = threading.Lock()

    def minta(self, url, path):
        """Antrekan satu ubin: (URL, tujuan berkas). Path ditentukan panel (per penyedia)."""
        self.q.put((url, path))

    def hentikan(self):
        """Hentikan semua worker dengan rapi (dipanggil GUI saat keluar)."""
        self._stop = True
        for _ in range(PengunduhUbin.JUMLAH_WORKER):
            self.q.put((None, None))     # bangunkan worker yang sedang menunggu

    def run(self):
        """Satu worker: ambil tugas dari antrean sampai antrean kosong."""
        import urllib.request
        os.makedirs(CACHE, exist_ok=True)
        while not self._stop:
            try:
                url, path = self.q.get(timeout=0.5)
            except queue.Empty:
                with self._kunci:
                    if self._sibuk == 0:
                        self.selesai.emit()
                continue
            with self._kunci:
                self._sibuk += 1
            if not url or not path:          # penanda berhenti dari hentikan()
                with self._kunci:
                    self._sibuk -= 1
                continue
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=10) as r:
                    data = r.read()
                with open(path, "wb") as f:
                    f.write(data)
                # kirim identitas ubin: (z,x,y) diambil dari nama berkas cache
                try:
                    nm = os.path.basename(path).replace(".png", "").split("_")
                    self.ubin_baru.emit(int(nm[0]), int(nm[1]), int(nm[2]))
                except (ValueError, IndexError):
                    self.ubin_baru.emit(0, 0, 0)
            except Exception:                                  # noqa: BLE001
                try:                                           # tandai gagal supaya tidak diulang terus
                    open(path + ".failed", "w").close()
                except OSError:
                    pass
            finally:
                with self._kunci:
                    self._sibuk -= 1


class PetaPanel(QtWidgets.QWidget):
    """Kanvas peta: ubin OSM + lintasan GT/VIO + penanda robot.

    Interaksi (permintaan pemilik 26 Sep malam):
      * posisi kursor selalu ditampilkan koordinatnya (lat, lon) + jaraknya ke ROV;
      * mode PIN: sekali klik kiri di peta = menaruh anchor di titik itu, koordinatnya
        dilaporkan (sinyal `pin_dipilih`) sehingga operator bisa memakai pin ATAU mengetik
        koordinat — dua-duanya sah.
    """

    pin_dipilih = QtCore.Signal(float, float)

    def __init__(self, parent=None, anchor=None):
        super().__init__(parent)
        self.anchor = anchor or {"lat": 56.136459, "lon": -2.706819,
                                 "heading_deg": 0.0, "sumber": "manual"}
        self.gt = []            # daftar (utara, timur)
        self.vio = []
        self.rencana = None     # rencana jalur (guide) dari node misi — lihat set_rencana()
        self.pose = (0.0, 0.0, 0.0)
        self.ikuti = True
        # NAVIGASI (26 Sep, pemilik: "gimana cara geser map-nya"): dasar tampilan = pose ROV
        # (mode IKUTI) atau titik yang dipilih; ditambah GESERAN dalam meter + PENYESUAIAN
        # ZOOM. Jadi operator bisa menggeser/zoom bebas tanpa kehilangan pengikutan robot.
        self._offset_m = [0.0, 0.0]   # geseran (utara, timur) dalam meter
        self._zoom_delta = 0          # + = mundur (lebih luas), - = maju (lebih detail)
        self._pusat_bebas = None      # (lat, lon) pusat saat mode bebas
        self._seret = None            # posisi awal saat tombol kiri ditahan
        self.mode_pin = False       # True = klik berikutnya menaruh anchor di peta
        self.kursor = None          # (lat, lon) posisi kursor saat ini
        self.pin = None             # (lat, lon) pin anchor terakhir
        self._ubin = {}
        self._z_terakhir = None
        self._minta = set()
        # LAPISAN KASAR (26 Sep): unduhan ubin OSM ternyata ~9 s/ubin (terukur: 8 ubin
        # dalam 70 s) sehingga peta terasa kosong lama. Karena itu peta selalu menggambar
        # lapisan kasar (z-4) lebih dulu — beberapa ubin saja sudah menutupi seluruh bidang
        # — lalu ubin detail menimpanya begitu tiba. Juga dipakai untuk membuktikan
        # "peta sudah tampil" lewat `detik_peta_pertama`.
        # Penyedia peta aktif. BAWAAN = penyedia paling cepat (hasil ukur), dan pilihan
        # operator disimpan sehingga dipakai lagi pada sesi berikutnya.
        try:
            from peta_penyedia import BAWAAN as _BAWAAN
        except Exception:                                     # noqa: BLE001
            _BAWAAN = "OSM : Standard"
        self.provider = _BAWAAN
        # grid meter di peta: default MATI (pemilik tidak suka tampilan bergaris)
        self.tampilkan_grid = False
        # pesan status di kanvas (mis. "sumber ubin kosong")
        self.pesan = ""
        try:
            import json
            _pp = json.load(open(PENYEDIA_JSON))
            if _pp.get("penyedia") in PENYEDIA:
                self.provider = _pp["penyedia"]
        except Exception:                                     # noqa: BLE001
            pass
        self._lapisan = {}          # z -> {kunci: QImage}: lapisan progresif (pola QGC)
        self._kosong = set()        # ubin kosong (tak ada citra) -> jangan digambar
        # JEDA unduhan: saat misi autonomous berjalan, unduhan ubin DIPAUSE supaya tidak
        # berebut CPU/jaringan dengan VINS (permintaan pemilik: jangan sampai mengganggu VIO).
        # Sumber ubin sendiri (server XYZ / folder lokal / berkas .mbtiles)
        self.url_server = "http://127.0.0.1:8080/{z}/{x}/{y}.png"
        self.dir_lokal = os.path.expanduser("~/peta_ubin")
        self.berkas_mbtiles = os.path.expanduser("~/peta.mbtiles")
        try:
            import json as _j
            _sb = _j.load(open(SUMBER_JSON))
            self.url_server = _sb.get("url_server", self.url_server)
            self.dir_lokal = _sb.get("dir_lokal", self.dir_lokal)
            self.berkas_mbtiles = _sb.get("berkas_mbtiles", self.berkas_mbtiles)
        except Exception:                                     # noqa: BLE001
            pass
        self.jeda = False
        self.stat = {"dimuat": 0, "diantre": 0, "gagal": 0, "kosong": 0}
        self._minta_kasar = set()
        self._t_mulai = time.time()
        self.detik_peta_pertama = None
        self.setMinimumHeight(220)
        # Latar panel dari theme.qss() (#PetaCanvas), BUKAN gaya inline: gaya inline
        # menyimpan hex saat dibuat sehingga tertinggal bergaya lama setelah tema berganti.
        self.setObjectName("PetaCanvas")
        self.unduh = PengunduhUbin(self)
        self.unduh.selesai.connect(self.update)
        self.unduh.ubin_baru.connect(self._ubin_tiba)
        self._pekerja = []
        for _ in range(PengunduhUbin.JUMLAH_WORKER):
            t = threading.Thread(target=self.unduh.run, daemon=True)
            t.start()
            self._pekerja.append(t)
        # matikan thread saat aplikasi keluar dari jalur mana pun (kalau tidak: QThread
        # dihancurkan saat masih berjalan -> proses ABORT; terbukti pada uji GUI)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.hentikan)
        # Timer EKSPLISIT: bentuk QTimer(self, timeout=..., interval=...) tidak menjalankan
        # timer pada PyQt5 di sini (akibatnya ubin tidak pernah diunduh — terukur: 1 ubin
        # di cache setelah 6 s). Bentuk di bawah pasti jalan.
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1200)
        self._timer.timeout.connect(self._segarkan_ubin)
        self._timer.start()

    # ---------------------------------------------------------------- data
    def add_gt(self, n, e):
        self.gt.append((n, e))
        if len(self.gt) > 20000:
            del self.gt[:5000]
        if self.ikuti:
            self.update()

    def add_vio(self, n, e):
        self.vio.append((n, e))
        if len(self.vio) > 20000:
            del self.vio[:5000]
        if self.ikuti:
            self.update()

    def set_pose(self, n, e, yaw_rad):
        self.pose = (n, e, yaw_rad)

    # ---------------------------------------------------- rencana jalur (GUIDE)
    # Jalur yang DIRENCANAKAN + kotak batas area, digambar di peta SEKETIKA saat tombol
    # misi ditekan — bukan hanya setelah robot bergerak seperti jejak GT/VIO (permintaan
    # pemilik 2 Okt 2026). Sumbernya SATU: JSON `plan` dari node misi
    # (`/rov/lawnmower/plan`, latched) — node autonomous sudah menerbitkannya, dan mode
    # MAPPING MANUAL memakai node guide yang menerbitkan bentuk yang sama. Peta tidak
    # pernah menghitung ulang geometrinya sendiri supaya gambar di layar = jalur yang
    # benar-benar dipakai kendali.
    def set_rencana(self, plan):
        """Pasang rencana jalur (dict dari JSON node misi) — atau None untuk menghapusnya."""
        if not plan:
            self.bersihkan_rencana()
            return
        wp = [list(map(float, w[:2])) for w in (plan.get("wp") or [])]
        if len(wp) < 2:
            return
        rencana = {
            "wp": wp,
            "sudut": [list(map(float, s[:2])) for s in (plan.get("sudut") or [])],
            "kotak": [float(v) for v in (plan.get("kotak_m") or [])][:2],
            "sp": float(plan.get("sp") or 0.0),
            "frame": list(map(float, plan.get("frame"))) if plan.get("frame") else None,
            "start_frame": plan.get("start_frame"),
            "agl": (float(plan["agl_target"]) if plan.get("agl_target") is not None else None),
            "t": plan.get("t"),
        }
        self.rencana = rencana
        self.update()

    def bersihkan_rencana(self):
        self.rencana = None
        self.update()

    def _wp_terdekat(self):
        """Indeks waypoint TERDEKAT dari pose ROV (untuk penanda 'NEXT')."""
        rencana = getattr(self, "rencana", None)
        if not rencana:
            return None
        n, e, _ = self.pose
        terbaik, jarak = None, None
        for i, (wn, we) in enumerate(rencana["wp"]):
            d = (wn - n) ** 2 + (we - e) ** 2
            if jarak is None or d < jarak:
                terbaik, jarak = i, d
        return terbaik

    def _gambar_rencana(self, p, ke_layar):
        """Gambar jalur rencana (putus-putus) + panah arah gerak + kotak area + penanda NEXT."""
        rencana = getattr(self, "rencana", None)
        if not rencana:
            return
        wp = rencana["wp"]
        # KOTAK AREA: warna domain misi (oranye) supaya tidak tertukar dengan jejak GT/VIO.
        sudut = rencana["sudut"]
        if len(sudut) >= 3:
            pen = QtGui.QPen(QtGui.QColor(theme.ORANGE), 2.0)
            pen.setStyle(QtCore.Qt.DashLine)
            pen.setDashPattern([7, 5])
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            jalan = QtGui.QPainterPath()
            x, y = ke_layar(*sudut[0])
            jalan.moveTo(x, y)
            for s in sudut[1:]:
                x, y = ke_layar(*s)
                jalan.lineTo(x, y)
            jalan.closeSubpath()
            p.drawPath(jalan)
            if len(rencana["kotak"]) == 2:
                x, y = ke_layar(sudut[0][0], sudut[0][1])
                p.setPen(QtGui.QPen(QtGui.QColor(theme.ORANGE)))
                f = p.font(); f.setPointSize(theme.px(8)); p.setFont(f)
                p.drawText(int(x) + 6, int(y) - 6, "AREA %.1f x %.1f m"
                           % (rencana["kotak"][0], rencana["kotak"][1]))
        # JALUR RENCANA: garis putus-putus (konvensi yang sama dengan plot pasca-misi).
        pen = QtGui.QPen(QtGui.QColor(theme.LAGOON), 2.6)
        pen.setStyle(QtCore.Qt.DashLine)
        pen.setDashPattern([6, 4])
        p.setPen(pen)
        jalan = QtGui.QPainterPath()
        x, y = ke_layar(*wp[0])
        jalan.moveTo(x, y)
        for w in wp[1:]:
            x, y = ke_layar(*w)
            jalan.lineTo(x, y)
        p.drawPath(jalan)
        # PANAH ARAH GERAK: satu panah di tiap lajur (pasangan waypoint), jadi pilot melihat
        # arah yang harus ditempuh, bukan hanya bentuk jalurnya.
        p.setPen(QtGui.QPen(QtGui.QColor(theme.LAGOON), 2.2))
        p.setBrush(QtGui.QBrush(QtGui.QColor(theme.LAGOON)))
        for i in range(1, len(wp) - 1, 2):
            (ax, ay) = ke_layar(*wp[i])
            (bx, by) = ke_layar(*wp[i + 1])
            panjang = math.hypot(bx - ax, by - ay)
            if panjang < 24:                    # terlalu pendek untuk dibaca -> lewati
                continue
            fx, fy = (bx - ax) / panjang, (by - ay) / panjang
            mx, my = ax + 0.55 * (bx - ax), ay + 0.55 * (by - ay)
            ukuran = 9.0
            kiri = (mx - ukuran * fx + ukuran * 0.55 * (-fy),
                    my - ukuran * fy + ukuran * 0.55 * fx)
            kanan = (mx - ukuran * fx - ukuran * 0.55 * (-fy),
                     my - ukuran * fy - ukuran * 0.55 * fx)
            segitiga = QtGui.QPolygonF([QtCore.QPointF(mx, my),
                                        QtCore.QPointF(*kiri), QtCore.QPointF(*kanan)])
            p.drawPolygon(segitiga)
        p.setBrush(QtCore.Qt.NoBrush)
        # PENANDA WAYPOINT BERIKUTNYA: titik tujuan terdekat, plus keterangan jaraknya.
        idx = self._wp_terdekat()
        if idx is not None:
            tn, te = wp[idx]
            tx, ty = ke_layar(tn, te)
            p.setPen(QtGui.QPen(QtGui.QColor(theme.OK), 2.2))
            p.setBrush(QtGui.QBrush(QtGui.QColor(theme.OK)))
            p.drawEllipse(QtCore.QPointF(tx, ty), 5.5, 5.5)
            p.setBrush(QtCore.Qt.NoBrush)
            n, e, _ = self.pose
            p.setPen(QtGui.QPen(QtGui.QColor(theme.OK)))
            p.drawText(int(tx) + 9, int(ty) - 9, "NEXT #%d/%d · %.1f m"
                       % (idx + 1, len(wp), math.hypot(tn - n, te - e)))

    def bersihkan(self):
        self.gt, self.vio, self.pose = [], [], (0.0, 0.0, 0.0)
        self.update()

    def set_anchor(self, anchor):
        self.anchor = dict(anchor)
        # tampilkan IKON ANCHOR (model BlueROV) tepat di titik anchor — seperti penanda home
        # di QGC — supaya operator melihat titik awal lintasan di peta.
        self.pin = (self.anchor["lat"], self.anchor["lon"])
        self._minta_kasar.clear()      # paksa lapisan pendahulu untuk lokasi baru
        self._lapisan.clear()
        self.detik_peta_pertama = None
        self._t_mulai = time.time()
        self._ubin.clear()
        self._minta.clear()
        self.update()

    # ------------------------------------------------------------- tampilan
    def _bidang(self):
        """(lat, lon, z, lebar, tinggi) yang akan digambar."""
        lebar, tinggi = max(200, self.width()), max(120, self.height())
        if self.ikuti:
            n, e, _ = self.pose
            lat, lon = G.ned_ke_latlon(n + self._offset_m[0], e + self._offset_m[1], self.anchor)
        elif self._pusat_bebas is not None:
            lat, lon = self._pusat_bebas
        else:
            titik = self.gt + self.vio or [(0.0, 0.0)]
            lat, lon = G.ned_ke_latlon(sum(p[0] for p in titik) / len(titik),
                                       sum(p[1] for p in titik) / len(titik), self.anchor)
        # rentang: cukup memuat seluruh lintasan (minimal 60 m)
        sem = self.gt + self.vio
        # Rentang minimal 60 m dulu membuat misi 10x10 m tampak seperti garis tipis; sekarang
        # minimal 8 m sehingga survey kecil mengisi layar.
        span = 8.0
        if len(sem) > 1:
            span = max(8.0, 2.2 * max(max(p[0] for p in sem) - min(p[0] for p in sem),
                                      max(p[1] for p in sem) - min(p[1] for p in sem)))
        z = max(1, min(22, G.zoom_untuk_span(span, lat, lebar)
                         + int(self._zoom_delta)))
        # PENTING (26 Sep, keluhan "misi 10x10 m cuma tampak garis"): zoom TAMPILAN tidak lagi
        # dibatasi penyedia. Kalau pemilik zoom lebih dalam daripada citra yang tersedia, panel
        # tetap memperbesar UBIN ZOOM TERDALAM yang benar-benar punya citra — persis perilaku
        # QGC/GMaps. Dibatasinya z di sini dulu membuat bidang jadi 523 m sehingga lintasan
        # 10 m hanya 17 piksel; sekarang bidang mengikuti lintasan dan latarnya diperbesar.
        self._z_ubin = min(z, int(self._info().get("z_maks", 19)))   # zoom ubin yang diambil
        # ZOOM-MUNDUR otomatis: OSM tidak punya ubin pada semua zoom di semua tempat
        # (terukur: Ubud z=18/19 kosong 103 byte, London penuh). Kalau ubin yang termuat
        # mayoritas kosong, turunkan zoom sampai ada isi.
        z = max(1, z - getattr(self, "_zoom_mundur", 0))
        return lat, lon, z, lebar, tinggi

    # ------------------------------------------------- penyedia peta & ubin
    def _info(self):
        tabel = PENYEDIA or {}
        bawaan = next(iter(tabel.values()),
                      {"url": "", "attr": "", "sub": "", "z_maks": 19, "sumber": "http"})
        info = dict(tabel.get(getattr(self, "provider", None), bawaan))
        if info.get("sub") == "server":
            info["url"] = getattr(self, "url_server", info["url"])
        return info

    def _path_ubin(self, z, x, y):
        info = self._info()
        if info.get("sumber") == "lokal":
            # folder milik operator: dukung tata letak XYZ standar (z/x/y.png) maupun
            # nama datar (z_x_y.png) supaya folder apa pun bisa langsung dipakai.
            for kandidat in (os.path.join(getattr(self, "dir_lokal", ""), str(z), str(x),
                                          "%d.png" % y),
                             os.path.join(getattr(self, "dir_lokal", ""),
                                          "%d_%d_%d.png" % (z, x, y))):
                if os.path.exists(kandidat):
                    return kandidat
            return os.path.join(getattr(self, "dir_lokal", ""), str(z), str(x), "%d.png" % y)
        d = os.path.join(CACHE, info["sub"]) if info["sub"] else CACHE
        return os.path.join(d, "%d_%d_%d.png" % (z, x, y))

    def _sumber_aktif(self):
        return self._info().get("sumber", "http")

    def _ambil_mbtiles(self, z, x, y):
        """Ambil satu ubin dari berkas .mbtiles (SQLite) -> simpan ke cache. Kembalikan bool."""
        import sqlite3
        b = getattr(self, "berkas_mbtiles", "")
        if not b or not os.path.exists(b):
            return False
        try:
            with sqlite3.connect("file:%s?mode=ro" % b, uri=True) as db:
                r = db.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND "
                               "tile_column=? AND tile_row=?", (z, x, (2 ** z - 1) - y)).fetchone()
            if not r or not r[0]:
                return False
            p = os.path.join(CACHE, "mbtiles", "%d_%d_%d.png" % (z, x, y))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(r[0])
            return True
        except Exception:                                     # noqa: BLE001
            return False

    def set_sumber(self, url_server=None, dir_lokal=None, berkas_mbtiles=None):
        """Atur sumber ubin sendiri (server XYZ / folder lokal / .mbtiles) + simpan."""
        if url_server:
            self.url_server = url_server
        if dir_lokal:
            self.dir_lokal = os.path.expanduser(dir_lokal)
        if berkas_mbtiles:
            self.berkas_mbtiles = os.path.expanduser(berkas_mbtiles)
        try:
            import json as _j
            os.makedirs(os.path.dirname(SUMBER_JSON), exist_ok=True)
            _j.dump({"url_server": self.url_server, "dir_lokal": self.dir_lokal,
                     "berkas_mbtiles": self.berkas_mbtiles}, open(SUMBER_JSON, "w"), indent=1)
        except OSError:
            pass
        self._lapisan.clear(); self._ubin.clear(); self._kosong.clear()
        self._minta.clear(); self._minta_kasar.clear()
        self.detik_peta_pertama = None; self._t_mulai = time.time()
        self.update()

    def _url_ubin(self, z, x, y):
        info = self._info()
        s = "abc"[(x + y) % 3] if "{s}" in info["url"] else ""
        return info["url"].format(z=z, x=x, y=y, s=s)

    def set_provider(self, nama, simpan=True):
        """Ganti penyedia peta (ubin dicache terpisah per penyedia, seperti QGC)."""
        if nama not in PENYEDIA:
            return False
        self.provider = nama
        if simpan:
            try:
                import json
                os.makedirs(os.path.dirname(PENYEDIA_JSON), exist_ok=True)
                json.dump({"penyedia": nama}, open(PENYEDIA_JSON, "w"))
            except OSError:
                pass
        self._lapisan.clear()
        self._minta_kasar.clear()
        self._minta.clear()
        self.detik_peta_pertama = None
        self._t_mulai = time.time()
        self.update()
        return True

    def unduh_area(self, z_min, z_max, radius_m=400.0):
        """Unduh ubin area sekitar pusat pandangan untuk PEMAKAIAN OFFLINE (seperti QGC).

        Mengembalikan jumlah ubin yang diantrekan. Radius default 400 m.
        """
        lat, lon, _ = self._bidang()[:3]
        diantre = 0
        for z in range(max(2, int(z_min)), int(z_max) + 1):
            dlat = radius_m / 111320.0
            dlon = radius_m / (111320.0 * max(0.05, math.cos(math.radians(lat))))
            n = 2 ** z
            for (la, lo) in ((lat - dlat, lon - dlon), (lat + dlat, lon + dlon)):
                px, py = G.lonlat_ke_piksel_global(la, lo, z)
                tx, ty = int(px // 256), int(py // 256)
                if not hasattr(self, "_kotak_area"):
                    self._kotak_area = {}
                k = (z, tx, ty)
                self._kotak_area.setdefault(z, set()).add((tx, ty))
            xs = sorted({t[0] for t in self._kotak_area.get(z, set())})
            ys = sorted({t[1] for t in self._kotak_area.get(z, set())})
            if not xs or not ys:
                continue
            for tx in range(min(xs), max(xs) + 1):
                for ty in range(min(ys), max(ys) + 1):
                    p = self._path_ubin(z, tx % n, ty)
                    if os.path.exists(p) or os.path.exists(p + ".failed"):
                        continue
                    self.unduh.minta(self._url_ubin(z, tx % n, ty), p)
                    diantre += 1
        return diantre

    @staticmethod
    def _ukuran_ubin(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _zoom_lapisan(self, z):
        """Zoom lapisan pendahulu, dari paling kasar ke paling halus (pola QGC).

        QGC terasa instan karena langsung menggambar peta paling kasar (beberapa ubin saja)
        lalu memperhalus. Diukur: 1 ubin perlu ~3-9 s, jadi melapisi zoom-8/-5/-2 membuat
        peta tampil setelah ubin PERTAMA tiba, bukan setelah seluruh bidang terunduh.
        """
        # Lapisan paling kasar (z-14, mis. z=4) hanya 1-2 ubin untuk SELURUH layar ->
        # peta muncul <1 detik; lalu z-8, z-5, z-2 memperhalus, ubin zoom penuh menyusul.
        kasar = [max(2, z - 14), max(2, z - 8), max(2, z - 5), max(2, z - 2)]
        hasil = []
        for zz in kasar:
            if zz < z and zz not in hasil:
                hasil.append(zz)
        return hasil

    def _ubin_tiba(self, z, x, y):
        """Satu ubin selesai diunduh -> LANGSUNG masuk tampilan.

        Sebelumnya ubin hanya dimuat saat pemindaian zoom yang sedang tampil, sehingga kalau
        zoom bergeser (atau lapisan kasar belum lengkap) ubin yang sudah terunduh tidak muncul
        — peta terasa lambat padahal berkasnya sudah ada (terukur: 17 ubin terunduh tetapi
        0 yang tampil). Sekarang ubin yang tiba langsung dipasang ke lapisan/detail.
        """
        if z:
            p = self._path_ubin(z, x, y)
            if os.path.exists(p) and self._ukuran_ubin(p) >= 400:
                img = QtGui.QImage(p)
                if not img.isNull():
                    if z in self._lapisan:
                        self._lapisan[z][(z, x, y)] = img
                    else:
                        self._ubin[(z, x, y)] = img
        self._segarkan_ubin()
        self.update()

    def _segarkan_ubin(self):
        lat, lon, z, lebar, tinggi = self._bidang()
        # zb = zoom ubin yang BENAR-BENAR diambil (<= citra penyedia). Layar boleh lebih
        # dalam; ubin zb digambar diperbesar 2^(z-zb). Enumerasi ubin memakai ruang zb.
        zb = int(getattr(self, "_z_ubin", z))
        fb = 2 ** max(0, z - zb)
        # lapisan pendahulu: kasar -> halus -> (ubin zoom penuh menyusul di bawah ini)
        perlu_kasar = False
        for zk in self._zoom_lapisan(zb):   # lapisan TIDAK boleh melebihi zoom ubin zb
            # PENTING: ukuran bidang untuk lapisan kasar harus DISKALAKAN (lebar/2^(z-zk)).
            # Sebelumnya dipakai lebar zoom penuh sehingga lapisan z=4 meminta 12 ubin untuk
            # area 68 derajat padahal yang terlihat hanya sebagian kecil -> puluhan ubin
            # terbuang dan peta terasa lambat.
            faktor = 2 ** (z - zk)
            ubk, _, _ = G.ubin_untuk_bidang(lat, lon, zk, max(1, lebar // faktor),
                                            max(1, tinggi // faktor))
            lap = self._lapisan.setdefault(zk, {})
            for (zz, tx, ty, px, py) in ubk:
                kunci = (zz, tx, ty)
                p = self._path_ubin(*kunci)
                if os.path.exists(p) and self._ukuran_ubin(p) < 400:
                    # UBIN KOSONG (penyedia tak punya citra di sini). Jangan digambar: kalau
                    # digambar, kotak gelapnya MENUTUPI lapisan kasar di bawahnya sehingga
                    # peta tampak berlubang (terukur pada citra Esri di Kepulauan Seribu).
                    self._kosong.add(kunci)
                    continue
                if os.path.exists(p):
                    if kunci not in lap:
                        img = QtGui.QImage(p)
                        if not img.isNull():
                            lap[kunci] = img
                            perlu_kasar = True
                elif self._sumber_aktif() == "mbtiles":
                    if kunci not in self._minta_kasar and self._ambil_mbtiles(zz, tx, ty):
                        self._minta_kasar.add(kunci)
                        perlu_kasar = True            # dibaca lokal, tanpa jaringan
                elif self._sumber_aktif() == "lokal":
                    continue                          # folder lokal: tidak ada yang diunduh
                elif kunci not in self._minta_kasar and not self.jeda:
                    self._minta_kasar.add(kunci)
                    self.unduh.minta(self._url_ubin(zz, tx, ty), self._path_ubin(zz, tx, ty))
        ub, x0, y0 = G.ubin_untuk_bidang(lat, lon, zb, max(1, lebar // fb),
                                        max(1, tinggi // fb))
        self._posisi = (x0, y0, zb)
        perlu = False
        for (zz, tx, ty, px, py) in ub:
            kunci = (zz, tx, ty)
            if kunci in self._minta:
                continue
            p = self._path_ubin(*kunci)
            if os.path.exists(p):
                if self._ukuran_ubin(p) < 400:
                    # ubin KOSONG: jangan dimuat/digambar supaya lapisan kasar di bawahnya
                    # tetap terlihat (kalau digambar, kotak gelap menutupi peta)
                    self._kosong.add(kunci)
                else:
                    img = QtGui.QImage(p)
                    if not img.isNull():
                        self._ubin[kunci] = img
                        perlu = True
            elif self._sumber_aktif() == "mbtiles":
                if kunci not in self._minta and self._ambil_mbtiles(zz, tx, ty):
                    self._minta.add(kunci)
                    perlu = True
            elif self._sumber_aktif() == "lokal":
                continue                              # folder lokal: tidak ada yang diunduh
            elif not os.path.exists(p + ".failed") and not self.jeda:
                self._minta.add(kunci)
                self.unduh.minta(self._url_ubin(zz, tx, ty), self._path_ubin(zz, tx, ty))
                self.stat["diantre"] += 1
        # deteksi ubin kosong -> turunkan zoom (maks 5 langkah) supaya peta tidak polos
        total = kosong = 0
        for (zz, tx, ty, px, py) in ub:
            p = self._path_ubin(zz, tx, ty)          # ikut penyedia yang aktif
            if os.path.exists(p):
                total += 1
                if os.path.getsize(p) < 400:
                    kosong += 1
        # Ubin yang DITOLAK penyedia (mis. server sendiri menjawab 204 di atas batas citranya)
        # dihitung sebagai "tidak tersedia" juga, supaya zoom-mundur otomatis turun ke zoom
        # terdalam yang ada — bukan berhenti di zoom yang tak punya citra.
        gagal_zoom = sum(1 for (zz, tx, ty, px, py) in ub
                         if os.path.exists(self._path_ubin(zz, tx, ty) + ".failed"))
        total2 = total + gagal_zoom
        kosong2 = kosong + gagal_zoom
        if total2 >= 4 and kosong2 / max(1, total2) > 0.7 and getattr(self, "_zoom_mundur", 0) < 5:
            self._zoom_mundur = getattr(self, "_zoom_mundur", 0) + 1
            self._minta.clear()
            print("map: empty tiles at this zoom -> zooming out to-%d" % self._zoom_mundur)
        if perlu or perlu_kasar:
            # perlu_kasar WAJIB: tanpa ini lapisan kasar termuat ke memori tetapi tidak
            # pernah digambar sampai ubin DETAIL tiba (terukur: peta kasar baru muncul
            # pada detik 40 padahal ubinnya sudah ada).
            self.update()

        # pesan jelas bila sumber ubin sendiri tidak berisi apa-apa (mis. folder lokal kosong
        # atau server yang sudah tidak jalan) — supaya peta tidak tampak "kosong tanpa sebab".
        self.pesan = ""
        if self._sumber_aktif() in ("lokal", "mbtiles") and not self._lapisan and not self._ubin:
            d = getattr(self, "dir_lokal", "")
            b = getattr(self, "berkas_mbtiles", "")
            self.pesan = ("empty tile source (%s) — pick another provider in the "
                          "Operator ▸ Map: map provider menu"
                          % ("folder %s" % d if self._sumber_aktif() == "lokal" else "file %s" % b))

    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        # Ubin yang diperbesar (overzoom) digambar HALUS, bukan piksel-kotak: permintaan
        # pemilik "Esri pecah-pecah gridnya, saya mau yang clean".
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QtGui.QColor(theme.PANEL))
        lat, lon, z, lebar, tinggi = self._bidang()
        # zb = zoom UBIN sesungguhnya (dibatasi citra yang tersedia pada penyedia). Layar
        # boleh lebih dalam daripada zb: ubin zb digambar DIPERBESAR 2^(z-zb) — persis cara
        # QGC/GMaps. Semua enumerasi piksel (lapisan, lintasan, grid, kursor) memakai ruang zb
        # supaya geometri dan konversi koordinat konsisten.
        zb = int(getattr(self, "_z_ubin", z))
        fb = 2 ** max(0, z - zb)
        ub, x0, y0 = G.ubin_untuk_bidang(lat, lon, zb, max(1, lebar // fb),
                                         max(1, tinggi // fb))
        # gambar lapisan pendahulu dari paling kasar -> halus (pola QGC)
        for zk in sorted(self._lapisan):
            if zk > zb:
                continue          # lapisan lebih dalam dari citra yang ada -> jangan digambar
            lap = self._lapisan[zk]
            if not lap:
                continue
            faktor = 2 ** (z - zk)
            ubk, _, _ = G.ubin_untuk_bidang(lat, lon, zk, max(1, lebar // faktor),
                                            max(1, tinggi // faktor))
            for (zz, tx, ty, px, py) in ubk:
                if (zz, tx, ty) in self._kosong:
                    continue                      # biarkan lapisan kasar di bawah terlihat
                img = lap.get((zz, tx, ty))
                if img is not None:
                    p.drawImage(QtCore.QRectF(px, py, 256 * faktor, 256 * faktor), img)
            if self.detik_peta_pertama is None:
                self.detik_peta_pertama = time.time() - self._t_mulai
                print("map: first display at %.1f s (zoom layer %d)"
                      % (self.detik_peta_pertama, zk))
        self._posisi = (x0, y0, zb)
        for (zz, tx, ty, px, py) in ub:
            img = self._ubin.get((zz, tx, ty))
            if img is not None:
                p.drawImage(QtCore.QRectF(px, py, 256 * fb, 256 * fb), img)

        def ke_layar(n, e):
            # ruang piksel ubin (zb) DIKALIKAN faktor perbesaran fb: tampilan boleh lebih
            # dalam daripada ubin, dan ubin digambar diperbesar 2^(z-zb). Tanpa perkalian ini
            # overlay (lintasan/ikon/grid) tergambar di sudut kiri-atas (terukur: ikon di
            # (31,23), bukan di tengah) setiap kali zoom tampilan > zoom ubin.
            lat, lon = G.ned_ke_latlon(n, e, self.anchor)
            gx, gy = G.lonlat_ke_piksel_global(lat, lon, zb)
            return (gx - x0) * fb, (gy - y0) * fb

        # GARIS PETUNJUK (guide) — jalur rencana + kotak area, digambar SEBELUM jejak
        # GT/VIO supaya jejak tetap paling atas (permintaan pemilik 2 Okt 2026).
        self._gambar_rencana(p, ke_layar)

        # garis dipertebal (26 Sep): di latar citra satelit yang gelap, garis 1,6 px nyaris
        # tak terlihat pada misi kecil; 2,4 px terbaca jelas.
        for jalur, warna, lebar_garis in ((self.gt, QtGui.QColor(60, 60, 60), 2.4),
                                          (self.vio, QtGui.QColor(255, 70, 70), 2.4)):
            if len(jalur) < 2:
                continue
            jalur_pen = QtGui.QPainterPath()
            x, y = ke_layar(*jalur[0])
            jalur_pen.moveTo(x, y)
            for n, e in jalur[1:]:
                x, y = ke_layar(n, e)
                jalur_pen.lineTo(x, y)
            p.setPen(QtGui.QPen(warna, lebar_garis))
            p.drawPath(jalur_pen)

        n, e, yaw = self.pose
        x, y = ke_layar(n, e)
        # PENANDA ROBOT: PANAH SEDERHANA (permintaan pemilik 13 Okt: "tanda anchor dan
        # penanda robotnya kayak arrow biasa aja, gausa pakai desain kayak sekarang").
        # Segitiga polos yang diputar mengikuti arah hadap — tanpa ikon/gambar.
        # (panah robot digambar SETELAH anchor — lihat di bawah — supaya saat robot
        #  berada tepat di titik anchor keduanya tetap terlihat, panah di atas dot)

        # GRID SKALA: DEFAULT MATI (permintaan pemilik: "Esri pecah-pecah gridnya saya gasuka").
        # Dinyalakan lewat menu bila memang ingin mengukur jarak di peta.
        mpx = G.skala_m_per_piksel(self.anchor["lat"], z)
        langkah = 0.0
        if self.tampilkan_grid:
            for kandidat in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
                langkah = float(kandidat)
                if langkah * 40 >= mpx * 60:      # grid tidak lebih rapat dari ~60 px
                    break
            n, e, _ = self.pose
            pusat = self.kursor if self.kursor else G.ned_ke_latlon(n, e, self.anchor)
            pcx, pcy = G.lonlat_ke_piksel_global(pusat[0], pusat[1], zb)
            cxp, cyp = (pcx - x0) * fb, (pcy - y0) * fb
            jarak_piksel = (langkah / mpx) * fb        # jarak grid juga diskalakan
            p.setPen(QtGui.QPen(QtGui.QColor(120, 140, 150, 90), 1))
            jumlah_sisi = int(max(self.width(), self.height()) / max(1.0, jarak_piksel)) // 2 + 1
            for i in range(-jumlah_sisi, jumlah_sisi + 1):
                if abs(i) > 400:
                    break
                xx = cxp + i * jarak_piksel
                yy = cyp + i * jarak_piksel
                p.drawLine(int(xx), 0, int(xx), self.height())
                p.drawLine(0, int(yy), self.width(), int(yy))
            if jarak_piksel > 20:
                p.setPen(QtGui.QPen(QtGui.QColor(150, 170, 180), 1))
                p.drawText(int(cxp + jarak_piksel) + 2, int(cyp) - 3, "%g m" % langkah)
        # keterangan: anchor, skala, atribusi OSM
        p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT)))
        f = p.font(); f.setPointSize(theme.px(8)); p.setFont(f)
        # teks pendek & langsung (permintaan pemilik); detail di tooltip
        p.drawText(8, 14, "z%d · %.2f m/px" % (z, mpx))
        try:
            self.setToolTip("anchor %.5f, %.5f (%s) · zoom %d · %.2f m/px"
                            % (self.anchor["lat"], self.anchor["lon"],
                               self.anchor.get("sumber", "manual"), z, mpx))
        except Exception:                          # noqa: BLE001
            pass
        # (legenda panjang & status tiles DIHAPUS — info sekunder; lihat tooltip peta)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT)))
        # koordinat di bawah kursor + jarak (permintaan pemilik: "kalau saya pin di map
        # interaktif juga beri tahu saya informasi lokasinya koordinatnya")
        if self.pesan:
            p.setPen(QtGui.QPen(QtGui.QColor(255, 196, 0)))
            p.drawText(8, 70, self.pesan)
            p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT)))
        if self.kursor is not None:
            n, e, _ = self.pose
            latk, lonk = self.kursor
            nk, ek = G.latlon_ke_ned(latk, lonk, self.anchor)
            p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT)))
            p.drawText(8, 28, "cur %.5f, %.5f · %.0f/%.0f m"
                       % (latk, lonk, math.hypot(nk - n, ek - e), math.hypot(nk, ek)))
            p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT)))
        if self.mode_pin:
            p.setPen(QtGui.QPen(QtGui.QColor(255, 196, 0)))
            p.drawText(8, 42, "PIN MODE — click map to place anchor")
            p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT)))
        if self.pin is not None:
            # PENANDA ANCHOR sederhana (permintaan pemilik 13 Okt): target kecil —
            # cincin + crosshair + titik, tanpa ikon gambar.
            gxp, gyp = G.lonlat_ke_piksel_global(self.pin[0], self.pin[1],
                                                 getattr(self, "_posisi", (0, 0, 12))[2])
            px, py = (gxp - self._posisi[0]) * fb, (gyp - self._posisi[1]) * fb
            try:
                # ANCHOR = dot sederhana (permintaan pemilik 13 Okt): cincin + titik.
                p.setPen(QtGui.QPen(QtGui.QColor(0, 229, 255), 1.6))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawEllipse(QtCore.QPointF(px, py), 8.0, 8.0)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QColor(0, 229, 255))
                p.drawEllipse(QtCore.QPointF(px, py), 4.0, 4.0)
                p.setPen(QtGui.QPen(QtGui.QColor(0, 220, 255)))
                p.drawText(int(px) + 14, int(py) - 12, "%.5f, %.5f" % self.pin)
            except Exception:                      # noqa: BLE001
                pass
        # PENANDA ROBOT: panah polos, DIGAMBAR TERAKHIR (di atas anchor dot).
        try:
            p.save()
            p.translate(x, y)
            p.rotate(math.degrees(yaw))          # yaw 0 = menghadap utara (atas peta)
            p.setPen(QtGui.QPen(QtGui.QColor(20, 23, 26), 1.4))
            p.setBrush(QtGui.QColor(241, 237, 230))
            p.drawPolygon(QtGui.QPolygonF([
                QtCore.QPointF(0, -9), QtCore.QPointF(-6, 7),
                QtCore.QPointF(0, 3.5), QtCore.QPointF(6, 7)]))
            p.restore()
        except Exception:                          # noqa: BLE001
            pass
        p.drawText(self.width() - 260, self.height() - 6, self._info()["attr"])
        p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT), 2))
        panjang = 50
        p.drawLine(12, self.height() - 14, 12 + panjang, self.height() - 14)
        p.drawText(12, self.height() - 18, "%.1f m" % (panjang * mpx))
        p.end()

    def hentikan(self):
        """Dipanggil GUI saat ditutup supaya thread unduhan tidak menghancurkan proses."""
        try:
            self.unduh.hentikan()
        except Exception:                                     # noqa: BLE001
            pass
        for t in getattr(self, "_pekerja", []):
            try:
                t.join(timeout=1.0)
            except Exception:                                 # noqa: BLE001
                pass

    def _layar_ke_latlon(self, mx, my):
        """Piksel widget -> (lat, lon) memakai bidang yang terakhir digambar."""
        x0, y0, z = getattr(self, "_posisi", (0.0, 0.0, 12))
        return G.piksel_global_ke_lonlat(x0 + mx, y0 + my, z)

    def showEvent(self, ev):
        """Saat tab peta ini DIBUKA, paksa muat + gambar.

        Penting (keluhan pemilik): saat tampilan pertama GUI adalah tab peta QML, panel ini
        belum pernah melukis sehingga `_posisi` belum diisi dan peta tampak kosong. Dengan ini
        panel langsung menyegarkan diri begitu tabnya dipilih.
        """
        super().showEvent(ev)
        try:
            self._segarkan_ubin()
        except Exception:                                     # noqa: BLE001
            pass
        self.update()

    def mouseMoveEvent(self, ev):
        self.kursor = self._layar_ke_latlon(ev.x(), ev.y())
        if self._seret is not None:                     # menyeret peta = geser pandangan
            dx = ev.x() - self._seret[0]
            dy = ev.y() - self._seret[1]
            self._seret = (ev.x(), ev.y())
            self._geser_meter(dx, dy)
        self.update()

    def mouseReleaseEvent(self, ev):
        self._seret = None

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.MiddleButton:
            self._seret = (ev.x(), ev.y())              # klik-tengah: geser
            return
        if ev.button() != QtCore.Qt.LeftButton:
            return
        if not self.mode_pin:
            self._seret = (ev.x(), ev.y())              # tahan-kiri: geser peta
            return
        lat, lon = self._layar_ke_latlon(ev.x(), ev.y())
        self.kursor = (lat, lon)
        if self.mode_pin:
            self.pin = (lat, lon)
            self.mode_pin = False
            self.unsetCursor()
            self.pin_dipilih.emit(lat, lon)      # -> GUI melaporkan koordinatnya
        self.update()

    # ------------------------------------------------------- navigasi (geser/zoom)
    def pergi_ke(self, lat, lon, sumber="manual"):
        """Pusatkan pandangan ke koordinat tertentu TANPA mengubah anchor lintasan."""
        self._pusat_bebas = (float(lat), float(lon))
        self.ikuti = False
        self._offset_m = [0.0, 0.0]
        self._zoom_delta = 0
        self._lapisan.clear()
        self._minta_kasar.clear()
        self.detik_peta_pertama = None
        self._t_mulai = time.time()
        self.update()

    def pusatkan_ke_robot(self):
        """Kembali ke mode IKUTI (pandangan mengikuti robot, geseran dinolkan)."""
        self.ikuti = True
        self._pusat_bebas = None
        self._offset_m = [0.0, 0.0]
        self._zoom_delta = 0
        self.update()

    def ekspor_xyz(self, folder):
        """Salin ubin area yang sedang tampil ke `folder` dengan tata letak XYZ (z/x/y.png).

        Dipakai supaya operator bisa MENARUH ubin di server sendiri (nginx/tileserver) atau
        memakainya lewat penyedia "Folder lokal". Kembalikan jumlah berkas tersalin.
        """
        lat, lon, z, lebar, tinggi = self._bidang()
        daftar = []
        for zz in self._zoom_lapisan(z) + [z]:
            faktor = 2 ** max(0, z - zz)
            ub, _, _ = G.ubin_untuk_bidang(lat, lon, zz, max(1, lebar // faktor),
                                           max(1, tinggi // faktor))
            daftar += [(zz, tx, ty) for (zz, tx, ty, _, _) in ub]
        n = 0
        for (zz, tx, ty) in daftar:
            src = self._path_ubin(zz, tx, ty)
            if not os.path.exists(src) or self._ukuran_ubin(src) < 100:
                continue
            dst = os.path.join(os.path.expanduser(folder), str(zz), str(tx), "%d.png" % ty)
            if os.path.exists(dst):
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                import shutil
                shutil.copyfile(src, dst)
                n += 1
            except OSError:
                pass
        return n

    def uji_koneksi(self, timeout=12):
        """Ambil SATU ubin sekarang juga (sinkron) untuk memastikan jaringan benar.

        Dipakai menu "Peta: uji koneksi": membedakan masalah jaringan dari masalah tampilan.
        """
        import urllib.request
        lat, lon, z = self._bidang()[:3]
        px, py = G.lonlat_ke_piksel_global(lat, lon, z)
        tx, ty = int(px // 256), int(py // 256)
        url = self._url_ubin(z, tx, ty)
        path = self._path_ubin(z, tx, ty)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) < 100:
                return False, "server replied but the tile is empty (%d bytes) at %s" % (len(data), url)
            with open(path, "wb") as f:
                f.write(data)
            self._minta_kasar.clear()
            self._lapisan.clear()
            self.update()
            return True, "OK %d bytes from %s → saved to cache" % (len(data), url)
        except Exception as exc:                                # noqa: BLE001
            return False, "FAILED %r for %s (check network/proxy/firewall)" % (exc, url)

    def zoom(self, langkah):
        """langkah: +1 = menjauh (lebih luas), -1 = mendekat."""
        # Zoom-out DIPERLUAS (permintaan pemilik 17 Okt: ingin bisa melihat
        # seluruh Indonesia — ubin z0-8 Indonesia sudah di-cache oleh
        # tools/cache_peta_indonesia.py sehingga instan walau tanpa jaringan).
        self._zoom_delta = max(-20, min(6, self._zoom_delta + langkah))
        self.update()

    def _geser_meter(self, dx_piksel, dy_piksel):
        """Geser pandangan sebesar perpindahan piksel (dipakai saat menyeret peta)."""
        lat, lon, z = self._bidang()[:3]
        mpx = G.skala_m_per_piksel(lat, z)
        de = dx_piksel * mpx            # kanan = timur
        dn = -dy_piksel * mpx           # bawah = selatan
        if self.ikuti:
            self._offset_m[0] += dn
            self._offset_m[1] += de
        else:
            clat, clon = self._pusat_bebas
            nl, no = G.ned_ke_latlon(dn, de, {"lat": clat, "lon": clon, "heading_deg": 0.0})
            self._pusat_bebas = (nl, no)

    def wheelEvent(self, ev):
        self.zoom(1 if ev.angleDelta().y() < 0 else -1)

    def siapkan_pin(self):
        """Aktifkan mode pin: klik berikutnya di peta = titik anchor."""
        self.mode_pin = True
        self.setCursor(QtCore.Qt.CrossCursor)
        self.update()

    def mouseDoubleClickEvent(self, ev):
        self.ikuti = not self.ikuti
        self.update()
