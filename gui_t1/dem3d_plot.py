"""dem3d_plot.py — INTI GAMBAR DEM 3D (matplotlib murni, tanpa Qt, tanpa state widget).

Dipisah dari widget supaya dipakai dua tempat (permintaan pemilik 27 Sep: DEM 3D jangan
ditanam di GUI karena membuat GUI ngelag — pindahkan ke jendela Python terpisah):

  * `dem3d_viewer.py` — JENDELA PYTHON TERPISAH (cara utama sekarang; `python3 dem3d_viewer.py <run>`),
  * `dem3d_panel.py`  — kanvas tertanam (dipertahankan sebagai komponen + dipakai ujinya,
                        tidak lagi dipasang di dashboard).

Fungsi di sini sengaja TANPA efek samping global: pemanggil yang memutuskan backend, ukuran
figur, dan kontrol. Semua satuan mengikuti frame NED lokal (Utara/Timur/kedalaman).
"""
import csv
import os

import numpy as np


def muat(dir_dem):
    """Baca DEM dari folder `<run>/dem` → (dem, meta, titik).

    dem    : grid float32 kedalaman (m, NaN = tanpa data)
    meta   : dict dari dem_meta.csv (i0, j0, gsd, nx, ny, dasar_m_min/max)
    titik  : [(x, y, kedalaman), ...] dari dem_titik.csv (titik capture)
    Mengembalikan (None, {}, []) bila dem.npy tidak ada (pemanggil yang memutuskan pesannya).
    """
    p_dem = os.path.join(dir_dem, "dem.npy")
    if not os.path.exists(p_dem):
        return None, {}, []
    dem = np.load(p_dem)
    meta = {}
    p_meta = os.path.join(dir_dem, "dem_meta.csv")
    if os.path.exists(p_meta):
        with open(p_meta) as f:
            meta = {k: float(v) for k, v in list(csv.DictReader(f))[0].items()}
    titik = []
    p_t = os.path.join(dir_dem, "dem_titik.csv")
    if os.path.exists(p_t):
        with open(p_t) as f:
            for r in csv.DictReader(f):
                try:
                    titik.append((float(r["x_m"]), float(r["y_m"]),
                                  float(r["kedalaman_dasar_m"])))
                except (KeyError, ValueError):
                    continue
    return dem, meta, titik


def muat_titik_bersih(dir_dem, buang_anomali=True):
    """Titik capture yang dipakai untuk PERMUKAAN 3D: kolom `anomali` dibuang.

    dem_bathimetri menandai spike (>0,35 m dari median tetangga) di kolom `anomali`. Untuk
    tampilan, spike itu dibuang (bukan disembunyikan: jumlahnya dikembalikan ke pemanggil
    supaya bisa disebut di judul gambar). kolom `leg` juga dibaca kalau ada — grid DEM sudah
    hanya memakai kaki lurus, jadi titik miring tidak ikut menggelembungkan tampilan.
    -> (titik, n_dibuang_anomali)
    """
    p_t = os.path.join(dir_dem, "dem_titik.csv")
    if not os.path.exists(p_t):
        return [], 0
    titik, n_buang = [], 0
    with open(p_t, newline="") as f:
        for r in csv.DictReader(f):
            try:
                x, y, z = float(r["x_m"]), float(r["y_m"]), float(r["kedalaman_dasar_m"])
            except (KeyError, ValueError):
                continue
            if buang_anomali and (r.get("anomali") == "1"):
                n_buang += 1
                continue
            titik.append((x, y, z))
    return titik, n_buang


def koordinat(dem, meta):
    """Grid (xs, ys) dalam meter dari meta DEM (i0/j0/gsd)."""
    g = float(meta.get("gsd", 0.05) or 0.05)
    i0, j0 = float(meta.get("i0", 0.0)), float(meta.get("j0", 0.0))
    H, W = dem.shape[:2]
    return (np.arange(W) + i0) * g, (np.arange(H) + j0) * g


