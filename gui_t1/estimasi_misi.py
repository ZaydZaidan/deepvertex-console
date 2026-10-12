"""Estimasi durasi misi survei — FITUR BARU (tidak ada di GUI lama), ditandai "+ NEW".

Model & kalibrasi diambil dari data yang SUDAH TERUKUR di proyek ini, bukan dikarang
(skrip aslinya: ROVGUI_REDESIGN/estimasi_waktu_misi.py, dijalankan ulang oleh uji):

  n_baris   = 2*floor((sisi/2) / jarak_baris) + 1  sisi = 2 x AREA (setengah-sisi)
  waypoint  = 1 + 2 x n_baris                      (WP pertama = pose robot, lalu 2/ lajur)
  t_misi    = n_baris x (sisi / v + t_belok)
              v = 0,5 m/s      (komentar terukur di bluerov2_lawnmower.py)
              t_belok = 17,33 s (kalibrasi: misi terukur 24 wp, 12 baris, 448 s)

Jangkar terukur yang harus tetap cocok (dijaga uji otomatis):
  - area 10x10 m, baris 1,0 m -> 11 lajur, 23 wp  (log node guide 2 Okt: 23 waypoint)
  - area 4x4 m, baris 1,0 m   ->  5 lajur, 11 wp  (dokumen 30 Sep §11.1: 1 + 2x5 lajur)
  - area 4x4 m, baris 0,55 m  ->  7 lajur, 15 wp
  - durasi 10x10 m, 1,0 m     -> 411 s (06:51)

KOREKSI 2 Okt 2026: rumus lajur/waypoint lama (floor(sisi/jarak)+1 dan 2 x lajur) adalah
warisan planner kerangka DUNIA; panel menampilkan 8 lajur/16 wp untuk area 4x4 m & 0,55 m
padahal node terbang 7 lajur/15 wp. Durasi (angka yang dipakai pilot) tidak berubah untuk
10x10 m karena jumlah lajurnya kebetulan sama.

Nilai ini PERKIRAAN (label "est." di GUI), bukan janji — tidak dipakai untuk keputusan
keselamatan misi. Asumsi yang bisa bergeser: kecepatan rata-rata misi (suku sisi/v).
"""
import math

V_MPS = 0.5           # m/s
T_BELOK = 17.33       # s per baris (belok 180 + stabil + jeda tangkap), hasil kalibrasi
JARAK = {"fixed_wp": 1.0, "fixed_wp_dense": 0.55, "fixed_wp_lama": 1.0}
MODE = ("fixed_wp", "fixed_wp_dense", "fixed_wp_lama")
LABEL = {"fixed_wp": "FIXED WP", "fixed_wp_dense": "DENSE", "fixed_wp_lama": "LEGACY"}


def n_baris(sisi, jarak):
    """Jumlah LAJUR lintasan yang BENAR-BENAR dibangun node misi (mode bebas/bawaan).

    `plan_survey_robot_frame()` menaruh lajur tiap `jarak` meter pada v = -v_batas..+v_batas
    dengan v_batas = sisi/2 (k = -k_maks..k_maks, k_maks = floor(v_batas/jarak)) sehingga
    jumlah lajur = 2*floor((sisi/2)/jarak) + 1.

    KOREKSI 2 Okt 2026: rumus lama `floor(sisi/jarak)+1` adalah warisan planner kerangka
    DUNIA. Selisihnya nyata untuk area kecil/rapat: area 4x4 m & 0,55 m -> panel menampilkan
    8 lajur padahal node terbang 7 (dokumen 30 Sep §11.1: "11 waypoints = 1 + 2x5 lajur"
    untuk 4x4 m). Untuk 10x10 m & 1,0 m kedua rumus kebetulan sama (11 lajur).
    """
    return 2 * int(math.floor((float(sisi) / 2.0) / jarak + 1e-9)) + 1


def estimasi(area_half, mode="fixed_wp"):
    """`area_half` = nilai 'AREA ± m' (setengah-sisi). -> dict hasil (detik, baris, wp)."""
    if mode not in JARAK:
        mode = "fixed_wp"
    sisi = 2.0 * float(area_half)
    jarak = JARAK[mode]
    n = n_baris(sisi, jarak)
    t = n * (sisi / V_MPS + T_BELOK)
    # WAYPOINT = 1 + 2*lajur: node menaruh WP PERTAMA di pose robot (tepi dekat kotak) lalu
    # 2 WP per lajur — diukur: 4x4 m -> 11 wp (dokumen 30 Sep §11.1) dan 10x10 m -> 23 wp
    # (log node guide, 2 Okt). Rumus lama `2*lajur` melewatkan WP pose-robot itu.
    return {"sisi": sisi, "jarak": jarak, "baris": n, "waypoint": 1 + 2 * n,
            "detik": t, "panjang_m": n * sisi, "mode": mode}


def hhmmss(detik):
    """448 -> '07:28' (tanpa jam kalau di bawah 1 jam)."""
    m, s = divmod(int(round(detik)), 60)
    h, m = divmod(m, 60)
    return ("%d:%02d:%02d" % (h, m, s)) if h else ("%02d:%02d" % (m, s))


def ringkas(area_half, mode="fixed_wp"):
    """(teks baris/waypoint, teks durasi) untuk panel MISSION CONTROL."""
    e = estimasi(area_half, mode)
    return ("%d · %d" % (e["baris"], e["waypoint"]), hhmmss(e["detik"]))
