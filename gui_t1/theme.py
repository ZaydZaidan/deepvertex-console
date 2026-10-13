"""Token desain + stylesheet ROV Mission Console (DeepVertex C62).

Satu berkas ini adalah SATU-SATUNYA sumber warna & rupa huruf untuk seluruh GUI.
Empat palet tersedia dan bisa diganti saat aplikasi berjalan:

  legacy  — tampilan hari ini apa adanya (jalan pulang; ambar #F0A92E)
  abyss   — field gelap brand (Abyss #14304D)      <- BAWAAN
  ink     — field hitam netral (mark brand #14171A)
  ivory   — field terang brand (Ivory #F1EDE6)

Prinsip (sengaja menghindari "AI slop"):
  - Tanpa gradien, tanpa drop-shadow, sudut maksimum 2 px, garis pemisah 1 px.
  - Satu aksen fungsi (cobalt) untuk data hidup; warna lain hanya PENANDA DOMAIN
    dan selalu disertai tulisan (warna tidak pernah jadi satu-satunya pembawa makna).
  - Label mikro UPPERCASE 10 px dengan letter-spacing; angka memakai digit tabular.
  - Jarak mengikuti grid 8 px; kepadatan informasi tinggi seperti panel instrumen.

Nilai hex & rasio kontrasnya diambil dari rancangan terukur
(`ROVGUI_REDESIGN/palet_dvx.py` + `MOCKUP_FINAL.html`); JANGAN diubah tanpa mengukur
ulang — skrip pengukurnya ada di berekas uji kontras (`Progress/tools/uji_kontras_gui.py`).

Kompatibilitas: nama token LAMA (BG, PANEL, ACCENT, TEXT_DIM, ...) tetap bisa dipakai
(`theme.TEXT_DIM`) dan SELALU mengembalikan nilai palet AKTIF (module __getattr__),
sehingga kode lama tidak perlu diubah sekaligus dan tetap ikut berganti tema.
"""

import glob
import os
import re
from string import Template

# --------------------------------------------------------------------------- #
# 1. PALET
# --------------------------------------------------------------------------- #
# Kunci token (huruf kecil) — dipakai QSS lewat $nama.
LEGACY = dict(
    field="#0A0C0E", panel="#111418", head="#151A1F", raise_="#151A1F",
    line="#232A31", line_hi="#303A44",
    ink="#E8EBEE", ink_dim="#9AA5B1", ink_faint="#6E7A87",
    accent="#00E5FF", accent_text="#00E5FF", accent_tint="#0F3138", accent_dim="#00748A",
    on_accent="#04252C",
    ok="#5CC98F", warn="#E0C158", err="#E06060",
    gt="#6E7A88", gt_dim="#333B44", cyan="#4FB3D9",
    coral="#DE6E5B", orange="#E2542A", brass="#A8823F", lagoon="#00A6B8",
    teal="#448186", stone="#6E7A88",
    sky="#16323F", ground="#0D1114", horizon="#E8EBEE", vid="#05070A",
)

ABYSS = dict(
    field="#0E2236", panel="#14304D", head="#1F3C5A", raise_="#1B3A5A",
    line="#33526F", line_hi="#41628A",
    ink="#F1EDE6", ink_dim="#AFB4B8", ink_faint="#8B97A2",
    accent="#00E5FF", accent_text="#00E5FF", accent_tint="#0F3E4D", accent_dim="#00748A",
    on_accent="#04252C",
    ok="#5CC98F", warn="#E0C158", err="#E47474",
    gt="#7A7770", gt_dim="#41628A", cyan="#00A6B8",
    coral="#DE6E5B", orange="#E2542A", brass="#A8823F", lagoon="#00A6B8",
    teal="#448186", stone="#7A7770",
    sky="#14405C", ground="#0B1A2A", horizon="#F1EDE6", vid="#080C12",
)