def batas_kolom(zintai):
    """Batas Z mode `column` (kolom penuh) dari kedalaman yang BENAR-BENAR digambar.

    Permukaan = datum vertikal DEM (kedalaman 0 m; lihat dem_bathimetri.py "PERMUKAAN LAUT
    sebagai datum") — jadi 0 m BUKAN konstanta hardcode. Batas bawah = kedalaman terdalam +
    margin kecil, supaya relief terlihat APA ADANYA: 0,3 m relief di kolom 5,4 m memang ~6 %
    tinggi kotak, bukan seluruhnya (keluhan pemilik 7 Okt: autoscale ke rentang terdeteksi
    membuat fluktuasi tampak ekstrem padahal porsinya kecil di kolom air penuh).
    -> (atas, bawah) = (0.0, -kedalaman); None bila tak ada data sah.
    """
    z = np.asarray(zintai, float)
    z = z[np.isfinite(z)]
    if z.size == 0:
        return None
    kedalaman = float(z.max())
    margin = max(0.10, 0.05 * (kedalaman - float(z.min())))
    return 0.0, -(kedalaman + margin)


def pasang_kolom(aks, xs, ys, zintai):
    """Patok mode `column`: zlim eksplisit [-(terdalam+margin), 0] + rasio kotak ASLI.

    mplot3d menormalisasi rentang data memenuhi kotak (autoscale), jadi HANYA batas eksplisit
    yang menentukan proporsi relief; `set_box_aspect` membuat rasio X:Y:Z sama dengan ukuran
    meteran asli (skala sebenarnya, bukan rasio bawaan 4:4:3).
    -> hasil batas_kolom() (atau None bila tak ada data).
    """
    b = batas_kolom(zintai)
    if b is None:
        return None
    aks.set_zlim(b[1], b[0])
    try:
        dx = float(np.max(xs) - np.min(xs)) if xs is not None and len(xs) > 1 else 0.0
        dy = float(np.max(ys) - np.min(ys)) if ys is not None and len(ys) > 1 else 0.0
        dz = abs(b[1])
        if dx > 0 and dy > 0 and dz > 0:
            aks.set_box_aspect((dx, dy, dz))
    except Exception:                                          # noqa: BLE001
        pass
    return b


def lepas_kolom(aks):
    """Kembalikan rasio kotak ke bawaan matplotlib (dipakai mode `relief`).

    Wajib karena `set_box_aspect` dari mode column menempel di axes; tanpa dilepas, berganti
    ke mode relief di axes yang sama akan memakai rasio kolom — perilaku lama berubah. Pada
    axes baru ini no-op (bawaan memang None; probe 7 Okt: hasil render identik piksel).
    """
    try:
        aks.set_box_aspect(None)
    except Exception:                                          # noqa: BLE001
        pass


def ringkas(dem, meta, titik, mode, exag, skala="relief", zlim=None):
    """Satu baris status jujur untuk ditampilkan di jendela (bukan angka karangan).

    `skala="column"` mengganti bagian "vertical scale nx" dengan rentang kolom penuh;
    `zlim` (opsional) = aks.get_zlim() setelah digambar supaya teks = angka yang benar-benar
    tampil, bukan hitungan ulang yang bisa meleset.
    """
    if dem is None:
        return "no DEM yet"
    z = dem.astype(float)
    dalam = z[np.isfinite(z)]
    if skala == "column":
        bawah = None
        if zlim is not None and np.isfinite(zlim[0]):
            bawah = float(zlim[0])
        elif dalam.size:
            b = batas_kolom(z)
            bawah = b[1] if b else None
        bagian_z = ("FULL COLUMN 0.0-%.1f m deep (true scale)" % abs(bawah)
                    if bawah is not None else "FULL COLUMN (true scale)")
    else:
        bagian_z = "vertical scale %.0fx" % exag
    return ("%d cells filled · depth %.2f-%.2f m · %s · %s"
            % (int(np.isfinite(z).sum()),
               float(dalam.min()) if dalam.size else 0.0,
               float(dalam.max()) if dalam.size else 0.0,
               bagian_z, "points (point cloud, %d points)" % len(titik) if mode == "titik"
               else "surface"))


