#!/usr/bin/env python3
"""pid_view.py — JENDELA REALTIME: RESPON SISTEM + GALAT (permintaan pemilik 27 Sep).

TAMPILAN (dua panel, diperbarui ~5 Hz)
  1. RESPON SISTEM : satu trace PER ITERASI (gaya `step()` MATLAB — tiap iterasi warna dan
                     gaya garisnya sendiri), + acuan r(t) putus-putus + masukan u(t) (sumbu
                     kanan) + pita fase langkah/pasca.
  2. GALAT         : e(t) = r(t) - y(t) per iterasi (warna sama dengan pasangannya), pita +-p95,
                     garis nol, dan metrik hidup per iterasi (RMS, p95, maks, p2p).
  Info `stepinfo`-ala MATLAB di kanan bawah: y0 → y_ss, overshoot, rise 10-90%, p2p sisa, RMS
  galat — per iterasi, angka yang sama dengan yang dipakai auto-tuner memutuskan.

DUA SUMBER DATA (dipilih otomatis, selalu disebut mana yang dipakai)
  * **TANGGA TUNING** — `/rov/tuning/sampel` [t, u, y, y0, y_ss, err, fase, iterasi] +
    `/rov/tuning/kejadian` dari `rov_pid_autotune.py`. Acuan = garis dasar (pra-langkah), lalu
    level tunak terukur saat fase pasca → galat = deviasi terhadap level yang sudah dicapai
    (yaitu "galat tunak" hidup), dan per-iterasi bisa dibandingkan langsung.
  * **PELACAKAN MISI** — acuan = `/rov/target_z` (kedalaman target yang benar-benar dipakai
    mengendalikan ROV), umpan balik = `/rov/depth` → galat = target - terukur.

KENAPA PROSES TERPISAH. Plot realtime di dalam proses Qt membuat GUI tersendat (pelajaran yang
sama dengan peta 3D, §12). Jendela ini proses sendiri; GUI hanya meluncurkannya.

Pemakaian
  /usr/bin/python3 gui/pid_view.py                       # otomatis (tuning / misi), tema gelap
  /usr/bin/python3 gui/pid_view.py --terang               # tema terang ala MATLAB
  /usr/bin/python3 gui/pid_view.py --sintetis tangga --tanpa-jendela --simpan /tmp/p.png
  /usr/bin/python3 gui/pid_view.py --sintetis misi --durasi 12 --tanpa-jendela --teks

Keluar ≠ 0 dengan pesan jelas bila tidak ada sumber data sama sekali (kecuali `--sintetis`).
"""
import argparse
import math
import os
import sys
import threading
import time
from collections import deque

import numpy as np

# ---- gaya "MATLAB": urutan warna default MATLAB (lines colormap) + gaya garis bergilir ----
WARNA_MATLAB = ["#0072BD", "#D95319", "#EDB120", "#7E2F8E", "#77AC30", "#4DBEEE", "#A2142F",
                "#00B7A5", "#B284BE"]
GAYA_GARIS = ["-", "--", "-.", ":"]
# Tema plot diambil dari TOKEN GUI (gui/theme.py) supaya jendela ini tidak pernah
# berbeda tema dari dashboard. Palet aktif dibaca dari env ROVGUI_THEME (di-set oleh
# dashboard saat menjalankan proses ini) — tanpa itu pakai palet bawaan.
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import theme as _theme
except Exception:                                          # noqa: BLE001 - jalan tanpa GUI
    _theme = None


def palet_plot(nama=None):
    """Token tema GUI -> nama kunci yang dipakai plot (latAR/panel/teks/redup/...)."""
    if _theme is None:
        return dict(latar="#12161A", panel="#12161A", teks="#E8EBEE", redup="#9AA5B1",
                    kisi="#37474F", nol="#546E7A", acuan="#B0BEC5")
    t = _theme.nilai(nama)
    return dict(latar=t["panel"], panel=t["panel"], teks=t["ink"], redup=t["ink_dim"],
                kisi=t["line_hi"], nol=t["ink_faint"], acuan=t["ink_faint"])


TEMA = ({n: palet_plot(n) for n in _theme.daftar()} if _theme else
        {"gelap": palet_plot(), "terang": palet_plot()})
# Nama lama ("gelap"/"terang") tetap ada: skrip & uji lamanya memakai kunci ini.
TEMA["gelap"] = TEMA.get("abyss", palet_plot())
TEMA["terang"] = TEMA.get("ivory", palet_plot())


def palet_awal():
    """Palet plot awal: dari env ROVGUI_THEME, kalau tidak ada pakai bawaan theme.py."""
    return _theme.aktif() if _theme else "gelap"


# --------------------------------------------------------------------------- #
# metrik (fungsi MURNI → bisa diuji tanpa ROS/jendela)
# --------------------------------------------------------------------------- #
def statistik(e):
    """Statistik galat e → dict (kosong → None semua, jangan mengarang angka)."""
    a = np.asarray([v for v in np.asarray(e, dtype=float) if math.isfinite(v)], dtype=float)
    if a.size == 0:
        return {"n": 0, "sekarang": None, "rms": None, "p95": None, "maks": None,
                "rata": None, "p2p": None}
    return {"n": int(a.size), "sekarang": float(a[-1]),
            "rms": float(np.sqrt(np.mean(a ** 2))), "p95": float(np.percentile(np.abs(a), 95)),
            "maks": float(np.max(np.abs(a))), "rata": float(np.mean(a)),
            "p2p": float(np.max(a) - np.min(a))}