INK = dict(
    field="#0A0C0E", panel="#14171A", head="#262B31", raise_="#1D2126",
    line="#343B43", line_hi="#3E464F",
    ink="#F1EDE6", ink_dim="#AFADA9", ink_faint="#9AA0A6",
    accent="#00E5FF", accent_text="#00E5FF", accent_tint="#11343A", accent_dim="#00748A",
    on_accent="#04252C",
    ok="#5CC98F", warn="#E0C158", err="#E06060",
    gt="#7A7770", gt_dim="#3E464F", cyan="#00A6B8",
    coral="#DE6E5B", orange="#E2542A", brass="#A8823F", lagoon="#00A6B8",
    teal="#448186", stone="#7A7770",
    sky="#1F2A33", ground="#12161A", horizon="#F1EDE6", vid="#080C12",
)

IVORY = dict(
    field="#F1EDE6", panel="#FCFAF9", head="#F3F0EA", raise_="#FFFFFF",
    line="#DCD7CD", line_hi="#C2BEB4",
    ink="#14171A", ink_dim="#565757", ink_faint="#686866",
    accent="#00748A", accent_text="#00748A", accent_tint="#D9E7E9", accent_dim="#BFDCDE",
    on_accent="#F1EDE6",
    ok="#3B815C", warn="#6F5E24", err="#BD5151",
    gt="#57534B", gt_dim="#C2BEB4", cyan="#00A0B2",
    coral="#D2543C", orange="#E2542A", brass="#A8823F", lagoon="#00A0B2",
    teal="#0E5C63", stone="#57534B",
    # Permukaan kotak citra saat belum ada gambar: di tema terang TIDAK boleh hitam
    # (permintaan pemilik 28 Sep) — dipakai warna ivory yang diredupkan tipis sehingga
    # tulisan statusnya tetap lolos ambang teks (TEXT_FAINT 4,5:1 di atas permukaan ini).
    sky="#BFDCEA", ground="#D9D1C3", horizon="#14304D", vid="#EDE9E1",
)

PALET = {"legacy": LEGACY, "abyss": ABYSS, "ink": INK, "ivory": IVORY}
LABEL = {"legacy": "Legacy · amber (as-is)", "abyss": "Dark · abyss",
         "ink": "Ink · black", "ivory": "Light · ivory"}
BAWAAN = "abyss"                     # field bawaan (keputusan pemilik 28 Sep 2026)
GELAP = ("legacy", "abyss", "ink")   # dipakai uji kecerahan piksel per mode

# ------------------------------------------------- skala teks (keterbacaan field)
# Untuk dipakai di kapal / matahari terik: SEMUA ukuran huruf QSS + px() widget
# dikalikan SKALA (1.0 normal · 1.2 besar · 1.4 ekstra).
SKALA = 1.0


def set_skala(x):
    global SKALA
    try:
        SKALA = max(0.8, min(2.0, float(x)))
    except (TypeError, ValueError):
        SKALA = 1.0
    return SKALA


def px(n):
    """Ukuran piksel yang mengikuti skala teks aktif."""
    return int(round(float(n) * SKALA))

# Penanda domain di kepala panel (kotak 6 px + tulisan).
DOMAIN = {"digital": "accent", "mission": "coral", "field": "orange",
          "session": "brass", "marine": "teal", "neutral": "stone"}
DOMAIN_LABEL = {"digital": "VIO · digital", "mission": "Mission · map", "field": "Flight · pilot",
                "session": "Stack · tuning", "marine": "Power · environment",
                "neutral": "Ground truth · ArduSub"}