def gambar(aks, dem, meta, titik, mode="permukaan", exag=5.0, cmap="viridis", skala="relief"):
    """Gambar DEM ke `aks` (3D). Mengembalikan mappable (untuk colorbar) atau None.

    Catatan: kedalaman digambar ke BAWAH (tanda negatif) supaya bentuknya sesuai intuisi
    "makin dalam = makin turun". Dua mode skala vertikal (`skala`):
      * "relief" (BAWAAN, perilaku lama): -(z - rerata) * exag; batas sumbu Z = autoscale
        pita relief (mplot3d menormalkan rentang memenuhi kotak → tampilan dramatis);
      * "column" (baru 7 Okt, permintaan pemilik): z ABSOLUT (permukaan 0 m → dasar terdalam
        + margin) dengan batas eksplisit + rasio kotak asli, jadi relief tampil sebesar porsi
        sebenarnya di kolom air penuh; `exag` DIIABAIKAN (mode ini justru menjawab keluhan
        "fluktuasi tampak ekstrem").
    """
    a = aks
    a.clear()
    lepas_kolom(a)              # bersihkan rasio kotak bila axes ini pernah dipakai mode column
    a.set_xlabel("North (m)", fontsize=8)
    a.set_ylabel("East (m)", fontsize=8)
    a.set_zlabel("depth (m)", fontsize=8)
    a.tick_params(labelsize=7)
    a.view_init(elev=45, azim=-60)
    if dem is None and not titik:
        return None
    if mode == "titik" and titik:
        t = np.array(titik)
        zz = -t[:, 2] if skala == "column" else -t[:, 2] * exag
        m = a.scatter(t[:, 0], t[:, 1], zz, c=t[:, 2], cmap=cmap, s=8, depthshade=False)
        if skala == "column":
            pasang_kolom(a, t[:, 0], t[:, 1], t[:, 2])
        return m
    # PERMUKAAN: pakai grid KONTINU dari titik terukur (interpolasi ≤0,6 m). Grid DEM aslinya
    # jarang; menggambarnya setelah NaN diisi nilai maksimum menghasilkan "bidang datar +
    # spike" (bug tampilan yang terlihat di GUI, 27 Sep).
    if titik:
        Z, xs, ys, _cov, _tepi, _jar = grid_dari_titik(titik)
        if Z is not None and np.isfinite(Z).any():
            Zn = np.where(np.isfinite(Z), Z, np.nanmean(Z))
            X, Y = np.meshgrid(xs, ys)
            Zz = -Zn if skala == "column" else -(Zn - np.nanmean(Zn)) * exag
            m = a.plot_surface(X, Y, Zz, cmap=cmap, linewidth=0, antialiased=False,
                               rstride=1, cstride=1)
            if skala == "column":
                pasang_kolom(a, xs, ys, Z)
            return m
    if dem is None:
        return None
    xs, ys = koordinat(dem, meta)
    Z = dem.astype(float)
    Zn = np.where(np.isnan(Z), np.nanmean(Z) if np.isfinite(Z).any() else 0.0, Z)
    X, Y = np.meshgrid(xs, ys)
    Zz = -Zn if skala == "column" else -(Zn - np.nanmean(Zn)) * exag
    m = a.plot_surface(X, Y, Zz, cmap=cmap, linewidth=0, antialiased=False,
                       rstride=2, cstride=2)
    if skala == "column":
        pasang_kolom(a, xs, ys, Z)
    return m


def _terdekat(t, xs, ys):
    """Indeks+jarak titik terdekat untuk setiap sel grid — vektor (per blok baris).

    Dulu ini loop Python per sel (60x60 sel x 231 titik). Sekarang numpy: penting karena grid
    tampilan boleh diperluas ke seluruh bbox mozaik dan dirapatkan (mutu `penuh`), jadi
    jumlah selnya naik ~10x. Blok baris dibatasi supaya memori tetap kecil.
    -> (idx (ny, nx) int32, jarak (ny, nx) float32)
    """
    nx, ny, nt = len(xs), len(ys), t.shape[0]
    idx = np.zeros((ny, nx), np.int32)
    jar = np.zeros((ny, nx), np.float32)
    dx2 = (xs[None, :] - t[:, 0][:, None]) ** 2               # (titik, nx)
    baris = max(1, int(2_000_000 / max(1, nx * nt)))
    for j0 in range(0, ny, baris):
        j1 = min(ny, j0 + baris)
        dy2 = (ys[j0:j1][None, :] - t[:, 1][:, None]) ** 2     # (titik, baris)
        dd = np.sqrt(dx2[:, None, :] + dy2[:, :, None])        # (titik, baris, nx)
        k = np.argmin(dd, axis=0)                              # (baris, nx)
        idx[j0:j1] = k
        jar[j0:j1] = np.take_along_axis(dd, k[None], axis=0)[0]
    return idx, jar