def metrik_respon(t, y, t_pra=0.0, t_step=0.0):
    """y0, y_ss, overshoot, rise 10-90% — rumus SAMA dengan `ukur_respons()` di
    rov_pid_autotune.py (satu definisi; jangan sampai jendela & tuner beda angka)."""
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    d = {"n": int(y.size), "y0": None, "y_ss": None, "overshoot": None, "rise_s": None,
         "p2p_sisa": None}
    if y.size < 10:
        return d
    if t_pra <= 0 or t_step <= 0:                       # tanpa fase (mis. pelacakan misi)
        d["y_ss"] = float(np.median(y))
        d["y0"] = float(y[0])
        return d
    pra = y[t < t_pra]
    step = [(tt, yy) for tt, yy in zip(t, y) if tt >= t_pra]
    pasca = [yy for tt, yy in step if tt >= t_pra + t_step]
    if pra.size:
        d["y0"] = float(np.median(pra))
    if not pasca or d["y0"] is None:
        return d
    d["y_ss"] = float(np.median(pasca))
    d["p2p_sisa"] = float(max(pasca) - min(pasca))
    langkah = d["y_ss"] - d["y0"]
    if abs(langkah) > 1e-9:
        arah = 1.0 if langkah >= 0 else -1.0
        puncak = max((arah * yy for _tt, yy in step)) * arah
        d["overshoot"] = float(max(0.0, (puncak - d["y_ss"]) / abs(langkah)))
        t10 = t90 = None
        for tt, yy in step:
            f = (yy - d["y0"]) / langkah
            if t10 is None and f >= 0.10:
                t10 = tt
            if t90 is None and f >= 0.90:
                t90 = tt
                break
        if t10 is not None and t90 is not None:
            d["rise_s"] = float(t90 - t10)
    return d


def bukit_lembah(t, y, sigma=None, menonjol_rel=0.10, halus_s=0.4):
    """Jumlah bukit/lembah + apakah sudah melandai pada deret (aturan sama dengan tuner).

    Disalin ringkas di sini supaya jendela tidak bergantung pada modul tuner (yang mengimpor
    pymavlink). Prominence standar: berjalan sampai menemukan nilai lebih ekstrem.
    """
    t = np.asarray(t, float)
    y = np.asarray(y, float)
    if y.size < 10:
        return 0, 0, None
    dt = float(np.median(np.diff(t))) or 0.05
    w = max(1, int(round(float(halus_s) / max(dt, 1e-3))))
    yh = np.convolve(y, np.ones(w) / w, mode="same")
    rentang = float(np.ptp(y)) or 1e-9
    menonjol = max(3.0 * float(sigma or 0.0), float(menonjol_rel) * rentang)
    d1 = np.diff(yh)
    bukit = lembah = 0

    def prom(i, naik):
        val = yh[i]
        kiri = val
        j = i
        while j > 0:
            j -= 1
            if (yh[j] > val) if naik else (yh[j] < val):
                break
            kiri = min(kiri, yh[j]) if naik else max(kiri, yh[j])
        kanan = val
        j = i
        while j < yh.size - 1:
            j += 1
            if (yh[j] > val) if naik else (yh[j] < val):
                break
            kanan = min(kanan, yh[j]) if naik else max(kanan, yh[j])
        return (val - max(kiri, kanan)) if naik else (min(kiri, kanan) - val)

    for i in range(1, d1.size):
        naik = None
        if d1[i - 1] > 0 and d1[i] <= 0:
            naik = True
        elif d1[i - 1] < 0 and d1[i] >= 0:
            naik = False
        if naik is None:
            continue
        if prom(i, naik) >= menonjol:
            if naik:
                bukit += 1
            else:
                lembah += 1
    # melandai? p2p 4 s terakhir <= max(3σ, 5% rentang) dan kemiringan kecil
    m = t >= (t[-1] - 4.0)
    melandai = None
    if m.sum() >= 5:
        yw = y[m]
        amb = max(3.0 * float(sigma or 0.0), 0.05 * rentang)
        tb = t[m] - float(np.mean(t[m]))
        sl = float(np.sum(tb * (yw - yw.mean())) / (float(np.sum(tb * tb)) or 1e-9))
        melandai = bool(float(np.ptp(yw)) <= amb and abs(sl) <= 0.02)
    return bukit, lembah, melandai


def fmt(v, lebar=7):
    if v is None or not math.isfinite(float(v)):
        return "-"
    return ("%+.*f" % (3 if abs(v) < 10 else 2, v)).rjust(lebar)