# Nama token LAMA -> kunci palet baru (kompatibilitas ke belakang).
_ALIAS = {
    "BG": "field", "PANEL": "panel", "PANEL_ALT": "head", "RAISE": "raise_",
    "HAIRLINE": "line", "HAIRLINE_HI": "line_hi",
    "TEXT": "ink", "TEXT_DIM": "ink_dim", "TEXT_FAINT": "ink_faint",
    "ACCENT": "accent", "ACCENT_TEXT": "accent_text", "ACCENT_TINT": "accent_tint",
    "ACCENT_DIM": "accent_dim", "ON_ACCENT": "on_accent",
    "OK": "ok", "WARN": "warn", "ERR": "err", "GT": "gt", "GT_DIM": "gt_dim",
    "CYAN": "cyan", "SKY": "sky", "GROUND": "ground", "HORIZON": "horizon",
    "CORAL": "coral", "ORANGE": "orange", "BRASS": "brass", "LAGOON": "lagoon",
    "TEAL": "teal", "STONE": "stone", "VID": "vid",
}
# Nama domain -> token warna (untuk penanda kepala panel & legenda).
_N_token = None                      # cache terbalik: kunci palet -> nama token lama

# ---------------------------------------------------------------- keadaan aktif
_aktif = None
_pendengar = []


def daftar():
    """SELURUH palet di kode (dipakai uji kontras + pembekuan nilai legacy)."""
    return ["abyss", "ink", "ivory", "legacy"]


def tampil():
    """Palet yang ditawarkan di GUI. `legacy` TIDAK ditampilkan (permintaan pemilik
    28 Sep 2026: opsi Legacy (as-is) tidak dipakai lagi); nilainya tetap ada di kode
    hanya sebagai acuan uji kontras/pembekuan."""
    return ["abyss", "ink", "ivory"]


def label(nama):
    return LABEL.get(nama, nama)


def aktif(nama=None):
    """Baca (tanpa argumen) atau set (dengan argumen) palet aktif."""
    global _aktif
    if nama is None:
        if _aktif is None:
            _aktif = _dari_lingkungan()
        return _aktif
    if nama not in PALET:
        return aktif()
    _aktif = nama
    return _aktif


def _dari_lingkungan():
    """Palet awal: dari env (proses anak: pid_view/dem3d) atau bawaan."""
    n = os.environ.get("ROVGUI_THEME", "").strip().lower()
    return n if n in PALET else BAWAAN


def warna(token, nama=None):
    """Nilai hex satu token pada palet aktif (atau palet `nama`)."""
    p = PALET.get(nama or aktif(), PALET[BAWAAN])
    if token in p:
        return p[token]
    kunci = _ALIAS.get(token, token.lower())
    return p.get(kunci, p["ink"])


def nilai(nama=None):
    """Salinan seluruh token palet (dipakai uji kontras & QSS)."""
    return dict(PALET.get(nama or aktif(), PALET[BAWAAN]))


def domain(nama_domain):
    """Warna penanda domain ('digital', 'mission', ...)."""
    return warna(DOMAIN.get(nama_domain, "neutral"))


def status(nama_status):
    """Warna status ok/warn/err pada palet aktif (set terang punya nilainya sendiri)."""
    return warna(nama_status)


def token_dari(nilai_warna, nama=None):
    """Kebalikan warna(): 'ok'/'accent'/'#5CC98F' -> nama token di palet `nama`.

    Dipakai widget yang menerima warna dari pemanggil (Meter/Chip): pemanggil lama
    mengirim HEX (mis. theme.OK) — hex itu bisa basi setelah tema berganti, jadi widget
    menyimpannya sebagai TOKEN dan baru mengambil nilainya saat menggambar.
    Bila tidak dikenali, kembalikan string aslinya apa adanya.
    """
    v = str(nilai_warna or "").strip()
    p = PALET.get(nama or aktif(), PALET[BAWAAN])
    if v.lower() in p:
        return v.lower()
    for k, hx in p.items():
        if hx.lower() == v.lower():
            return k
    return v


def hexa(nama_token, default=None):
    """Terima NAMA TOKEN ('ok','accent','accent_text',...) atau HEX mentah -> hex palet aktif.

    Widget yang menyimpan pilihan warna dari pemanggil memakai ini saat menggambar,
    sehingga nilainya selalu mengikuti tema yang sedang aktif (tidak basi).
    """
    if nama_token is None:
        return default
    t = str(nama_token).strip()
    if t.startswith("#") and len(t) in (4, 7, 9):
        return t
    p = PALET.get(aktif(), PALET[BAWAAN])
    k = _ALIAS.get(t.upper(), t.lower())
    return p.get(k, default if default is not None else p["ink"])