def grid_dari_titik(titik, sel=0.10, radius=0.90, halus=2, bbox=None, radius_tepi=None,
                    topeng=None):
    """Grid tampilan KONTINU dari titik terukur (x, y, kedalaman) + peta tutup.

    Kenapa perlu: grid DEM aslinya JARANG (satu sel 5 cm per capture — 190 sel terisi dari
    43.000). Menggambar grid jarang setelah NaN-nya diisi nilai maksimum menghasilkan
    "bidang datar + spike" — dulu itu yang terlihat di GUI.

    LUAS & TUTUPAN (permintaan pemilik 27 Sep: *"3D hasil mapping jangan ada cut-cut, 1:1
    tampilannya sesuai yang di-capture"*):
      * `bbox=(x0,y0,x1,y1)` (NED) → grid tepat seluas kanvas mozaik (bukan sebaran titik);
      * `topeng` = citra alpha mozaik (0..255) → **poligon yang benar-benar dicapture**
        (sfm_colmap memotong mozaik ke poligon survei, jadi sel di luar poligon memang tidak
        pernah ter-capture dan TIDAK digambar — dulu ini terbaca sebagai "cut");
      * tiga tingkat: `cover` = sel di dalam poligon yang ≤ `radius` dari titik ukur
        (**TERUKUR**) · `tepi` = di dalam poligon tetapi `radius`..`radius_tepi`
        (**INTERPOLASI TEPI**, alpha lebih rendah, jarak titiknya dilaporkan) · sisanya NaN.

    Tanpa `bbox`/`topeng` perilakunya = versi lama (sebaran titik + 0,5 m, tanpa tingkat tepi).
    -> (Z, xs, ys, cover, tepi, jarak) — `jarak` = jarak ke titik ukur terdekat per sel (m),
       dipakai pemanggil untuk MELAPORKAN kualitas tepi, bukan mengklaim sama rata.
    """
    if not titik:
        return None, None, None, None, None, None
    t = np.array([[p[0], p[1], p[2]] for p in titik], dtype=float)
    if bbox is not None:
        x0, y0, x1, y1 = (float(v) for v in bbox[:4])
    else:
        x0, y0 = float(t[:, 0].min()) - 0.5, float(t[:, 1].min()) - 0.5
        x1, y1 = float(t[:, 0].max()) + 0.5, float(t[:, 1].max()) + 0.5
    if radius_tepi is None:
        radius_tepi = 2.50 if topeng is not None else (max(radius, 1.50) if bbox is not None
                                                        else radius)
    nx = max(2, int(round((x1 - x0) / sel)))
    ny = max(2, int(round((y1 - y0) / sel)))
    xs = x0 + (np.arange(nx) + 0.5) * (x1 - x0) / nx
    ys = y0 + (np.arange(ny) + 0.5) * (y1 - y0) / ny
    idx, jar = _terdekat(t, xs, ys)
    dalam = np.ones((ny, nx), bool)
    if topeng is not None:
        import cv2
        al = np.asarray(topeng)
        al = (al * 255.0) if al.dtype != np.uint8 and float(al.max()) <= 1.0 else al.astype(np.float32)
        dalam = cv2.resize(al.astype(np.float32), (nx, ny), interpolation=cv2.INTER_AREA) >= 128.0
    cover = dalam & (jar <= float(radius))
    tepi = dalam & (jar > float(radius)) & (jar <= float(radius_tepi))
    pakai = cover | tepi
    Z = np.where(pakai, t[idx, 2], np.nan)
    if halus and pakai.any():
        import cv2
        Zf = np.where(pakai, Z, 0.0).astype(np.float32)
        W = pakai.astype(np.float32)
        k = np.ones((3, 3), np.float32)
        num = cv2.filter2D(Zf, -1, k, borderType=cv2.BORDER_REPLICATE)
        den = cv2.filter2D(W, -1, k, borderType=cv2.BORDER_REPLICATE)
        Z = np.where(den > 0, num / np.maximum(den, 1e-6), np.nan)
    Z[~pakai] = np.nan
    return Z, xs, ys, cover, tepi, jar