# --------------------------------------------------------------------------- #
# pengumpul data
# --------------------------------------------------------------------------- #
class Sumber:
    """Deret waktu dari topik ROS. Mode tangga dipisah PER ITERASI (satu trace = satu iterasi)."""

    def __init__(self, topik_sampel, topik_kejadian, topik_target, topik_depth,
                 topik_agl, jendela_s=30.0):
        # topik_agl: HANYA DISIMPAN, tidak dilanggan dan tidak digambar (28 Sep 2026 —
        # jujur di code, bukan di tooltip). Jendela ini menampilkan sinyal loop yang
        # sedang di-tuning; kanal AGL/fusi h tidak pernah menjadi umpan balik loop depth
        # (umpan baliknya sensor depth Bar30). Argumennya tetap ada supaya pemanggil
        # lama/tests tidak perlu diubah.
        self.topik = dict(sampel=topik_sampel, kejadian=topik_kejadian, target=topik_target,
                          depth=topik_depth, agl=topik_agl)
        self.jendela_s = float(jendela_s)
        self.t0 = time.monotonic()
        self.traces = {}          # iterasi -> dict(t=[], y=[], u=[], ref=[], err=[], fase=[])
        self.urut = []            # urutan iterasi muncul
        self.label = {"sinyal": "-", "satuan": "-", "nama": "-", "loop": "-"}
        self.komentar = {}        # iterasi -> label teks dari kejadian LANGKAH
        self.gain = {}            # iterasi -> teks P/I/D yang dipakai (dari kejadian LANGKAH)
        self.t_sampel_terakhir = None
        # pelacakan misi (satu trace)
        self.t_misi = deque(maxlen=4000)
        self.ref = deque(maxlen=4000)
        self.depth = deque(maxlen=4000)
        self.t_ref_terakhir = None
        self.t_depth_terakhir = None
        self.kejadian = deque(maxlen=60)

    # ---- sumber aktif (jujur: satu saja, disebut namanya) ----
    def mode(self):
        now = time.monotonic()
        if self.t_sampel_terakhir and now - self.t_sampel_terakhir < 2.0 and self.traces:
            return "tangga"
        if (self.t_ref_terakhir and self.t_depth_terakhir
                and now - self.t_ref_terakhir < 2.0 and now - self.t_depth_terakhir < 2.0
                and len(self.t_misi) > 2):
            return "mission"
        return None

    def trace(self, it):
        if it not in self.traces:
            self.traces[it] = {k: [] for k in ("t", "u", "y", "ref", "err", "fase")}
            self.urut.append(it)
        return self.traces[it]

    def timpa_trace(self, it, t, u, y, ref, err, fase):
        tr = self.trace(it)
        for k, v in (("t", t), ("u", u), ("y", y), ("ref", ref), ("err", err), ("fase", fase)):
            tr[k].append(v)

    # ---- deret siap gambar ----
    def deret(self, jendela_s=None):
        m = self.mode()
        if m == "tangga":
            traces = []
            for it in self.urut[-6:]:                    # tampilkan maksimal 6 iterasi terakhir
                tr = self.traces[it]
                if len(tr["t"]) < 2:
                    continue
                t = np.array(tr["t"], float)
                y = np.array(tr["y"], float)
                f = np.array(tr["fase"], float)
                t_pra = float(t[f == 1][0]) if (f == 1).any() else 0.0
                t_step = 0.0
                if (f == 1).any() and (f == 2).any():
                    t_step = float(t[f == 2][0] - t[f == 1][0])
                ref = np.array(tr["ref"], float)
                ref = np.where(np.isfinite(ref), ref, np.nan)
                st_tr = statistik(tr["err"])
                bk, lm, md = bukit_lembah(t, y, sigma=st_tr.get("rms"))
                traces.append(dict(iter=int(it), label=self.komentar.get(it, "iteration %d" % it),
                                   t=t, y=y, u=np.array(tr["u"], float), ref=ref,
                                   err=np.array(tr["err"], float), fase=f,
                                   gain=self.gain.get(int(it), ""),
                                   bukit=bk, lembah=lm, melandai=md,
                                   t_pra=t_pra, t_step=t_step,
                                   metrik=metrik_respon(t, y, t_pra, t_step),
                                   stat=st_tr))
            if not traces:
                return None
            return dict(mode="tangga", traces=traces, t_pra=traces[-1]["t_pra"],
                        t_step=traces[-1]["t_step"], **self.label)
        if m == "mission":
            t = np.array(self.t_misi, float)
            r = -np.array(self.ref, float)               # NED: target positif turun → tampil naik
            y = -np.array(self.depth, float)
            jw = float(jendela_s or self.jendela_s)
            if t.size:
                k = t >= (t[-1] - jw)
                t, r, y = t[k], r[k], y[k]
            tr = dict(iter=None, label="mission (target vs depth)", t=t, y=y, u=np.zeros_like(t),
                      ref=r, err=r - y, fase=np.zeros_like(t), t_pra=0.0, t_step=0.0,
                      metrik=metrik_respon(t, y, 0.0, 0.0), stat=statistik(r - y))
            return dict(mode="mission", traces=[tr], t_pra=0.0, t_step=0.0,
                        sinyal="depth", nama="depth", satuan="m",
                        loop="lawnmower/mission")
        return None


# --------------------------------------------------------------------------- #
# ROS
# --------------------------------------------------------------------------- #
def mulai_ros(p, sumber):
    try:
        import rclpy
        from rclpy.node import Node
        from std_msgs.msg import Float32, Float32MultiArray, Float64, String
    except Exception as exc:                                         # noqa: BLE001
        return None, "rclpy/std_msgs not available: %r" % (exc,)
    if not rclpy.ok():
        rclpy.init(args=None)
    node = Node("pid_view")

    def cb_sampel(msg):
        d = list(msg.data)
        if len(d) < 7:
            return
        it = int(d[7]) if len(d) > 7 else 0
        sumber.timpa_trace(it, d[0], d[1], d[2], d[3] if d[3] == d[3] else float("nan"),
                           d[5], d[6])
        sumber.t_sampel_terakhir = time.monotonic()

    def cb_kejadian(msg):
        teks = str(msg.data)
        sumber.kejadian.append(teks)
        if teks.startswith("TUNER|MULAI"):
            for bagian in teks.split("|"):
                if "=" in bagian:
                    k, v = bagian.split("=", 1)
                    sumber.label[k.strip()] = v.strip()
        elif teks.startswith("LANGKAH|"):
            bagi = teks.split("|")
            if len(bagi) >= 3:
                try:
                    it = int(bagi[1])
                    sumber.komentar[it] = bagi[2].strip()
                    if len(bagi) >= 6 and bagi[4].strip().upper() == "GAIN":
                        sumber.gain[it] = bagi[5].strip()
                except ValueError:
                    pass

    def cb_target(msg):
        sumber.t_misi.append(time.monotonic() - sumber.t0)
        sumber.ref.append(float(msg.data))
        sumber.t_ref_terakhir = time.monotonic()

    def cb_depth(msg):
        sumber.depth.append(float(msg.data))
        sumber.t_depth_terakhir = time.monotonic()

    node.create_subscription(Float32MultiArray, p.topik_sampel, cb_sampel, 50)
    node.create_subscription(String, p.topik_kejadian, cb_kejadian, 20)
    node.create_subscription(Float32, p.topik_target, cb_target, 20)
    node.create_subscription(Float64, p.topik_depth, cb_depth, 20)
    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()
    return node, ""


def ringkas_gain(teks_gain):
    """Pendekkan teks P/I/D untuk legenda: 'P (laju)=12 ; D (laju)=0.4' → 'P 12 · D 0,4'."""
    if not teks_gain:
        return ""
    bagian = []
    for potong in teks_gain.split(";"):
        potong = potong.strip()
        if "=" not in potong:
            continue
        nama, nilai = potong.split("=", 1)
        nama = nama.replace("(", "").replace(")", "").strip()
        bagian.append("%s %s" % (nama, nilai.strip().replace(".", ",")))
    return " · ".join(bagian)


