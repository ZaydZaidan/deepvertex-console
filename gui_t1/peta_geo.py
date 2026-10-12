"""peta_geo.py — logika geografis untuk jendela PETA (ala QGC).

Terpisah dari Qt supaya bisa diuji tanpa tampilan.

Konsep:
  * Robot bekerja di frame lokal NED (x = Utara, y = Timur, dalam meter) — itu yang
    dipublikasikan odometri/VIO.
  * Untuk menggambar di peta perlu ANCHOR: satu titik (lat, lon) + arah hadap utara sejati
    (`heading_deg`) di frame lokal. Sekarang diisi MANUAL oleh operator (dialog menu);
    di robot nyata nanti diambil dari GPS saat di permukaan (fungsi `anchor_dari_gps` sudah
    disiapkan untuk itu).
  * Konversi memakai pendekatan bidang datar (equirectangular) — cukup untuk radius survei
    puluhan meter; galat << 1 cm pada skala ini.

Tile:
  * Sumber OSM standar: https://tile.openstreetmap.org/{z}/{x}/{y}.png (tanpa API key).
  * Fungsi tile_x/tile_y/lonlat_ke_piksel_gobal memakai rumus Web Mercator standar.
"""
import json
import math
import os

R_BUMI = 6378137.0          # radius ekuator (m)
LAT_MAKS = 85.05112878      # batas Web Mercator


def ned_ke_latlon(x_utara, y_timur, anchor):
    """(x,y) NED meter -> (lat, lon) derajat. Anchor: dict lat/lon/heading_deg."""
    lat0 = math.radians(anchor["lat"])
    lon0 = math.radians(anchor["lon"])
    hd = math.radians(anchor.get("heading_deg", 0.0))
    # putar dari frame lokal ke Utara/Timur sejati kalau frame dipasang miring
    n = x_utara * math.cos(hd) - y_timur * math.sin(hd)
    e = x_utara * math.sin(hd) + y_timur * math.cos(hd)
    dlat = n / R_BUMI
    dlon = e / (R_BUMI * math.cos(lat0))
    return math.degrees(lat0 + dlat), math.degrees(lon0 + dlon)


def latlon_ke_ned(lat, lon, anchor):
    """Kebalikan ned_ke_latlon (untuk menaruh penanda GPS di peta lokal)."""
    lat0 = math.radians(anchor["lat"])
    lon0 = math.radians(anchor["lon"])
    hd = math.radians(anchor.get("heading_deg", 0.0))
    n = math.radians(lat - anchor["lat"]) * R_BUMI
    e = math.radians(lon - anchor["lon"]) * R_BUMI * math.cos(lat0)
    x = n * math.cos(-hd) - e * math.sin(-hd)
    y = n * math.sin(-hd) + e * math.cos(-hd)
    return x, y


def anchor_dari_gps(lat, lon, heading_deg=0.0):
    """Anchor dari GPS saat robot di permukaan (dipakai di robot nyata)."""
    return {"lat": float(lat), "lon": float(lon), "heading_deg": float(heading_deg),
            "sumber": "gps"}


def muat_anchor(path, bawaan=None):
    """Anchor tersimpan (JSON) atau bawaan (mis. dari <ned> di skenario)."""
    if path and os.path.exists(path):
        try:
            d = json.load(open(path))
            if "lat" in d and "lon" in d:
                return d
        except (OSError, ValueError):
            pass
    return dict(bawaan or {"lat": 0.0, "lon": 0.0, "heading_deg": 0.0, "sumber": "manual"})


def simpan_anchor(path, anchor):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(anchor, open(path, "w"), indent=1)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- Web Mercator
def lonlat_ke_piksel_global(lat, lon, z):
    """Posisi di mosaik ubin global pada zoom z (satuan piksel ubin 256)."""
    lat = max(-LAT_MAKS, min(LAT_MAKS, lat))
    n = 2.0 ** z
    px = (lon + 180.0) / 360.0 * n * 256.0
    sinlat = math.sin(math.radians(lat))
    py = (0.5 - math.log((1 + sinlat) / (1 - sinlat)) / (4 * math.pi)) * n * 256.0
    return px, py


def piksel_global_ke_lonlat(px, py, z):
    n = 2.0 ** z
    lon = px / (n * 256.0) * 360.0 - 180.0
    y = 0.5 - py / (n * 256.0)
    lat = math.degrees(math.asin((math.exp(4 * math.pi * y) - 1) / (math.exp(4 * math.pi * y) + 1)))
    return lat, lon


def skala_m_per_piksel(lat, z):
    """Resolusi peta (m/piksel) pada lintang tertentu."""
    return 156543.03392 * math.cos(math.radians(lat)) / (2.0 ** z)


def zoom_untuk_span(meter, lat, lebar_piksel=800):
    """Pilih zoom agar `meter` selebar bidang tampil."""
    target = max(1e-6, meter) / max(1, lebar_piksel)     # m per piksel yang diinginkan
    z = math.log2(156543.03392 * math.cos(math.radians(lat)) / target)
    # Batas di sini hanya pengaman matematis; BATAS NYATA per penyedia diatur di PENYEDIA
    # (mis. satelit sampai z22). Dulu dibatasi 19 sehingga misi 10x10 m tidak pernah bisa
    # di-zoom dalam — itu penyebab lintasan tampak seperti garis tipis.
    return max(1, min(22, int(round(z))))


def ubin_untuk_bidang(lat, lon, z, lebar, tinggi_px):
    """Daftar (z, x, y, px, py) ubin yang menutupi bidang berukuran lebar x tinggi_px."""
    px0, py0 = lonlat_ke_piksel_global(lat, lon, z)
    x0, y0 = px0 - lebar / 2.0, py0 - tinggi_px / 2.0
    tx0, tx1 = int(x0 // 256), int((x0 + lebar) // 256)
    ty0, ty1 = int(y0 // 256), int((y0 + tinggi_px) // 256)
    n = 2 ** z
    hasil = []
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            if 0 <= ty < n:
                hasil.append((z, tx % n, ty, tx * 256 - x0, ty * 256 - y0))
    return hasil, x0, y0


def ringkas(posisi_ned, anchor):
    """Ringkasan untuk ditampilkan: lintasan (lat,lon) + panjang + luas kotak."""
    latlon = [ned_ke_latlon(x, y, anchor) for x, y in posisi_ned]
    if not latlon:
        return {"n": 0}
    la = [p[0] for p in latlon]
    lo = [p[1] for p in latlon]
    return {"n": len(latlon), "lat_min": min(la), "lat_max": max(la),
            "lon_min": min(lo), "lon_max": max(lo),
            "lintasan": latlon}