# --------------------------------------------------------------------------- #
# 2. RUPA HURUF
# --------------------------------------------------------------------------- #
# Satu rupa huruf untuk seluruh GUI = rupa huruf logo (Plus Jakarta Sans).
# Angka memakai varian turunan `DeepVertex Sans` (digit tabular jadi BAWAAN) karena
# Qt 5.15 tidak punya QFont.setFeature → tanpa ini readout bergeser saat nilai berubah.
KELUARGA = "DeepVertex Sans"
CADANGAN = '"Plus Jakarta Sans", "DejaVu Sans", sans-serif'
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_dimuat = []


def muat_font():
    """Daftarkan seluruh bobot DeepVertex Sans (200-800) untuk proses ini.

    Dipanggil sekali di main() SEBELUM jendela dibuat; aman dipanggil berkali-kali.
    """
    global _dimuat
    if _dimuat:
        return _dimuat
    try:
        from PySide6 import QtGui
        for f in sorted(glob.glob(os.path.join(FONT_DIR, "DeepVertexSans-*.ttf"))):
            i = QtGui.QFontDatabase.addApplicationFont(f)
            for fam in (QtGui.QFontDatabase.applicationFontFamilies(i) if i >= 0 else []):
                if fam not in _dimuat:
                    _dimuat.append(fam)
    except Exception:                                   # noqa: BLE001 - font opsional
        pass
    return _dimuat


def keluarga():
    """Daftar keluarga font untuk QSS (turunan dulu, lalu cadangan sistem)."""
    fams = muat_font()
    utama = fams[0] if fams else KELUARGA
    return '"%s", %s' % (utama, CADANGAN)


