#!/usr/bin/env python3
"""dem3d_viewer.py — JENDELA PYTHON TERPISAH untuk peta 3D/DEM (matplotlib asli).

Permintaan pemilik (27 Sep): *"DEM 3D-nya rada ngelag kalau dimasukin GUI. Gimana kalau di SFM
window mozaiknya satu aja, defaultnya nampilin yang 2D, tapi ada tombol (show 3D model) yang
mengalihkan ke window Python baru yang native untuk matplot 3D originalnya. Biar lebih rapih."*

Karena itu kanvas 3D TIDAK lagi ditanam di dalam GUI (setiap redraw-nya menggambar ulang seluruh
figur matplotlib di dalam proses Qt → lag). Jendela ini proses terpisah, jadi GUI tetap ringan:
menutup/memutar/zoom tidak menyentuh GUI sama sekali.

Kontrol: semuanya kontrol matplotlib asli (bukan widget Qt):
  * tahan klik-kiri + geser = putar · klik-kanan + geser = zoom · klik-tengah = geser
  * toolbar matplotlib (Home/pan/zoom/simpan) ikut terpasang
  * radio SKALA VERTIKAL (baru 7 Okt, permintaan pemilik): "full column" (BAWAAN) = sumbu Z
    dipatok 0 m (permukaan laut, datum DEM) → dasar terdalam + margin, rasio kotak ASLI =
    proporsi sebenarnya (relief tidak lagi tampak ekstrem); "relief focus" = perilaku lama
    (zoom pita relief + pengali vertikal). Berganti mode: klik radio-nya.
  * slider PENGALI VERTIKAL — hanya aktif di mode "relief focus"; bawaan 1x, naikkan
    (sampai 60x) kalau ingin relief nyaris datar ini kelihatan jelas
  * tombol radio: tekstur (relief + mozaik ditempel) / permukaan (warna kedalaman) /
    titik (point cloud capture)

Pemakaian:
    python3 dem3d_viewer.py <run_dir|dir_dem> [--mode permukaan|titik] [--vscale relief]
    python3 dem3d_viewer.py <run_dir> --simpan /tmp/dem3d.png --tanpa-jendela   # untuk laporan/uji

Keluar ≠ 0 dengan pesan jelas bila DEM belum ada (jangan membuka jendela kosong tanpa alasan).
"""
import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dem3d_plot as DP                                            # noqa: E402
try:                               # token warna GUI (opsional: skrip ini juga jalan sendiri)
    import theme as _t
except Exception:                                                  # noqa: BLE001
    _t = None