def muat_mozaik(run_dir, lebar_maks=1600, info=None):
    """Muat orthomosaic run + transformasi dunia->piksel dari sfm_report.json.

    -> (img_bgr, bbox_world) atau (None, None) bila mozaik/laporan tidak ada.
    bbox_world = [x0, y0, x1, y1] (NED meter) SELARAS dengan kanvas mozaik: kolom naik
    dengan Utara (x), baris naik dengan Timur (y) — sama seperti sfm_colmap.py.

    `info` (dict, opsional) diisi fakta pemuatan supaya pemanggil bisa MELAPORKAN kejujuran
    teksturnya, bukan menebak: lebar_asli, lebar_dimuat, skala, tinggi_asli, tinggi_dimuat.
    `lebar_maks=0` atau None = muat resolusi penuh (dipakai jalur ekspor/mesh 1:1).
    """
    import cv2
    p_mo = os.path.join(run_dir, "sfm", "mosaic.png")
    p_rep = os.path.join(run_dir, "sfm", "sfm_report.json")
    if not (os.path.exists(p_mo) and os.path.exists(p_rep)):
        return None, None
    try:
        import json
        rep = json.load(open(p_rep))
        bb = (rep.get("mozaik", {}) or {}).get("bbox_world")
        if not bb:
            return None, None
        img = cv2.imread(p_mo, cv2.IMREAD_UNCHANGED)
        if img is None:
            return None, None
        topeng = None
        if img.ndim == 3 and img.shape[2] == 4:          # RGBA -> komposit ke latar gelap
            al = img[:, :, 3:4].astype(np.float32) / 255.0
            topeng = img[:, :, 3].copy()                  # poligon yang benar-benar dicapture
            rgb = img[:, :, :3].astype(np.float32) * al + np.array([20, 23, 25], np.float32) * (1 - al)
            img = rgb.astype(np.uint8)
        h_asli, w_asli = img.shape[:2]
        sk = 1.0 if not lebar_maks else min(1.0, float(lebar_maks) / max(1, w_asli))
        if sk < 1.0:
            img = cv2.resize(img, (int(w_asli * sk), int(h_asli * sk)),
                             interpolation=cv2.INTER_AREA)
            if topeng is not None:
                topeng = cv2.resize(topeng, (img.shape[1], img.shape[0]),
                                    interpolation=cv2.INTER_AREA)
        if isinstance(info, dict):
            info.update({"lebar_asli": int(w_asli), "tinggi_asli": int(h_asli),
                         "lebar_dimuat": int(img.shape[1]), "tinggi_dimuat": int(img.shape[0]),
                         "skala": float(sk), "topeng": topeng,
                         "potong_survei": bool((rep.get("mozaik", {}) or {})
                                               .get("potong_survei", False))})
        return img, [float(v) for v in bb]
    except Exception:                                        # noqa: BLE001
        return None, None


def bayangan_dari_kemiringan(Z, sel, az=315.0, alt=45.0, bawah=0.45, atas=1.25):
    """Faktor pencahayaan per sel dari kemiringan permukaan (pengganti shade=True).

    `shade=True` di matplotlib 3.6 MENGGANTI facecolors dengan colormap (tekstur mozaik
    hilang, terlihat seperti peta warna kedalaman). Jadi bayangan dihitung sendiri di sini:
    normal dari gradien kedalaman → dot dengan arah cahaya → faktor 0.45-1.25.
    -> array (ny-1, nx-1) pengali warna.
    """
    import math as _m
    a = np.deg2rad(az)
    e = np.deg2rad(alt)
    lx, ly, lz = _m.cos(e) * _m.sin(a), -_m.cos(e) * _m.cos(a), _m.sin(e)
    Zf = np.where(np.isfinite(Z), Z, np.nanmean(Z) if np.isfinite(Z).any() else 0.0)
    dzdx = np.gradient(Zf, axis=1) / max(1e-6, sel)      # kedalaman bertambah = turun
    dzdy = np.gradient(Zf, axis=0) / max(1e-6, sel)
    nx, ny, nz = -dzdx, -dzdy, np.ones_like(Zf)
    nn = np.sqrt(nx ** 2 + ny ** 2 + nz ** 2)
    dot = (nx * lx + ny * ly + nz * lz) / np.maximum(nn, 1e-9)
    dot = np.clip(0.5 + 0.5 * dot, 0.0, 1.0)
    return (bawah + (atas - bawah) * dot)[:-1, :-1]


