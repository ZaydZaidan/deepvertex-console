"""peta_penyedia.py — daftar penyedia ubin peta untuk panel "PETA ubin sendiri".

Prinsip: HANYA penyedia yang benar-benar mengembalikan citra yang didaftarkan.
Hasil uji nyata (`scripts/tools/uji_penyedia.py`, satu ubin per penyedia, 26 Sep 2026):

  hidup : OSM Standard · OSM Humanitarian (HOT) · OpenTopoMap · Wikimedia ·
          Esri World Imagery · World Topo · World Street · Dark Gray · Ocean
  dibuang: Google (4 jenis, host tidak dapat diresolusi — 0/4),
           OSM Cycle Map & Transport (host lama sudah tidak ada),
           Server sendiri/folder lokal/.mbtiles (permintaan pemilik; server ubinnya
           juga sudah dihapus).

Penyedia yang mati TIDAK didaftarkan supaya menu tidak menawarkan pilihan yang
menghasilkan peta kosong (keluhan pemilik soal Google).

`z_maks` = zoom terdalam yang benar-benar punya citra di wilayah uji; di atas itu
panel berhenti mengambil ubin dan memperbesar ubin terdalam (overzoom, gaya QGC).
"""
import os

CACHE = os.path.expanduser("~/.cache/rovgui_peta")

# User-Agent yang sama dengan panel peta (ketentuan penyedia: identitas jelas + atribusi).
UA = "RovGuiVIO/1.0 (riset VIO BlueROV2; kontak zaydzihnizaidan@gmail.com)"

PENYEDIA_DAFTAR = {'OSM : Standard': {'url': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                    'attr': '© OpenStreetMap contributors',
                    'sub': '',
                    'z_maks': 19,
                    'grup': 'OSM'},
 'OSM : Humanitarian (HOT)': {'url': 'https://tile-{s}.openstreetmap.fr/hot/{z}/{x}/{y}.png',
                              'attr': '© OpenStreetMap contributors · HOT',
                              'sub': 'hot',
                              'z_maks': 19,
                              'grup': 'OSM'},
 'OpenTopoMap': {'url': 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
                 'attr': '© OpenTopoMap (CC-BY-SA)',
                 'sub': 'topo',
                 'z_maks': 17,
                 'grup': 'OSM'},
 'Wikimedia (OSM intl)': {'url': 'https://maps.wikimedia.org/osm-intl/{z}/{x}/{y}.png',
                          'attr': '© OpenStreetMap · Wikimedia',
                          'sub': 'wikimedia',
                          'z_maks': 18,
                          'grup': 'OSM'},
 'Satelit (Esri World Imagery)': {'url': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                                  'attr': 'Esri World Imagery (for research)',
                                  'sub': 'satelit',
                                  'z_maks': 18,
                                  'grup': 'Esri'},
 'Esri : World Topo': {'url': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
                       'attr': 'Esri World Topo',
                       'sub': 'esri_topo',
                       'z_maks': 19,
                       'grup': 'Esri'},
 'Esri : World Street': {'url': 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
                         'attr': 'Esri World Street',
                         'sub': 'esri_street',
                         'z_maks': 19,
                         'grup': 'Esri'},
 'Esri : Dark Gray Canvas': {'url': 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
                             'attr': 'Esri Dark Gray Canvas',
                             'sub': 'esri_gelap',
                             'z_maks': 16,
                             'grup': 'Esri'},
 'Esri : Ocean Basemap': {'url': 'https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}',
                          'attr': 'Esri Ocean Basemap',
                          'sub': 'esri_laut',
                          'z_maks': 13,
                          'grup': 'Esri'}}

# PENYEDIA BAWAAN: dipilih dari hasil ukur `scripts/tools/ukur_penyedia.py` (latensi rata-rata,
# 3 ubin per penyedia, lokasi Jakarta/Seribu):
#   1. OSM : Standard        0,08 s  <-- TERCEPAT + tajam saat zoom dalam (bukan foto diperbesar)
#   2. Satelit (Esri)        0,20 s     citra foto; di zoom dalam tampak pecah/kabur
#   3. Esri Ocean            0,27 s
#   ... OpenTopoMap          2,66 s     paling lambat
#
# KEPUTUSAN PEMILIK 27 Sep 2026: HOT TIDAK lagi jadi bawaan. Dua alasan terukur:
#   (a) HOT paling lambat dari jaringan ini (1,7-1,8 s/ubin) dan ubinnya sering nyaris kosong
#       (738 byte) sehingga peta tampak "tidak muncul";
#   (b) tampilannya dinilai ulang secara visual pada ubin area Kepulauan Seribu
#       (`scripts/tools/banding_penyedia.py`): OSM Standard punya garis pantai tegas + label
#       terbaca + tidak ramai = paling dekat dengan tampilan "bersih" QGroundControl.
BAWAAN = "OSM : Standard"

URUTAN_GRUP = ["OSM", "Esri"]


def grup(nama):
    return PENYEDIA_DAFTAR.get(nama, {}).get("grup", "Lain")


def per_grup():
    """{nama grup: [nama penyedia...]} mengikuti URUTAN_GRUP."""
    hasil = {}
    for nama in PENYEDIA_DAFTAR:
        hasil.setdefault(grup(nama), []).append(nama)
    return {g: hasil[g] for g in URUTAN_GRUP if g in hasil}


# ---------------------------------------------------------------- ubin (tanpa Qt)
# Dipakai DUA tempat supaya tidak pernah divergen: panel peta (`peta_panel.py`) dan alat
# pengunduh offline (`scripts/tools/panaskan_peta.py`, jalan tanpa GUI).
# Uji `test_peta.py::R4` memastikan kedua jalur menulis ke BERKAS YANG SAMA.
def info(nama):
    """Info penyedia (dict) atau {} bila namanya tidak dikenal."""
    return dict(PENYEDIA_DAFTAR.get(nama, {}))


def url_ubin(inf, z, x, y):
    """URL ubin untuk info penyedia (menangani subdomain {s} seperti panel)."""
    s = "abc"[(x + y) % 3] if "{s}" in inf.get("url", "") else ""
    return inf["url"].format(z=z, x=x, y=y, s=s)


def path_ubin(inf, z, x, y):
    """Berkas cache ubin — cache terpisah per penyedia (persis seperti QGC)."""
    d = os.path.join(CACHE, inf["sub"]) if inf.get("sub") else CACHE
    return os.path.join(d, "%d_%d_%d.png" % (z, x, y))


LOKASI_JSON = os.path.expanduser("~/.config/rovgui/peta_lokasi.json")


def lokasi_preset(path=None):
    """{nama: (lat, lon)} dari daftar lokasi GUI (menu Operator ▸ Peta: pergi ke lokasi)."""
    import json
    p = path or LOKASI_JSON
    try:
        with open(p) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: (float(v[0]), float(v[1])) for k, v in d.items() if len(v) >= 2}