def _dir_dem(arga):
    """Terima folder run ATAU folder dem langsung."""
    if os.path.basename(os.path.normpath(arga)) == "dem" or os.path.exists(
            os.path.join(arga, "dem.npy")):
        return arga
    return os.path.join(arga, "dem")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", help="run folder (e.g. RUN SIMULASI/RUN_... ) or a dem folder")
    ap.add_argument("--mode", choices=("tekstur", "permukaan", "titik"), default="tekstur",
                    help="tekstur (DEFAULT): relief surface + MOSAIC pasted as texture; "
                         "permukaan: colour by depth; titik: capture point cloud")
    ap.add_argument("--exag", type=float, default=1.0,
                    help="vertical exaggeration for the RELIEF view (default 1; raise it with "
                         "the slider if you want this nearly flat relief to look more dramatic). "
                         "Ignored in the full-column view.")
    ap.add_argument("--vscale", choices=("column", "relief"), default="column",
                    help="vertical reference (default: column). 'column' = Z axis pinned from the "
                         "surface (0 m) down to the deepest seabed = true proportion of the full "
                         "water column; 'relief' = zoom Z to the detected relief band (old look). "
                         "Both can be switched live with the radio buttons in the window.")
    ap.add_argument("--cmap", default="viridis")
    ap.add_argument("--mutu", choices=("fast", "balanced", "full"), default="balanced",
                    help="display grid density: fast 0.10 m (~240 cells) · balanced 0.06 m "
                         "(~400 cells, DEFAULT) · full 0.04 m (~900 cells, smoothest texture "
                         "but heaviest rotation)")
    ap.add_argument("--sel", type=float, default=0.0,
                    help="grid cell (m) — overrides --mutu; e.g. 0.02 for a very smooth texture")
    ap.add_argument("--mozaik-px", type=int, default=1600,
                    help="max width of the mosaic image loaded (0 = full resolution). Note: "
                         "colour per cell = BOX AVERAGE, so raising this number does not add "
                         "sharpness; what adds it = --mutu/--sel")
    ap.add_argument("--tepi-radius", type=float, default=2.5,
                    help="max distance (m) an edge cell inside the capture polygon may be filled "
                         "from the nearest measured point (DEFAULT 2.5 m = capture polygon fully "
                         "filled; lower it to be stricter, e.g. 1.5)")
    ap.add_argument("--simpan", default="", help="save PNG then (optionally) exit")
    ap.add_argument("--tanpa-jendela", action="store_true",
                    help="no window (e.g. for reports/tests); requires saving a PNG")
    a = ap.parse_args()

    dir_dem = _dir_dem(a.run)
    dem, meta, titik = DP.muat(dir_dem)
    if dem is None:
        sys.exit("no DEM yet: %s (run a mission first, or open a run that already has dem/)"
                 % os.path.join(dir_dem, "dem.npy"))

    import matplotlib
    if a.tanpa_jendela:
        matplotlib.use("Agg")
    else:
        # backend jendela NATIVE: TkAgg (ringan) lalu Qt5Agg; kalau dua-duanya tidak ada,
        # biarkan matplotlib memilih (pesan galatnya akan jelas).
        for b in ("TkAgg", "Qt5Agg"):
            try:
                matplotlib.use(b, force=True)
                break
            except Exception:                                      # noqa: BLE001
                continue
    import matplotlib.pyplot as plt

    fig = plt.figure(figsize=(11, 8))
    aks = fig.add_subplot(111, projection="3d")
    judul = "3D MAP — %s" % os.path.basename(os.path.normpath(a.run))
    run_dir = a.run if os.path.isdir(os.path.join(a.run, "dem")) else os.path.dirname(dir_dem)
    # MUTU: kerapatan grid tampilan. Yang menentukan ketajaman tekstur adalah UKURAN SEL, bukan
    # jumlah piksel mozaik yang dimuat (warna per sel = rata-rata kotak lewat INTER_AREA), jadi
    # keduanya diatur bersama di sini dan dilaporkan apa adanya di baris status.
    tabel_mutu = {"fast": (0.10, 240), "balanced": (0.06, 400), "full": (0.04, 900)}
    sel, maks_sel = tabel_mutu[a.mutu]
    if a.sel > 0:
        sel = float(a.sel)
        maks_sel = max(maks_sel, int(np.ceil(30.0 / sel)))     # jangan paksa turun resolusi
    info_mo = {}
    img, bb = DP.muat_mozaik(run_dir, lebar_maks=a.mozaik_px, info=info_mo)
    titik_bersih, n_anom = DP.muat_titik_bersih(dir_dem)
    # Grid dibangun di atas POLIGON CAPTURE (alpha mozaik, hasil potong sfm_colmap), bukan
    # sebaran titik capture → permukaan 3D = tepat area yang benar-benar dicapture; tidak ada
    # "cut" 0,8 m di tiap sisi dan tidak ada bidang di area yang tidak pernah difoto.
    Z, xs, ys, cov, tepi, jar = DP.grid_dari_titik(
        titik_bersih or titik, sel=sel, bbox=bb, topeng=info_mo.get("topeng"),
        radius_tepi=(float(a.tepi_radius) if a.tepi_radius else None))
    status = ""
    mapp = bar = None

    def _ket_tekstur():
        """Kalimat jujur soal tutupan & tekstur (dihitung, bukan diklaim)."""
        if Z is None or cov is None or tepi is None:
            return "grid not formed"
        n = float(cov.size) or 1.0
        px_asli = info_mo.get("lebar_asli", 0)
        dx = (bb[2] - bb[0]) if bb else 0.0
        px_sel = (px_asli / dx * sel) if (px_asli and dx > 0) else 0.0
        ket = ("grid %.2f m (%s) · area %.2fx%.2f m = capture polygon · "
               "coverage %.0f%% measured + %.0f%% edge"
               % (sel, a.mutu, (bb[2] - bb[0]) if bb else (xs[-1] - xs[0]),
                  (bb[3] - bb[1]) if bb else (ys[-1] - ys[0]),
                  100 * cov.sum() / n, 100 * tepi.sum() / n))
        if tepi.any() and jar is not None:                      # kualitas tepi, bukan klaim rata
            ket += " (edge point distance %.2f-%.2f m, p90 %.2f)" % (
                float(jar[tepi].min()), float(jar[tepi].max()),
                float(np.percentile(jar[tepi], 90)))
        ket += " · 1 cell = %.0f px mosaic" % px_sel
        return ket

    def gambar_ulang(mode, exag, vskala):
        nonlocal mapp, bar, status
        if bar is not None:
            try:
                bar.remove()
            except Exception:                                     # noqa: BLE001
                pass
            bar = None
        if mode == "tekstur" and img is not None and Z is not None:
            # Mode relief = perilaku lama: pengali 1x = "skala asli" pita (keluhan pemilik
            # 27 Sep: "bentuknya ekstrim banget padahal reliefnya rada datar" — pengali
            # otomatis 1,7x dulu dipakai saat slider masih di 1). Mode column (bawaan baru
            # 7 Okt) mengabaikan pengali: yang tampil = proporsi kolom air penuh, permintaan
            # pemilik 7 Okt ("fluktuasi jangan tampak ekstrem").
            mapp, e = DP.gambar_bertekstur(
                aks, Z, xs, ys, cov, img, bb,
                exag=(max(1.0, exag) if vskala == "relief" else None),
                maks_sel=maks_sel, tepi=tepi, skala=vskala)
            # TANPA colorbar: warnanya FOTO, bukan skala kuantitatif. Colorbar viridis di sini
            # menyesatkan (dibaca sebagai "kedalaman dikodekan warna" padahal bukan).
            bar = None
            zz = Z[np.isfinite(Z)]
            if vskala == "relief":
                status = ("MOSAIC TEXTURE (colour = photo, not a scale) · vertical scale %.1fx"
                          "%s · depth %0.2f-%0.2f m · %s · %d points"
                          % (e, " (true scale)" if e <= 1.01 else " — move the slider to "
                             "exaggerate", zz.min(), zz.max(), _ket_tekstur(),
                             len(titik_bersih)))
            else:
                status = ("MOSAIC TEXTURE (colour = photo, not a scale) · FULL COLUMN "
                          "0.0-%.1f m deep (true scale) · depth %0.2f-%0.2f m · %s · %d points"
                          % (abs(aks.get_zlim()[0]), zz.min(), zz.max(), _ket_tekstur(),
                             len(titik_bersih)))
            if n_anom:
                status += " (%d spikes removed)" % n_anom
        else:
            mapp = DP.gambar(aks, dem, meta, titik, "titik" if mode == "titik" else "permukaan",
                             max(1.0, exag), a.cmap, skala=vskala)
            bar = fig.colorbar(mapp, ax=aks, shrink=0.6, pad=0.08) if mapp is not None else None
            status = DP.ringkas(dem, meta, titik, mode, max(1.0, exag), skala=vskala,
                                zlim=aks.get_zlim())
        aks.set_title("%s\n%s" % (judul, status), fontsize=10)
        DP.terapkan_tema(fig, aks, bar)

    mode_awal = a.mode
    if a.mode == "tekstur" and img is None:
        mode_awal = "permukaan"          # tidak ada mozaik → jangan tampilkan tekstur kosong
    gambar_ulang(mode_awal, a.exag, a.vscale)
    print("3D map: %s" % _ket_tekstur())
    if info_mo.get("skala", 1.0) < 1.0:
        print("mosaic loaded %d/%d px (scale %.2f) — sharpness is limited by the cell size "
              "%.2f m, not by these pixels; raise --mutu/--sel if you want it smoother"
              % (info_mo.get("lebar_dimuat", 0), info_mo.get("lebar_asli", 0),
                 info_mo.get("skala", 1.0), sel))

    if a.simpan:
        os.makedirs(os.path.dirname(os.path.abspath(a.simpan)), exist_ok=True)
        fig.canvas.draw()
        fig.savefig(a.simpan, dpi=110, facecolor=fig.get_facecolor())
        print("3D DEM saved: %s" % a.simpan)
    if a.tanpa_jendela:
        return 0

    # ---- kontrol interaktif (matplotlib asli, tanpa Qt) ----
    from matplotlib.widgets import RadioButtons, Slider

    # warna kontrol dari TOKEN GUI (bukan hex keras): kalau dashboard berganti tema,
    # jendela 3D yang dibuka berikutnya ikut tema itu (lewat env ROVGUI_THEME).
    _kontrol = _t.warna("panel") if _t else "#151A1F"
    _teks = _t.warna("ink") if _t else "#E8EBEE"
    _redup = _t.warna("ink_dim") if _t else "#9AA5B1"
    ax_radio = fig.add_axes([0.012, 0.66, 0.13, 0.17])
    ax_radio.set_facecolor(_kontrol)
    opsi = ("tekstur", "permukaan", "titik")
    radio = RadioButtons(ax_radio, opsi, active=opsi.index(mode_awal))
    for lbl in radio.labels:
        lbl.set_color(_redup)
        lbl.set_fontsize(9)

    # SKALA VERTIKAL (baru 7 Okt, permintaan pemilik: "switch mode pilihannya harus ada di
    # GUI"). "full column" (bawaan) = proporsi kolom air penuh; "relief focus" = perilaku
    # lama. Label ENGLISH karena semua teks GUI wajib Inggris (uji_bahasa_gui.py).
    _faint = _t.warna("ink_faint") if _t else "#6E7A87"
    ax_r2 = fig.add_axes([0.012, 0.50, 0.165, 0.125])
    ax_r2.set_facecolor(_kontrol)
    ops_v = ("full column", "relief focus")
    radio_v = RadioButtons(ax_r2, ops_v, active=0 if a.vscale == "column" else 1)
    for lbl in radio_v.labels:
        lbl.set_color(_redup)
        lbl.set_fontsize(9)
    ax_r2.set_title("vertical axis", fontsize=8, color=_redup)

    ax_sl = fig.add_axes([0.20, 0.035, 0.55, 0.03])
    ax_sl.set_facecolor(_kontrol)
    sl = Slider(ax_sl, "vertical scale", 1.0, 60.0, valinit=a.exag)
    sl.label.set_color(_redup)
    sl.valtext.set_color(_teks)

    def _vskala_radio():
        return "column" if radio_v.value_selected == "full column" else "relief"

    def _terap_slider(v):
        """Slider 'vertical scale' hanya bermakna di mode relief focus."""
        aktif = (v == "relief")
        try:
            sl.set_active(aktif)
            sl.label.set_color(_redup if aktif else _faint)
            sl.valtext.set_color(_teks if aktif else _faint)
        except Exception:                                         # noqa: BLE001
            pass

    def ulang(mode=None, exag=None, vskala=None):
        m = mode or (radio.value_selected if hasattr(radio, "value_selected") else mode_awal)
        e = exag if exag is not None else sl.val
        v = vskala or _vskala_radio()
        gambar_ulang(m, float(e), v)
        _terap_slider(v)
        fig.canvas.draw_idle()

    radio.on_clicked(lambda lbl: ulang(mode=lbl))
    radio_v.on_clicked(lambda lbl: ulang(vskala=("column" if lbl == "full column" else "relief")))
    sl.on_changed(lambda v: ulang(exag=v))
    _terap_slider(a.vscale)

    print("3D map window open · %s" % status)
    plt.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