def warna_tekstur_grid(xs, ys, img, bb, cover=None, Z=None, sel=None, tepi=None):
    """Warna mozaik per SEL grid tampilan -> (ny-1, nx-1, 4) RGBA untuk facecolors.

    PENTING (keluhan pemilik 27 Sep: "gambarnya kotak-kotak aneh overlay gambar koralnya"):
    versi pertama mengambil SATU piksel mozaik per sel 10 cm — mozaik 1,2 mm/px berarti satu sel
    menutup ±83 px, jadi warna per sel melompat-lompat seperti papan catur. Sekarang citra
    mozaik di-POTONG ke luas grid lalu **diperkecil dengan INTER_AREA** (rata-rata kotak) ke
    ukuran grid → tiap sel = warna rata-rata areanya (piksel foto asli, bukan warna tunggal).

    Sel TIDAK tertutup (cover False) diberi alpha 0 supaya tidak ada bidang karangan.
    Sel tingkat TEPI (di luar radius terukur, interpolasi ≤ radius_tepi) diberi alpha lebih
    rendah (0,55) supaya terbaca "ini estimasi tepi", bukan disembunyikan sebagai cut.
    """
    if img is None or bb is None:
        return None
    x0, y0, x1, y1 = bb
    h_img, w_img = img.shape[:2]
    ny, nx = len(ys) - 1, len(xs) - 1
    if ny < 1 or nx < 1:
        return None
    # kotak grid dalam ruang piksel mozaik
    px0 = int(np.clip((xs[0] - x0) / max(1e-9, (x1 - x0)) * w_img, 0, w_img - 1))
    px1 = int(np.clip((xs[-1] - x0) / max(1e-9, (x1 - x0)) * w_img, 1, w_img))
    py0 = int(np.clip((ys[0] - y0) / max(1e-9, (y1 - y0)) * h_img, 0, h_img - 1))
    py1 = int(np.clip((ys[-1] - y0) / max(1e-9, (y1 - y0)) * h_img, 1, h_img))
    import cv2
    crop = img[py0:py1, px0:px1]
    if crop.size == 0:
        return None
    kecil = cv2.resize(crop, (nx, ny), interpolation=cv2.INTER_AREA)
    rgb = kecil[:, :, ::-1].astype(np.float32) / 255.0          # BGR -> RGB, rata-rata per sel
    if Z is not None:
        f = bayangan_dari_kemiringan(Z, sel or 0.10)
        if f.shape == rgb.shape[:2]:
            rgb = np.clip(rgb * f[:, :, None], 0.0, 1.0)
    fc = np.concatenate([rgb, np.ones(rgb.shape[:2] + (1,), np.float32)], axis=2)
    if cover is not None:
        cov_q = cover[:-1, :-1] & cover[1:, :-1] & cover[:-1, 1:] & cover[1:, 1:]
        if tepi is not None:
            ter_q = (cover | tepi)[:-1, :-1] & (cover | tepi)[1:, :-1] \
                & (cover | tepi)[:-1, 1:] & (cover | tepi)[1:, 1:]
            fc[~ter_q, 3] = 0.0
            fc[ter_q & ~cov_q, 3] = 0.55                        # tepi = estimasi, ditandai
        else:
            fc[~cov_q, 3] = 0.0
    return fc