def banding_awam(a, b):
    """Kalimat awam: apakah iterasi b lebih baik dari a (dipakai jendela realtime).

    Sengaja disalin ringkas di sini (bukan mengimpor modul tuner) supaya jendela tidak
    bergantung pada pymavlink/MAVLink — viewer harus tetap hidup walau tuner tidak ada.

    DUA KELAS UJI dibedakan dari penanda metriknya, karena angkanya tidak sebanding:
      * uji TANGGA   -> overshoot / rise time / residual swing (kecil = baik);
      * uji GANGGUAN -> simpangan puncak / waktu pulih / sisa akhir (kecil = baik).
    Tanpa pemisahan ini, uji gangguan (acuannya DIAM, "langkah"-nya cuma pulsa) terbaca sebagai
    "overshoot 332% → 16015%" — angka tak bermakna yang membuat pemilik mengira ada error
    (terukur pada plot tuning yaw 29 Sep).
    """
    if not a or not b:
        return ""
    gaya_gangguan = any(k in a or k in b for k in ("puncak_dev", "waktu_pulih", "sisa_akhir"))
    kolom = (("puncak_dev", "deviation", lambda v: "%.3f" % v),
             ("waktu_pulih", "recovery", lambda v: "%.2fs" % v),
             ("sisa_akhir", "residual", lambda v: "%.3f" % v)) if gaya_gangguan else \
            (("overshoot", "overshoot", lambda v: "%.0f%%" % (100 * v)),
             ("p2p_sisa", "residual swing", lambda v: "%.3f" % v),
             ("rise_s", "rise time", lambda v: "%.2fs" % v))
    bagian, lebih_baik, lebih_buruk = [], 0, 0
    for kunci, label, fmt_ in kolom:
        va, vb = a.get(kunci), b.get(kunci)
        if va is None or vb is None or not va:
            continue
        d = (vb - va) / abs(va)
        if d < -0.05:
            lebih_baik += 1
        elif d > 0.05:
            lebih_buruk += 1
        bagian.append("%s %s→%s (%s %.0f%%)" % (label, fmt_(va), fmt_(vb),
                                                "up" if d > 0 else "down", abs(d) * 100))
    if not bagian:
        return ""
    if lebih_buruk == 0 and lebih_baik:
        putusan = "BETTER"
    elif lebih_baik == 0 and lebih_buruk:
        putusan = "WORSE"
    else:
        putusan = "MIXED"
    return "%s: %s — %s" % ("NEW ITERATION vs PREVIOUS (disturbance test)"
                            if gaya_gangguan else "NEW ITERATION vs PREVIOUS",
                            putusan, ", ".join(bagian))


PANDUAN = ("HOW TO READ (if you are new to PID)\n"
           "P = how hard the gap to the target is chased. Large P → snappy response\n"
           "    but it can overshoot/oscillate.\n"
           "I = removes the error that stays (e.g. always stopping 5 cm above).\n"
           "D = brakes before overshooting (damps oscillation).\n"
           "Overshoot           : how far past the target — the SMALLER the better.\n"
           "Rise time 10–90%    : speed to reach the target — the SMALLER the snappier.\n"
           "Residual swing (p2p): wobble after the step — the SMALLER the steadier.\n"
           "ERROR               : distance from the response to the reference; ideally shrinks to near zero.")


# --------------------------------------------------------------------------- #
# PEMULIHAN LANGGANAN
# --------------------------------------------------------------------------- #
class SambungRos:
    """Langganan ROS dengan pemulihan otomatis + diagnostika (permintaan pemilik 27 Sep).

    KENAPA: jendela yang dibuka OTOMATIS saat masuk mode tuning kadang tidak pernah menerima
    data, sedangkan jendela yang dibuka dari TOMBOL (jadi setelah tuner jalan) langsung normal.
    Terbukti dari jejak proses: jendela otomatis (mulai bersamaan tuner) hilang tanpa jejak
    sedangkan yang dari tombol hidup normal. Sebab paling masuk akal: langganan dibuat SEBELUM
    penerbit tuner ada, dan pada Fast DDS + profil SHM besar pencocokan itu kadang tidak terjadi.

    Daripada menebak, langganan DIHIDUPKAN ULANG otomatis bila belum ada data setelah
    `tunggu_s` detik (sampai `maks_ulang` kali), dan statusnya ditampilkan di baris atas:
    berapa penerbit yang terlihat (`count_publishers`) + sudah berapa kali menyambung ulang.
    """

    def __init__(self, topik, sumber, tunggu_s=6.0, maks_ulang=8):
        self.topik = topik
        self.sumber = sumber
        self.tunggu_s = float(tunggu_s)
        self.maks_ulang = int(maks_ulang)
        self.node = None
        self.pesan = "not tried yet"
        self.ulang = 0
        self.t_mulai = None
        self.n_sampel_terakhir = -1

    def mulai(self):
        """Buat node + semua langganan. -> pesan galat ("" = berhasil)."""
        try:
            import rclpy
            from rclpy.node import Node
            from std_msgs.msg import Float32, Float32MultiArray, Float64, String
        except Exception as exc:                                     # noqa: BLE001
            self.pesan = "rclpy not available: %r" % (exc,)
            return self.pesan
        if not rclpy.ok():
            rclpy.init(args=None)
        s = self.sumber
        self.node = Node("pid_view")
        p = self.topik

        def cb_sampel(msg):
            d = list(msg.data)
            if len(d) < 7:
                return
            it = int(d[7]) if len(d) > 7 else 0
            s.timpa_trace(it, d[0], d[1], d[2], d[3] if d[3] == d[3] else float("nan"),
                          d[5], d[6])
            s.t_sampel_terakhir = time.monotonic()

        def cb_kejadian(msg):
            teks = str(msg.data)
            s.kejadian.append(teks)
            if teks.startswith("TUNER|MULAI"):
                for bagian in teks.split("|"):
                    if "=" in bagian:
                        k, v = bagian.split("=", 1)
                        s.label[k.strip()] = v.strip()
            elif teks.startswith("LANGKAH|"):
                bagi = teks.split("|")
                if len(bagi) >= 3:
                    try:
                        it = int(bagi[1])
                        s.komentar[it] = bagi[2].strip()
                        if len(bagi) >= 6 and bagi[4].strip().upper() == "GAIN":
                            s.gain[it] = bagi[5].strip()
                    except ValueError:
                        pass

        def cb_target(msg):
            s.t_misi.append(time.monotonic() - s.t0)
            s.ref.append(float(msg.data))
            s.t_ref_terakhir = time.monotonic()

        def cb_depth(msg):
            s.depth.append(float(msg.data))
            s.t_depth_terakhir = time.monotonic()

        self.node.create_subscription(Float32MultiArray, p["sampel"], cb_sampel, 50)
        self.node.create_subscription(String, p["kejadian"], cb_kejadian, 20)
        self.node.create_subscription(Float32, p["target"], cb_target, 20)
        self.node.create_subscription(Float64, p["depth"], cb_depth, 20)
        threading.Thread(target=rclpy.spin, args=(self.node,), daemon=True).start()
        self.t_mulai = time.monotonic()
        self.pesan = ""
        return ""

    def jaga(self):
        """Dipanggil tiap refresh: pulihkan langganan bila belum ada data sama sekali."""
        if self.node is None:
            return self.mulai()
        if self.sumber.t_sampel_terakhir is not None:
            return ""                       # sudah menerima data → tidak perlu diapa-apakan
        if self.t_mulai is None:
            self.t_mulai = time.monotonic()
            return ""
        if time.monotonic() - self.t_mulai < self.tunggu_s:
            return ""
        if self.ulang >= self.maks_ulang:
            self.pesan = ("no data after %d reconnects — check: is the tuner "
                          "running? does topic %s exist?" % (self.ulang, self.topik["sampel"]))
            self.t_mulai = time.monotonic()          # coba lagi nanti (jangan berhenti mencoba)
            return self.pesan
        try:
            import rclpy
            if self.node is not None:
                self.node.destroy_node()
            rclpy.shutdown()
        except Exception:                                            # noqa: BLE001
            pass
        self.node = None
        self.ulang += 1
        return self.mulai()

    def info(self):
        """Teks status: penerbit terlihat + jumlah penyambungan ulang."""
        n = None
        try:
            if self.node is not None:
                n = self.node.count_publishers(self.topik["sampel"])
        except Exception:                                            # noqa: BLE001
            n = None
        return ("publishers %s · reconnects %d" % (n if n is not None else "?", self.ulang)
                + (" · " + self.pesan if self.pesan else ""))