# --------------------------------------------------------------------------- #
# 3. QSS
# --------------------------------------------------------------------------- #
_QSS = Template("""
/* IMPORTANT: never give `background` to the QWidget selector. It also matches every child
   (labels, layout containers, scroll viewports), which paints a dark box behind text and
   controls — obvious on the brand palettes where field != panel. Backgrounds belong only to
   the containers that really have a surface: window, header, panel, panel head, status bar,
   menus, tooltip. Everything else stays transparent. NOTE: a plain QWidget ignores a QSS
   background unless WA_StyledBackground is set (see StatusBar/Header/PanelBody). */
QWidget {
    color: $ink;
    font-family: $font;
    font-size: 11px;
}
QMainWindow, QDialog { background: $field; }
QScrollArea, QScrollArea > QWidget, QScrollArea > QWidget > QWidget { background: transparent; }
QToolTip { background: $head; color: $ink; border: 1px solid $line_hi; padding: 3px 6px; }
#Header     { background: $panel; border-bottom: 1px solid $line; }
#HeaderTitle{ font-size: 14px; font-weight: 700; letter-spacing: 2px; color: $ink; }
#HeaderSub  { font-size: 10px; color: $ink_faint; letter-spacing: 0.5px; }
#BrandWord  { font-size: 18px; font-weight: 800; letter-spacing: 0.2px; color: $ink; }
#CtlLabel   { font-size: 10px; letter-spacing: 1.4px; color: $ink_faint; }

#StatusBar  { background: $panel; border-top: 1px solid $line; }
#StatusText { font-size: 10px; color: $ink_dim; }
#StatusNote { font-size: 9px; letter-spacing: 1.2px; color: $warn; }
#StatusSep  { background: $line; }
#StatusGrup { background: transparent; border: none; }

#Panel      { background: $panel; border: 1px solid $line; }
#PanelHead  { background: $head; border-bottom: 1px solid $line; }
#PanelTitle { font-size: 10px; font-weight: 600; letter-spacing: 1.2px; color: $ink_dim; }
#PanelHint  { font-size: 10px; color: $ink_faint; }
#PanelBody  { background: $panel; }

#MicroLabel { font-size: 10px; letter-spacing: 1.2px; color: $ink_faint; }
#Unit       { font-size: 10px; color: $ink_faint; }
#ValueMono  { color: $ink; }
#Separator  { background: $line; }
#RowLabel   { font-size: 10px; color: $ink_dim; }
#RowValue   { font-size: 10px; color: $ink; border-bottom: 1px solid $line; }
#Faint      { font-size: 10px; color: $ink_faint; }
#LogLine    { font-size: 10px; color: $ink_dim; }
#LogName    { font-size: 10px; color: $ink; }
#MapBar     { font-size: 10px; color: $ink_dim; }
#PetaCanvas { background: $panel; }
#SimHolder  { background: $vid; }
#SimPlaceholder { background: $vid; font-size: 10px; letter-spacing: 1.4px; }

/* status text: role set via a dynamic property (theme.set_peran()) */
QLabel[peran="dim"]   { color: $ink_dim; }
QLabel[peran="faint"] { color: $ink_faint; }
QLabel[peran="ink"]   { color: $ink; }
QLabel[peran="accent"]{ color: $accent_text; }
QLabel[peran="ok"]    { color: $ok; }
QLabel[peran="warn"]  { color: $warn; }
QLabel[peran="err"]   { color: $err; }
QLabel[peran="brass"] { color: $brass; }

QScrollArea { border: none; }
QScrollBar:vertical { background: $field; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: $line_hi; min-height: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QScrollBar:horizontal { background: $field; height: 8px; margin: 0; }
QScrollBar::handle:horizontal { background: $line_hi; min-width: 24px; }

/* --- menu bar (desktop-application look, 23 Sep 2026) --- */
QMenuBar {
    background: $panel;
    color: $ink;
    border-bottom: 1px solid $line;
    padding: 1px 4px;
}
QMenuBar::item { background: transparent; padding: 4px 10px; border-radius: 8px; }
QMenuBar::item:selected { background: $head; color: $ink; }
QMenuBar::item:pressed { background: $line; }
QMenu {
    background: $panel;
    color: $ink;
    border: 1px solid $line;
    padding: 4px;
}
QMenu::item { padding: 5px 22px 5px 14px; border-radius: 8px; }
QMenu::item:selected { background: $line_hi; }
QMenu::item:disabled { color: $ink_faint; }
QMenu::separator { height: 1px; background: $line; margin: 4px 6px; }

/* --- buttons: one style for the whole GUI --- */
QPushButton {
    background: $raise_;
    color: $ink;
    border: 1px solid $line_hi;
    border-radius: 8px;
    padding: 3px 10px;
    min-height: 22px;
    font-size: 10px;
    letter-spacing: 0.8px;
}
QPushButton:hover   { border-color: $accent; color: $accent_text; }
QPushButton:pressed { background: $head; border-color: $accent; }
QPushButton:focus   { border-color: $accent; }
QPushButton:disabled { color: $ink_faint; border-color: $line; }
QPushButton:checked { background: $accent_tint; border-color: $accent; color: $ink; }
QPushButton#BtnPrimary {
    background: $accent; border-color: $accent; color: $on_accent;
    font-weight: 700; letter-spacing: 1.4px;
}
QPushButton#BtnPrimary:hover { background: $accent_text; border-color: $accent_text;
                               color: $on_accent; }
QPushButton#BtnPrimary:disabled { background: $raise_; border-color: $line;
                                 color: $ink_faint; }
QPushButton#BtnGhost { background: transparent; border-color: $line; color: $ink_dim; }
QPushButton#BtnGhost:hover { border-color: $accent; color: $accent_text; }
QPushButton#BtnBig {
    min-height: 30px; font-size: 12px; font-weight: 600; letter-spacing: 2px;
}
QPushButton#BtnTiny { min-height: 20px; padding: 0 8px; font-size: 10px; }
/* STACK-row buttons (2 Oct 2026): the component list must stay compact so the owner's
   right rail fits WITHOUT a scrollbar at 1620x978 (test R9_kanan_1620x978). The pilot's
   pan/tilt and log-fold buttons deliberately KEEP the larger BtnTiny size. */
QPushButton#BtnRow { min-height: 16px; padding: 0 6px; font-size: 10px; }
QPushButton#SegBtn {
    background: transparent; border: 1px solid $line; border-radius: 8px;
    padding: 0 10px; min-height: 20px; font-size: 10px; font-weight: 600;
    letter-spacing: 0.8px; color: $ink_dim;
}
QPushButton#SegBtn:checked { background: $accent_tint; color: $accent_text;
                             border-color: $accent; }
QPushButton#SegBtn:hover:!checked { background: $raise_; color: $ink; }
QPushButton#SegBtn:pressed { background: $head; }

/* --- number fields: must READ as editable --- */
QDoubleSpinBox, QSpinBox, QLineEdit {
    background: $raise_; color: $ink; border: 1px solid $line_hi;
    border-radius: 8px; padding: 0 8px; min-height: 20px; font-size: 12px; font-weight: 500;
    selection-background-color: $accent; selection-color: $on_accent;
}
QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus { border-color: $accent; }
QComboBox {
    background: $raise_; color: $ink; border: 1px solid $line_hi;
    border-radius: 8px; padding: 0 8px 0 10px; min-height: 20px; font-size: 11px;
}
QComboBox:focus { border-color: $accent; }
QComboBox QAbstractItemView { background: $panel; color: $ink;
                              border: 1px solid $line; selection-background-color: $line_hi; }
QCheckBox { color: $ink; font-size: 11px; }
QCheckBox::indicator { width: 12px; height: 12px; border: 1px solid $line_hi;
                       background: $raise_; border-radius: 4px; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }

QTabWidget::pane { border: 1px solid $line; background: $panel; }
QTabBar::tab {
    background: $head; color: $ink_dim; border: 1px solid $line;
    padding: 3px 12px; font-size: 10px; letter-spacing: 1px;
}
QTabBar::tab:selected { background: $panel; color: $ink; border-bottom-color: $panel; }

QSplitter::handle { background: $line; }
QHeaderView::section { background: $head; color: $ink_dim; border: 0;
                       border-right: 1px solid $line; padding: 3px 6px; font-size: 10px; }
QTableWidget, QTreeWidget, QListWidget { background: $panel; color: $ink;
                                         gridline-color: $line; border: 1px solid $line; }
QProgressBar { background: $field; border: 1px solid $line; color: $ink; text-align: center; }
QProgressBar::chunk { background: $accent; }

/* mission button: colour follows the MISSION STATE (dynamic property, not inline) */
QPushButton#BtnPrimary[status="run"]  { background: $ok; border-color: $ok; color: $on_accent; }
QPushButton#BtnPrimary[status="done"] { background: $cyan; border-color: $cyan;
                                        color: $on_accent; }
QPushButton#BtnPrimary[status="stop"] { background: $warn; border-color: $warn;
                                        color: $on_accent; }
""")