def pengali_otomatis_grid(Z, xs, ys, target_tinggi=0.18):
    """Pengali vertikal agar relief terlihat (bentuk 'dinormalisasi' secara tampilan)."""
    if Z is None:
        return 1.0
    z = Z[np.isfinite(Z)]
    if z.size < 10:
        return 1.0
    rentang_z = float(np.percentile(z, 98) - np.percentile(z, 2))
    lebar = max(1e-6, max(float(xs[-1] - xs[0]), float(ys[-1] - ys[0])))
    return 1.0 if rentang_z <= 1e-6 else max(1.0, target_tinggi * lebar / rentang_z)


def pengali_otomatis(dem, meta, target_tinggi=0.18):
    """Pengali vertikal yang membuat relief jelas terlihat (bentuk 'dinormalisasi').

    target_tinggi = bagian tinggi tampilan relatif terhadap lebar area (0,18 = 18 %).
    """
    if dem is None:
        return 1.0
    z = dem.astype(float)
    z = z[np.isfinite(z)]
    if z.size < 10:
        return 1.0
    rentang_z = float(np.percentile(z, 98) - np.percentile(z, 2))
    xs, ys = koordinat(dem, meta)
    lebar = max(1e-6, max(xs[-1] - xs[0], ys[-1] - ys[0]))
    if rentang_z <= 1e-6:
        return 1.0
    return max(1.0, target_tinggi * lebar / rentang_z)


def gambar_bertekstur(aks, Z, xs, ys, cover, img, bb, exag=None, target_tinggi=0.10,
                      maks_sel=200, tepi=None, skala="relief"):
    """PERMUKAAN 3D dengan MOZAIK ditempel sebagai tekstur (permintaan pemilik 27 Sep).

    Bentuk = kedalaman yang diinterpolasi (grid kontinu, bukan grid DEM jarang) lalu
    "dinormalisasi" secara tampilan lewat pengali vertikal (bawaan: otomatis supaya relief
    terlihat seperti dataran bergelombang); warna = citra mozaik pada koordinat dunianya.
    `tepi` (bila ada) = sel tingkat interpolasi tepi → alpha lebih rendah (lihat
    `warna_tekstur_grid`), jadi bidangnya tetap seluas capture tanpa berpura-pura semua terukur.
    Mode skala: "relief" (bawaan, perilaku lama) / "column" (kolom penuh: z absolut + batas
    eksplisit + rasio kotak asli; pengali vertikal diabaikan).
    -> (mappable, pengali_dipakai)
    """
    if Z is None or cover is None or not cover.any():
        return None, 1.0
    sel_grid = float(xs[1] - xs[0]) if len(xs) > 1 else 0.10
    fc = warna_tekstur_grid(xs, ys, img, bb, cover, Z=Z, sel=sel_grid, tepi=tepi)
    e = float(exag) if exag else pengali_otomatis_grid(Z, xs, ys, target_tinggi)
    if skala == "column":
        e = 1.0                                  # kolom penuh = skala sebenarnya; pengali off
    # turunkan resolusi: plot_surface menggambar satu poligon per sel, jadi grid besar
    # membuat rotasi tersendat (mutu `cepat` ~200 sel, `penuh` ~600 sel — lihat dem3d_viewer).
    ny, nx = Z.shape
    st = max(1, int(np.ceil(max(ny, nx) / float(maks_sel))))
    Zs, cs = Z[::st, ::st], cover[::st, ::st]
    ts = (cover | tepi)[::st, ::st] if tepi is not None else cs
    xss, yss = xs[::st], ys[::st]
    fcs = None
    if fc is not None:
        fcs = fc[::st, ::st]
        # `fc` = warna per SEL (ny-1, nx-1); `ts` = penutup per NODE (ny, nx). Saat st=1 keduanya
        # beda satu baris/kolom (sel (i,j) memakai node (i,j)) dan saat st>1 keduanya berukuran
        # SAMA. Memakai `ts[:-1, :-1]` untuk kedua kasus membuat st>1 langsung gagal:
        # bentuk (98,100) vs (99,101) -> "operands could not be broadcast" (bug tersembunyi,
        # muncul begitu grid tampilan > maks_sel dan permukaan diturunkan resolusinya).
        # Pemotongan [:n, :m] sama dengan perilaku lama saat st=1, jadi tampilan tak berubah.
        sel_ts = ts[:fcs.shape[0], :fcs.shape[1]]
        fcs = np.where(np.dstack([sel_ts] * 4), fcs,
                       np.zeros(sel_ts.shape + (4,), np.float32))
    rata = float(np.nanmean(Zs)) if np.isfinite(Zs).any() else 0.0
    Zvar = np.where(np.isfinite(Zs), Zs, np.nan)
    Zz = -Zvar if skala == "column" else -(Zvar - rata) * e
    X, Y = np.meshgrid(xss, yss)
    aks.clear()
    lepas_kolom(aks)
    aks.set_xlabel("North (m)", fontsize=8)
    aks.set_ylabel("East (m)", fontsize=8)
    aks.set_zlabel("depth (m)" if skala == "column" else "depth (m) x%.0f" % e, fontsize=8)
    aks.tick_params(labelsize=7)
    aks.view_init(elev=42, azim=-60)
    # shade=True: matplotlib 3.6 menerapkan pencahayaan pada facecolors → relief terbaca
    # (tanpa bayangan, permukaan bertekstur tampak rata). Kalau versi mpl lama mengabaikannya,
    # tidak ada efek samping (hanya tanpa bayangan).
    # shade=False WAJIB: dengan shade=True matplotlib mengganti facecolors dengan colormap
    # (tekstur mozaik hilang). Bayangannya sudah dihitung di warna_tekstur_grid().
    mapp = aks.plot_surface(X, Y, Zz, facecolors=fcs, linewidth=0, antialiased=False,
                            shade=False, rstride=1, cstride=1)
    if skala == "column":
        pasang_kolom(aks, xss, yss, Zs)
    return mapp, e