# --------------------------------------------------------------------------- #
# gambar
# --------------------------------------------------------------------------- #
def gambar_ulang(aks_resp, aks_err, d, tema="gelap", kejadian=(), terang=False,
                 panduan=True, fig=None):
    """Dua panel: respon (satu trace per iterasi) + galat (warna sama). Fungsi terpisah supaya
    bisa dipanggil tanpa jendela (uji/laporan)."""
    T = TEMA[tema]
    aks_resp.clear()
    aks_err.clear()
    for a in (aks_resp, aks_err):
        a.set_facecolor(T["panel"])
        a.grid(True, which="major", color=T["kisi"], alpha=0.55, lw=0.6)
        a.grid(True, which="minor", color=T["kisi"], alpha=0.25, lw=0.4)
        a.minorticks_on()
        a.tick_params(labelsize=7.5, colors=T["redup"], direction="in", top=True, right=True)
        for sp in a.spines.values():
            sp.set_color(T["kisi"])
    if not d:
        for a in (aks_resp, aks_err):
            a.text(0.5, 0.5, "waiting for data…\n(enable Auto-PID, or run a mission)",
                   ha="center", va="center", fontsize=10, color=T["redup"],
                   transform=a.transAxes)
        aks_resp.set_title("SYSTEM RESPONSE", fontsize=10, color=T["teks"])
        aks_err.set_title("ERROR", fontsize=10, color=T["teks"])
        return None
    satuan = d.get("satuan", "-")
    baris_info = []
    baris_galat = []
    baris_gain = []
    for i, tr in enumerate(d["traces"]):
        warna = WARNA_MATLAB[i % len(WARNA_MATLAB)]
        gaya = GAYA_GARIS[i % len(GAYA_GARIS)]
        nm = ("iteration %d · %s" % (tr["iter"], tr["label"])) if tr["iter"] is not None \
            else tr["label"]
        g_ringkas = ringkas_gain(tr.get("gain", ""))
        if g_ringkas:
            nm += "  [" + g_ringkas + "]"
        aks_resp.plot(tr["t"], tr["y"], color=warna, ls=gaya, lw=1.5, label=nm)
        if np.isfinite(tr["ref"]).any():
            aks_resp.plot(tr["t"], tr["ref"], color=warna, lw=0.9, ls=":", alpha=0.85)
        aks_err.plot(tr["t"], tr["err"], color=warna, ls=gaya, lw=1.3, label=nm)
        m, s = tr["metrik"], tr["stat"]
        tag = ("iter %s" % tr["iter"]) if tr["iter"] is not None else "mission"
        baris_gain.append("%-6s %s" % (tag, g_ringkas or "(P/I/D not reported by the tuner)"))
        if m.get("puncak_dev") is not None:
            # UJI GANGGUAN: yang dibaca bukan lonjakan/waktu naik, melainkan besar simpangan,
            # waktu pulih, dan sisa menetap (permintaan pemilik: "lihat responnya mempertahankan
            # kedalaman"). Ditulis apa adanya, termasuk "belum pulih".
            baris_info.append("iter %-3s deviation %-7s recovered %-7s residual %-8s %d peaks %d troughs · "
                              "settled: %s"
                              % ((tr["iter"] if tr["iter"] is not None else "-"),
                                 ("%.3f" % m["puncak_dev"]) if m.get("puncak_dev") is not None
                                 else "-",
                                 ("%.2fs" % m["waktu_pulih"]) if m.get("waktu_pulih") is not None
                                 else "not yet",
                                 ("%.3f" % m["sisa_akhir"]) if m.get("sisa_akhir") is not None
                                 else "-",
                                 tr.get("bukit", 0), tr.get("lembah", 0),
                                 ("yes" if tr.get("melandai") else "not yet")
                                 if tr.get("melandai") is not None else "-"))
        baris_info.append("iter %-3s * shape: %d peaks %d troughs · settled: %s"
                          % ((tr["iter"] if tr["iter"] is not None else "-"), tr.get("bukit", 0),
                             tr.get("lembah", 0),
                             ("yes" if tr.get("melandai") else "not yet")
                             if tr.get("melandai") is not None else "-"))
        baris_info.append("iter %-3s y0 %s → y_ss %s · overshoot %-6s rise %-6s residual %-7s"
                          % ((tr["iter"] if tr["iter"] is not None else "-"),
                             ("%.3f" % m["y0"]) if m.get("y0") is not None else "-",
                             ("%.3f" % m["y_ss"]) if m.get("y_ss") is not None else "-",
                             ("%.0f%%" % (100 * m["overshoot"])) if m.get("overshoot") is not None
                             else "-",
                             ("%.2fs" % m["rise_s"]) if m.get("rise_s") is not None else "-",
                             ("%.3f" % m["p2p_sisa"]) if m.get("p2p_sisa") is not None else "-"))
        # GALAT per iterasi (permintaan pemilik: "tulis error iterasi 1 berapa, dst")
        baris_galat.append("iter %-3s error RMS %s · p95 %s · max %s"
                           % ((tr["iter"] if tr["iter"] is not None else "-"),
                              fmt(s["rms"]), fmt(s["p95"]), fmt(s["maks"])))
    if d["mode"] == "tangga":
        tr0 = d["traces"][-1]
        f = tr0["fase"]
        _ok = _theme.warna("ok") if _theme else "#66BB6A"
        for fa, warna, _nm in ((1, _ok, "langkah"), (2, T["nol"], "pasca")):
            m = f == fa
            if m.any():
                aks_resp.axvspan(tr0["t"][m][0], tr0["t"][m][-1], color=warna, alpha=0.10, lw=0)
        if np.abs(np.concatenate([tr["u"] for tr in d["traces"]])).max() > 0:
            a2 = aks_resp.twinx()
            for i, tr in enumerate(d["traces"]):
                a2.plot(tr["t"], tr["u"], color=WARNA_MATLAB[i % len(WARNA_MATLAB)], lw=0.8,
                        alpha=0.40)
            a2.set_ylabel("input u (µs)", fontsize=7.5, color=T["redup"])
            a2.tick_params(labelsize=6.5, colors=T["redup"], direction="in")
            for sp in a2.spines.values():
                sp.set_color(T["kisi"])
        aks_resp.set_title("SYSTEM RESPONSE — %s · each iteration has its own colour & line "
                           "style (source: Auto-PID step)" % d.get("nama", "sinyal"), fontsize=10,
                           color=T["teks"])
    else:
        aks_resp.set_title("SYSTEM RESPONSE — mission tracking (target_z vs depth)", fontsize=10,
                           color=T["teks"])
    aks_resp.set_ylabel("%s (%s)" % (d.get("nama", "signal"), satuan), fontsize=8.5,
                        color=T["teks"])
    aks_resp.tick_params(labelbottom=False)
    aks_resp.legend(loc="upper left", fontsize=7, framealpha=0.25, labelcolor=T["teks"])
    # kotak P/I/D yang sedang dipakai tiap iterasi (permintaan pemilik)
    if any(b for b in baris_gain):
        aks_resp.text(0.995, 0.985, "P/I/D IN USE\n" + "\n".join(baris_gain), transform=
                      aks_resp.transAxes, ha="right", va="top", fontsize=6.6, color=T["teks"],
                      bbox=dict(boxstyle="round,pad=0.3", fc=T["panel"], ec=T["kisi"], alpha=0.65))
    aks_err.axhline(0.0, color=T["nol"], lw=0.9, ls="--")
    st_semua = statistik(np.concatenate([tr["err"] for tr in d["traces"]]))
    if st_semua["p95"]:
        aks_err.axhspan(-st_semua["p95"], st_semua["p95"],
                        color=_theme.warna("warn") if _theme else "#FFCC80", alpha=0.10, lw=0)
    aks_err.set_ylabel("error (%s)" % satuan, fontsize=8.5, color=T["teks"])
    aks_err.set_xlabel("time since step (s)", fontsize=8.5, color=T["teks"])
    # TANPA legenda di panel galat: warna & gaya garis sudah dijelaskan di legenda panel respon,
    # dan kotak verdict butuh ruang di sini (uji render 27 Sep: legenda tertimpa kotak verdict).
    aks_err.set_title("ERROR e(t) = r − y — now %s · RMS %s · p95 %s · max %s (all iterations "
                      "in the window)"
                      % (fmt(st_semua["sekarang"]), fmt(st_semua["rms"]), fmt(st_semua["p95"]),
                         fmt(st_semua["maks"])), fontsize=9.5, color=T["teks"])
    # GALAT per iterasi + keputusan awam (dua kotak di panel galat)
    teks_galat = "ERROR PER ITERATION\n" + "\n".join(baris_galat)
    aks_err.text(0.995, 0.985, teks_galat, transform=aks_err.transAxes, ha="right", va="top",
                 fontsize=6.8, color=T["teks"],
                 bbox=dict(boxstyle="round,pad=0.3", fc=T["panel"], ec=T["kisi"], alpha=0.65))
    if len(d["traces"]) >= 2:
        a, b = d["traces"][-2]["metrik"], d["traces"][-1]["metrik"]
        kal = banding_awam(a, b)
        if kal:
            kal = kal.replace(" — ", "\n")
            aks_err.text(0.012, 0.985, kal, transform=aks_err.transAxes, ha="left", va="top",
                         fontsize=7.0, color=T["teks"],
                         bbox=dict(boxstyle="round,pad=0.3", fc=T["panel"],
                                   ec=_theme.warna("ok") if _theme else "#66BB6A",
                                   alpha=0.60))
    teks_info = "\n".join(baris_info[-6:])
    if kejadian:
        teks_info += "\n" + " | ".join(str(k) for k in list(kejadian)[-2:])
    aks_resp.text(0.012, 0.03, teks_info, transform=aks_resp.transAxes, ha="left", va="bottom",
                  fontsize=6.4, color=T["redup"])
    if panduan and fig is not None:
        # Panduan ditaruh di TINGKAT FIGUR (bawah), bukan di dalam axes: di dalam axes ia
        # berdesakan dengan legenda & kotak metrik (terlihat pada render uji 27 Sep).
        fig.text(0.012, 0.012, PANDUAN, ha="left", va="bottom", fontsize=6.0, color=T["redup"])
    return st_semua