# Kombinasi objectName + properti `peran`. Dibangkitkan (bukan disalin manual) karena
# selektor ID lebih spesifik daripada selektor atribut di Qt: tanpa baris ini warna
# default #MicroLabel/#Unit/... akan menang atas QLabel[peran="..."].
_PERAN = (("dim", "ink_dim"), ("faint", "ink_faint"), ("ink", "ink"),
          ("accent", "accent_text"), ("ok", "ok"), ("warn", "warn"),
          ("err", "err"), ("brass", "brass"))
_OBJ = ("MicroLabel", "Unit", "HeaderSub", "PanelTitle", "PanelHint", "CtlLabel",
        "StatusText", "StatusNote", "Faint", "LogLine", "LogName", "RowLabel",
        "RowValue", "MapBar", "BrandWord")


def _aturan_peran():
    out = []
    for obj in _OBJ:
        for peran, tok in _PERAN:
            out.append('QLabel#%s[peran="%s"] { color: $%s; }' % (obj, peran, tok))
    return "\n".join(out)


def qss(nama=None):
    """Stylesheet penuh untuk palet `nama` (bawaan: palet aktif)."""
    t = nilai(nama)
    t["font"] = keluarga()
    out = _QSS.substitute(t) + "\n" + Template(_aturan_peran()).substitute(t)
    if SKALA != 1.0:
        # SEMUA ukuran px di QSS ikut SKALA (font, padding, radius, min-height, lebar
        # scrollbar) — proporsi tetap utuh di 100/120/140% (audit grid 13 Okt).
        out = re.sub(r"([0-9]+(?:\.[0-9]+)?)px",
                     lambda m: "%dpx" % px(float(m.group(1))), out)
    return out