def terapkan_tema(fig, aks, bar=None, tema=None):
    """Tema GELAP mengikuti GUI (permintaan pemilik: "jangan light mode").

    `aks.clear()` mengembalikan warna bawaan matplotlib (putih), jadi fungsi ini WAJIB dipanggil
    setiap kali selesai menggambar. `tema` = modul berisi token warna GUI (gui/theme.py, murni
    tanpa Qt) supaya jendela terpisah tetap seragam dengan dashboard.
    """
    if tema is None:
        try:
            import theme as tema                                  # noqa: N813
        except ImportError:                                       # tema tidak ada → lewati
            return
    fig.patch.set_facecolor(tema.PANEL)
    aks.set_facecolor(tema.PANEL)
    for sumbu in (aks.xaxis, aks.yaxis, aks.zaxis):
        try:
            sumbu.pane.set_facecolor(tema.PANEL_ALT)
            sumbu.pane.set_edgecolor(tema.HAIRLINE)
            sumbu.pane.set_alpha(0.85)
        except Exception:                                         # noqa: BLE001
            pass
        sumbu.label.set_color(tema.TEXT_DIM)
        sumbu.set_tick_params(colors=tema.TEXT_DIM, labelcolor=tema.TEXT_FAINT)
        try:
            sumbu.line.set_color(tema.HAIRLINE_HI)
        except Exception:                                         # noqa: BLE001
            pass
        try:
            for garis in sumbu.get_gridlines():
                garis.set_color(tema.HAIRLINE)
        except Exception:                                         # noqa: BLE001
            pass
    aks.title.set_color(tema.TEXT)
    if bar is not None:
        try:
            bar.ax.tick_params(colors=tema.TEXT_FAINT, labelsize=7)
            # JANGAN menimpa teks label (pemanggil yang menentukan: "kedalaman dasar (m)" atau
            # "warna = foto mozaik") — dulu tema menimpanya sehingga label mode tekstur hilang.
            if not getattr(bar, "ax", None) or not bar.ax.get_ylabel():
                bar.set_label("seabed depth (m)", color=tema.TEXT_DIM, fontsize=8)
            else:
                bar.ax.yaxis.label.set_color(tema.TEXT_DIM)
                bar.ax.yaxis.label.set_fontsize(8)
            bar.outline.set_edgecolor(tema.HAIRLINE)
        except Exception:                                         # noqa: BLE001
            pass