def teks_metrik(d, st):
    """Ringkas untuk konsol/log (angka per iterasi, bukan hanya satu trace)."""
    if not d:
        return "no samples yet"
    out = []
    for tr in d["traces"]:
        m, s = tr["metrik"], tr["stat"]
        out.append("iter %s: y0 %s y_ss %s os %s rise %s p2p %s | error RMS %s p95 %s max %s "
                   "(n=%d)" % ((tr["iter"] if tr["iter"] is not None else "-"),
                               ("%.3f" % m["y0"]) if m["y0"] is not None else "-",
                               ("%.3f" % m["y_ss"]) if m["y_ss"] is not None else "-",
                               ("%.1f%%" % (100 * m["overshoot"])) if m["overshoot"] is not None
                               else "-",
                               ("%.2fs" % m["rise_s"]) if m["rise_s"] is not None else "-",
                               ("%.3f" % m["p2p_sisa"]) if m["p2p_sisa"] is not None else "-",
                               fmt(s["rms"]), fmt(s["p95"]), fmt(s["maks"]), s["n"]))
    return " · ".join(out)


# --------------------------------------------------------------------------- #
# data sintetis (uji/offline; tanpa ROS)
# --------------------------------------------------------------------------- #
def sintetis(mode="tangga", durasi=16.0, rate=20.0, seed=3, n_iter=3):
    """Tangga sintetis dengan BEBERAPA ITERASI berbeda redaman (agar gaya plot per-iterasi
    terlihat seperti keluarga respons `step()` MATLAB)."""
    rng = np.random.default_rng(seed)
    n = int(durasi * rate)
    t = np.arange(n) / rate
    if mode == "tangga":
        t_pra, t_step = 2.0, 3.0
        langkah = 0.25
        fase = np.where(t < t_pra, 0.0, np.where(t < t_pra + t_step, 1.0, 2.0))
        traces = []
        for i in range(n_iter):
            tau = [1.1, 0.85, 0.7][i % 3]
            zeta = [0.25, 0.45, 0.8][i % 3]                 # makin besar = makin teredam
            y = np.zeros(n)
            for k, tt in enumerate(t):
                if tt < t_pra:
                    y[k] = 0.0
                    continue
                dt = tt - t_pra
                w = 6.0
                y[k] = langkah * (1 - math.exp(-zeta * w * dt) / math.sqrt(max(1e-6, 1 - zeta ** 2))
                                  * math.sin(w * math.sqrt(max(1e-6, 1 - zeta ** 2)) * dt
                                             + math.acos(min(1.0, zeta))))
                if dt > 5 * tau:
                    y[k] = langkah
            y += rng.normal(0, 0.004, n)
            ref = np.where(fase >= 2, langkah, 0.0)
            traces.append(dict(iter=i + 1, label="depth_it%d" % (i + 1), t=t.copy(), y=y,
                               u=np.where(fase == 1, 120.0, 0.0), ref=ref, err=y - ref,
                               fase=fase, t_pra=t_pra, t_step=t_step,
                               gain="P (rate)=%d ; I (rate)=0 ; D (rate)=%.2f"
                                    % (8 + 4 * i, 0.2 + 0.1 * i),
                               metrik=metrik_respon(t, y, t_pra, t_step),
                               stat=statistik(y - ref)))
        return dict(mode="tangga", traces=traces, t_pra=t_pra, t_step=t_step, sinyal="vz",
                    nama="vertical rate", satuan="m/s", loop="depth")
    tgt = 3.0 + 0.45 * (t > 5.0).astype(float) - 0.30 * (t > 9.0).astype(float)
    z = np.zeros(n)
    for k in range(1, n):
        z[k] = z[k - 1] + (tgt[k] - z[k - 1]) * 0.10
    z += rng.normal(0, 0.01, n)
    tr = dict(iter=None, label="mission (target vs depth)", t=t, y=-z, u=np.zeros(n),
              ref=-tgt, err=(-tgt) - (-z), fase=np.zeros(n), t_pra=0.0, t_step=0.0,
              metrik=metrik_respon(t, -z, 0.0, 0.0), stat=statistik((-tgt) - (-z)))
    return dict(mode="mission", traces=[tr], t_pra=0.0, t_step=0.0, sinyal="depth",
                nama="depth", satuan="m", loop="lawnmower/mission")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Realtime window: system response + PID error")
    ap.add_argument("--topik-sampel", default="/rov/tuning/sampel")
    ap.add_argument("--topik-kejadian", default="/rov/tuning/kejadian")
    ap.add_argument("--topik-target", default="/rov/target_z")
    ap.add_argument("--topik-depth", default="/rov/depth")
    ap.add_argument("--topik-agl", default="/rov/altitude_meas_p20",
                    help="reserved (NOT plotted): this window shows the tuned loop's own "
                         "response/error. The depth loop's feedback is the ArduSub depth "
                         "sensor (Bar30), never the altitude fusion h=f*b/d")
    ap.add_argument("--jendela-s", type=float, default=30.0)
    ap.add_argument("--sinyal", default="auto")
    ap.add_argument("--terang", action="store_true",
                    help="MATLAB-style light theme (default: dark)")
    ap.add_argument("--simpan", default="")
    ap.add_argument("--tanpa-jendela", action="store_true")
    ap.add_argument("--sintetis", choices=("tangga", "misi"), default="")
    ap.add_argument("--iterasi-sintetis", type=int, default=3, dest="iterasi_sintetis")
    ap.add_argument("--durasi", type=float, default=16.0)
    ap.add_argument("--teks", action="store_true",
                    help="print per-iteration metrics (no window)")
    ap.add_argument("--tanpa-panduan", action="store_true", dest="tanpa_panduan",
                    help="hide the P/I/D explanation box (default: shown, so a reader new to "
                         "control theory can still read the plot)")
    a = ap.parse_args()
    tema = "ivory" if a.terang else palet_awal()

    if a.sintetis:
        d = sintetis(a.sintetis, a.durasi, n_iter=a.iterasi_sintetis)
        return gambar_dan_selesai(d, a, tema, [])

    sumber = Sumber(a.topik_sampel, a.topik_kejadian, a.topik_target, a.topik_depth,
                    a.topik_agl, a.jendela_s)
    if a.tanpa_jendela:
        node, alasan = mulai_ros(a, sumber)
        if node is None:
            sys.exit("cannot start ROS: %s" % alasan)
        # Tunggu SAMPAI SELESAI, bukan sekadar "ada beberapa sampel": kalau berhenti terlalu
        # awal, gambar hanya memuat sebagian respons (terbukti di uji integrasi: jendela
        # keluar setelah 11 sampel padahal satu langkah tangga ~260 sampel).
        # Selesai = durasi habis, ATAU sudah ada data lalu sunyi 4 s (tuner berhenti).
        batas = time.monotonic() + max(3.0, float(a.durasi))
        sunyi_sejak = None
        ada_data = False
        n_lalu = -1
        d_terakhir = None
        while time.monotonic() < batas:
            d = sumber.deret()
            if d is not None:
                # SIMPAN deret terakhir yang sah: setelah tuner berhenti, sampel menjadi "basi"
                # dan mode() kembali None — tanpa ini jendela menolak menggambar padahal datanya
                # sudah pernah masuk (bug yang tertangkap uji integrasi 27 Sep).
                d_terakhir = d
            n = sum(len(tr["t"]) for tr in d["traces"]) if d else 0
            if n >= 5:
                ada_data = True
            if ada_data:
                if n == n_lalu:
                    sunyi_sejak = sunyi_sejak or time.monotonic()
                    if time.monotonic() - sunyi_sejak >= 4.0:
                        break
                else:
                    sunyi_sejak = None
                    n_lalu = n
            time.sleep(0.2)
        d = sumber.deret() or d_terakhir
        if d is None:
            sys.exit("no data from either source:\n"
                     "  * tuning step  : %s (tuner not running?)\n"
                     "  * mission track: %s + %s (mission not running?)"
                     % (a.topik_sampel, a.topik_target, a.topik_depth))
        hasil = gambar_dan_selesai(d, a, tema, sumber.kejadian)
        # Tutup rclpy DENGAN URUT lalu keluar paksa: thread spin masih berjalan saat exit dan
        # kalau tidak, penerjemah mencetak "terminate called without an active exception"
        # (kotor tapi tidak berbahaya — sekarang dibersihkan supaya log run bisa dipercaya).
        try:
            import rclpy
            if node is not None:
                node.destroy_node()
            rclpy.shutdown()
        except Exception:                                            # noqa: BLE001
            pass
        sys.stdout.flush()
        os._exit(0)

    import matplotlib
    for b in ("TkAgg", "Qt5Agg"):
        try:
            matplotlib.use(b, force=True)
            break
        except Exception:                                            # noqa: BLE001
            continue
    import matplotlib.pyplot as plt
    T = TEMA[tema]
    fig = plt.figure(figsize=(10.8, 7.4))
    fig.patch.set_facecolor(T["latar"])
    aks_resp = fig.add_subplot(211)
    aks_err = fig.add_subplot(212, sharex=aks_resp)
    status = fig.text(0.012, 0.957, "waiting for data…", fontsize=8, color=T["redup"])
    topik = dict(sampel=a.topik_sampel, kejadian=a.topik_kejadian, target=a.topik_target,
                 depth=a.topik_depth)
    sambung = SambungRos(topik, sumber)
    alasan = sambung.mulai()
    if alasan:
        status.set_text("ROS not active: %s — window stays open & waits" % alasan)

    def segarkan():
        # PEMULIHAN + TAHAN-GALAT: satu galat menggambar TIDAK boleh membekukan jendela
        # (gejala yang dilaporkan pemilik 27 Sep: plot diam padahal log tuner jalan).
        try:
            pesan_pulih = sambung.jaga()
            d = sumber.deret()
            st = gambar_ulang(aks_resp, aks_err, d, tema, sumber.kejadian, terang=a.terang,
                              panduan=not a.tanpa_panduan, fig=fig)
            status.set_text("source %s · unit %s · %s · %s"
                            % (d["mode"] if d else "-", (d or {}).get("satuan", "-"),
                               "each iteration has its own colour & line style" if d
                               else "waiting for data (from %s)" % a.topik_sampel,
                               sambung.info()))
            status.set_color(T["redup"] if not pesan_pulih else
                             (_theme.warna("warn") if _theme else "#FFB74D"))
            fig.canvas.draw_idle()
        except Exception as exc:                                     # noqa: BLE001
            print("ERROR while drawing (recorded, window stays alive): %r" % (exc,), flush=True)
            import traceback
            traceback.print_exc()

    timer = fig.canvas.new_timer(interval=200)
    timer.add_callback(segarkan)
    timer.start()
    print("response+error window open (theme %s) · waiting for data…" % tema)
    plt.show()
    return 0


def gambar_dan_selesai(d, a, tema, kejadian):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    T = TEMA[tema]
    fig = plt.figure(figsize=(10.8, 7.4))
    fig.patch.set_facecolor(T["latar"])
    aks_resp = fig.add_subplot(211)
    aks_err = fig.add_subplot(212, sharex=aks_resp)
    st = gambar_ulang(aks_resp, aks_err, d, tema, kejadian, terang=a.terang,
                      panduan=not a.tanpa_panduan, fig=fig)
    fig.subplots_adjust(bottom=0.205 if not a.tanpa_panduan else 0.08, top=0.93, hspace=0.22)
    print("source: %s · %s" % (d["mode"], teks_metrik(d, st)))
    if kejadian:
        print("events received: %s" % " | ".join(str(k) for k in list(kejadian)[-4:]))
    if a.simpan:
        os.makedirs(os.path.dirname(os.path.abspath(a.simpan)), exist_ok=True)
        fig.savefig(a.simpan, dpi=115, facecolor=fig.get_facecolor())
        print("PNG: %s" % a.simpan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