def set_prop(w, nama, nilai):
    """Set properti dinamis Qt + paksa gaya dihitung ulang (untuk selektor QSS)."""
    try:
        w.setProperty(nama, nilai)
        _paksa_gaya(w)
    except Exception:                                   # noqa: BLE001
        pass


def _paksa_gaya(w):
    """Paksa Qt menghitung ulang gaya widget (setelah properti dinamis berubah)."""
    try:
        w.style().unpolish(w)
        w.style().polish(w)
        w.update()
    except Exception:                                   # noqa: BLE001
        pass


def set_peran(w, peran):
    """Tetapkan peran warna sebuah label (dim/faint/ink/accent/ok/warn/err) + repolish."""
    try:
        w.setProperty("peran", peran)
        _paksa_gaya(w)
    except Exception:                                   # noqa: BLE001
        pass


def on_ganti(fn):
    """Daftarkan fungsi yang dipanggil setiap tema berganti (mis. apply_theme matplotlib)."""
    if fn not in _pendengar:
        _pendengar.append(fn)
    return fn


def apply(nama, app=None):
    """Ganti palet + terapkan stylesheet ke aplikasi; beri tahu pendengar.

    Mengembalikan nama palet yang benar-benar dipakai.
    """
    nama = aktif(nama)
    os.environ["ROVGUI_THEME"] = nama          # proses anak (pid_view/dem3d) ikut tema ini
    if app is None:
        try:
            from PySide6 import QtWidgets
            app = QtWidgets.QApplication.instance()
        except Exception:                               # noqa: BLE001
            app = None
    if app is not None:
        try:
            app.setStyleSheet(qss(nama))
        except Exception:                               # noqa: BLE001
            pass
    for fn in list(_pendengar):
        try:
            fn(nama)
        except Exception:                               # noqa: BLE001
            pass
    return nama


# --------------------------------------------------------------------------- #
# 4. HELPER GAYA (nama lama dipertahankan supaya 20+ pemanggil tidak perlu diubah)
# --------------------------------------------------------------------------- #
def mono(size, weight=400):
    """Gaya angka: rupa huruf turunan (digit tabular) pada ukuran tertentu."""
    return ('font-family: %s; font-size: %dpx; font-weight: %d;'
            % (keluarga(), px(size), int(weight)))


def micro(size=10, color=None):
    """Label mikro UPPERCASE; lantai ukuran 10 px (rancangan §7.5)."""
    return "font-size: %dpx; letter-spacing: 1.2px; color: %s;" % (
        px(max(10, size)), color or warna("ink_faint"))


# --------------------------------------------------------------------------- #
# 5. KOMPATIBILITAS: nama token lama SELALU mengikuti palet aktif
# --------------------------------------------------------------------------- #
def __getattr__(nama):
    """`theme.TEXT_DIM`, `theme.QSS`, `theme.FONT_UI` -> nilai palet AKTIF (bukan salinan)."""
    if nama in _ALIAS:
        return warna(_ALIAS[nama])
    if nama == "QSS":
        return qss()
    if nama in ("FONT_UI", "FONT_MONO"):
        return KELUARGA
    if nama.startswith("DOMAIN_"):
        return DOMAIN.get(nama[7:].lower(), "neutral")
    raise AttributeError("theme has no token %r" % nama)
