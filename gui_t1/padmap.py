"""padmap.py — GAMEPAD MAPPING page (owner request 13 Oct).

Shows a PICTURE of the controller with game-style button names (LB / RB / left
stick / D-PAD ...), so users can see exactly which physical control each mapping
means ("LY [1] is which one?").

- CHANGE captures the next pressed button / moved axis (live from the pad).
- The picture is INTERACTIVE: pressing buttons / moving sticks on the real pad
  lights up the matching element on the drawing in real time.
- SAVE AS… stores the current mapping as a named preset (e.g. "Custom A");
  the active preset loads automatically on the next start.
- Presets live in gamepad_presets.json next to the app.

All UI text in this module is English (company rule).
"""
import json
import os

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt
import theme

# (cfg key, title, kind, tooltip description, remap-highlight target)
# target: element id string or list; "auto_btn"/"auto_axis" = derive from cfg value
FUNGSI = (
    ("axis_fwd",   "Forward / back",        "axis",
     "Push the LEFT STICK up / down (shown as LY [1])",
     "stick_l"),
    ("axis_lat",   "Strafe left / right",   "axis",
     "Push the LEFT STICK left / right (shown as LX [0])",
     "stick_l"),
    ("axis_depth", "Ascend / descend",      "axis",
     "Push the RIGHT STICK up / down (shown as RY [4])",
     "stick_r"),
    ("axis_yaw",   "Turn (yaw)",            "axis",
     "Push the RIGHT STICK left / right (shown as RX [3])",
     "stick_r"),
    ("axis_tilt",  "Camera tilt",           "axis",
     "D-PAD up / down tilts the camera (dpadY [7])",
     "dpad"),
    ("deadman_buttons", "DEADMAN — hold to move", "tombol",
     "Hold LB or RB (L1 / R1): no thrust reaches the ROV unless held",
     ["lb", "rb"]),
    ("btn_arm_toggle",  "ARM / DISARM",     "tombol",
     "Face buttons — △ = Y (top) · ○ = B (right) · × = A (bottom) · □ = X (left)",
     "auto_btn"),
    ("btn_mode_cycle",  "Cycle flight mode", "tombol",
     "SHARE (Select) — MANUAL → ALT_HOLD → back",
     "auto_btn"),
    ("btn_depth_hold",  "Depth hold — hold", "tombol",
     "Hold LT (L2) = ALT_HOLD; release = back to MANUAL",
     "auto_btn"),
    ("btn_center_cam",  "Camera back to center", "tombol",
     "Press the button = camera tilt returns to 50%",
     "auto_btn"),
    ("btn_record",  "Record video (toggle)", "tombol",
     "Press = start / stop recording the front camera (default □ — X left)",
     "auto_btn"),
    ("btn_photo",   "Take photo (snapshot)", "tombol",
     "Press = save a snapshot of the front camera (default ○ — B right)",
     "auto_btn"),
)

_TOMBOL_EN = (
    "× (A, bottom)", "○ (B, right)", "△ (Y, top)", "□ (X, left)",
    "LB (L1)", "RB (R1)", "LT (L2)", "RT (R2)",
    "SHARE (Select)", "OPTIONS (Start)", "PS", "L3 (stick press)",
    "R3 (stick press)",
)
_SUMBU_EN = (
    "LX — left stick ←/→", "LY — left stick ↑/↓", "L2 trigger axis",
    "RX — right stick ←/→", "RY — right stick ↑/↓", "R2 trigger axis",
    "D-PAD ←/→", "D-PAD ↑/↓",
)

# raw button index -> diagram element id
_BTN_ELEMEN = {0: "b0", 1: "b1", 2: "b2", 3: "b3", 4: "lb", 5: "rb",
               6: "lt", 7: "rt", 8: "share", 9: "options", 11: "l3", 12: "r3"}
# raw axis index -> diagram element id
_AX_ELEMEN = {0: "stick_l", 1: "stick_l", 3: "stick_r", 4: "stick_r",
              6: "dpad", 7: "dpad"}


def _acc():
    c = theme.warna("accent")
    return c if isinstance(c, QtGui.QColor) else QtGui.QColor(c)


def _dgn_alpha(c, a):
    return QtGui.QColor(c.red(), c.green(), c.blue(), a)


def _nama_tombol(idx):
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return str(idx)
    if idx < 0:
        return "not assigned"
    if 0 <= idx < len(_TOMBOL_EN):
        return "%s [%d]" % (_TOMBOL_EN[idx], idx)
    return "[%d]" % idx


def _nama_sumbu(idx):
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        return str(idx)
    if idx < 0:
        return "not assigned"
    if 0 <= idx < len(_SUMBU_EN):
        return "%s [%d]" % (_SUMBU_EN[idx], idx)
    return "[%d]" % idx


class PadDiagram(QtWidgets.QWidget):
    """Professional top-down drawing of the DualShock pad + LIVE interactivity.

    - set_live(axes, buttons): lights up whatever is being held on the real pad.
    - set_tandai(ids): highlights the element(s) being remapped (list or str).
    Canvas coordinates: 500 x 280 units, scaled to the widget by `f`.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hl = []          # highlight dari remap
        self._ax = [0.0] * 8   # live axes (ternormalisasi)
        self._btn = [False] * 13
        self._ada_live = False
        self.setMinimumSize(theme.px(480), theme.px(300))

    # ------------------------------------------------------------- API publik
    def set_tandai(self, ids):
        if ids is None:
            self._hl = []
        elif isinstance(ids, (list, tuple, set)):
            self._hl = list(ids)
        else:
            self._hl = [ids]
        self.update()

    def set_live(self, axes, buttons):
        try:
            if axes is not None:
                self._ax = [float(v) for v in axes]
            if buttons is not None:
                self._btn = [bool(b) for b in buttons]
            self._ada_live = True
            self.update()
        except Exception:                              # noqa: BLE001
            pass

    def _nyala(self, elemen):
        """True bila elemen harus menyala (sedang ditekan / sedang diremap)."""
        if elemen in self._hl:
            return True
        if not self._ada_live:
            return False
        for idx, el in _BTN_ELEMEN.items():
            if el == elemen and idx < len(self._btn) and self._btn[idx]:
                return True
        return False

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _ev):                          # noqa: N802 (Qt)
        """Gaya SDL2 Gamepad Tool (referensi pemilik 13 Okt): line-art terang,
        outline tegas, TANPA label kecil di gambar — elemen diidentifikasi lewat
        posisi + sorotan aksen saat disentuh/diremap."""
        p = QtGui.QPainter(self)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            acc = _acc()
            f = min(self.width() / 520.0, self.height() / 305.0)
            ox = (self.width() - 520.0 * f) / 2.0
            oy = (self.height() - 305.0 * f) / 2.0

            def R(x, y, w, h):
                return QtCore.QRectF(ox + x * f, oy + y * f, w * f, h * f)

            OL = QtGui.QColor(14, 17, 21)          # outline gelap
            BODY = QtGui.QColor(224, 228, 233)     # badan terang
            DETAIL = QtGui.QColor(246, 248, 250)   # detail terang
            SHADE = QtGui.QColor(199, 205, 212)    # shading lembut
            GLYPH = QtGui.QColor(62, 68, 76)       # simbol tombol

            def wpen(on=False, lebar=2.2):
                return QtGui.QPen(acc if on else OL, (lebar + 0.9) if on else lebar)

            def wbrush(dasar, on=False, alfa=150):
                return _dgn_alpha(acc, alfa) if on else dasar

            def teks_kecil(r, txt, px=7.0, warna=None):
                p.setPen(warna or GLYPH)
                ft = p.font()
                ft.setPixelSize(max(7, int(theme.px(px))))
                ft.setWeight(QtGui.QFont.Weight.DemiBold)
                p.setFont(ft)
                p.drawText(r, Qt.AlignCenter, txt)

            def teks_glyph(r, txt, on):
                p.setPen(OL if on else GLYPH)          # di atas aksen: gelap agar kontras
                ft = p.font()
                ft.setPixelSize(max(10, int(theme.px(12.5))))
                ft.setWeight(QtGui.QFont.Weight.Bold)
                p.setFont(ft)
                p.drawText(r, Qt.AlignCenter, txt)

            # ---------- siluet badan: union rect pusat + 2 grip (dirotasi) ----------
            tubuh = QtGui.QPainterPath()
            tubuh.addRoundedRect(R(112, 46, 296, 148), 54 * f, 54 * f)
            for masuk in (1, -1):
                cx = 260 + masuk * 128
                t = QtGui.QTransform()
                t.translate(ox + cx * f, oy + 192 * f)
                t.rotate(masuk * 16)
                t.translate(-(ox + cx * f), -(oy + 192 * f))
                q = QtGui.QPainterPath()
                q.addRoundedRect(R(cx - 56, 100, 112, 166), 56 * f, 56 * f)
                tubuh = tubuh.united(t.map(q))
            p.setPen(QtGui.QPen(OL, 2.2))
            p.setBrush(BODY)
            p.drawPath(tubuh)

            # ---------- bahu: trigger (pilin tinggi) + bumper (bar) ----------
            for xs, tg, bm in ((168, "lt", "lb"), (280, "rt", "rb")):
                on_t = self._nyala(tg)
                p.setPen(wpen(on_t))
                p.setBrush(wbrush(SHADE, on_t))
                p.drawRoundedRect(R(xs, 8, 72, 20), 10 * f, 10 * f)
                teks_kecil(R(xs, 8, 72, 20), "LT (L2)" if tg == "lt" else "RT (R2)")
                on_b = self._nyala(bm)
                p.setPen(wpen(on_b))
                p.setBrush(wbrush(DETAIL, on_b))
                # menempel ke tepi atas body (area datar x168..352, top y46)
                p.drawRoundedRect(R(xs, 30, 72, 20), 9 * f, 9 * f)
                teks_kecil(R(xs, 30, 72, 20), "LB (L1)" if bm == "lb" else "RB (R1)")

            # ---------- touchpad + PS + share/options ----------
            on_tp = self._nyala("touchpad")
            p.setPen(wpen(on_tp))
            p.setBrush(wbrush(SHADE, on_tp))
            p.drawRoundedRect(R(224, 62, 72, 48), 9 * f, 9 * f)
            teks_kecil(R(224, 62, 72, 48), "TOUCHPAD", px=6.5)
            p.setPen(QtGui.QPen(_dgn_alpha(GLYPH, 90), 1.2))
            p.drawLine(R(232, 104, 56, 1).topLeft(), R(232, 104, 56, 1).topRight())
            for xs, el in ((178, "share"), (306, "options")):
                on = self._nyala(el)
                p.setPen(wpen(on, 1.8))
                p.setBrush(wbrush(DETAIL, on))
                p.drawRoundedRect(R(xs, 66, 36, 16), 8 * f, 8 * f)
                teks_kecil(R(xs, 66, 36, 16),
                           "SHARE" if el == "share" else "OPTIONS", px=6.0)
            p.setPen(QtGui.QPen(OL, 2.0))
            p.setBrush(DETAIL)
            p.drawEllipse(R(246, 120, 28, 28))
            p.setPen(QtGui.QPen(SHADE, 2.2))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(R(251, 125, 18, 18))
            teks_kecil(R(246, 150, 28, 11), "PS", px=7.0)

            # ---------- D-PAD (cross; panah menyala per-arah) ----------
            on_d = self._nyala("dpad")
            dl = self._ax[6] if len(self._ax) > 6 else 0.0   # HAT0X: -1 kiri / +1 kanan
            du = self._ax[7] if len(self._ax) > 7 else 0.0   # HAT0Y: -1 atas / +1 bawah
            cross = QtGui.QPainterPath()
            cross.addRoundedRect(R(139, 92, 22, 78), 7 * f, 7 * f)
            cross.addRoundedRect(R(111, 120, 78, 22), 7 * f, 7 * f)
            p.setPen(QtGui.QPen(OL, 2.2))
            p.setBrush(DETAIL)
            p.drawPath(cross)
            teks_kecil(R(105, 68, 66, 12), "D-PAD", px=7.5)
            for (x, y, w2, h2), aktif in (((139, 92, 22, 28), du < -0.5),
                                          ((139, 142, 22, 28), du > 0.5),
                                          ((111, 120, 28, 22), dl < -0.5),
                                          ((161, 120, 28, 22), dl > 0.5)):
                if aktif or on_d:
                    p.setPen(wpen(False, 1.4))
                    p.setBrush(_dgn_alpha(acc, 165))
                    p.drawRoundedRect(R(x, y, w2, h2), 6 * f, 6 * f)

            # ---------- tombol face (bentuk DS4, tanpa label tambahan) ----------
            for cx, cy, el, gl in ((370, 92, "b2", "\u25b3"),
                                   (398, 120, "b1", "\u25cb"),
                                   (370, 148, "b0", "\u00d7"),
                                   (342, 120, "b3", "\u25a1")):
                on = self._nyala(el)
                p.setPen(wpen(on, 2.0))
                p.setBrush(wbrush(DETAIL, on))
                p.drawEllipse(R(cx - 15, cy - 15, 30, 30))
                teks_glyph(R(cx - 15, cy - 15, 30, 30), gl, on)

            # ---------- analog stick: dish cekung + knob LIVE ----------
            for cx, cy, el, el3, ax_i, ay_i in (
                    (152, 200, "stick_l", "l3", 0, 1),
                    (368, 200, "stick_r", "r3", 3, 4)):
                on = self._nyala(el) or self._nyala(el3)
                dx = self._ax[ax_i] if len(self._ax) > ax_i else 0.0
                dy = self._ax[ay_i] if len(self._ax) > ay_i else 0.0
                p.setPen(wpen(on, 2.2))
                p.setBrush(wbrush(DETAIL, on))
                p.drawEllipse(R(cx - 27, cy - 27, 54, 54))
                p.setPen(QtGui.QPen(SHADE, 2.0))
                p.setBrush(SHADE)
                p.drawEllipse(R(cx - 17, cy - 17, 34, 34))
                p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 165), 2.0))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawArc(R(cx - 15, cy - 15, 30, 30), 40 * 16, 100 * 16)  # kilau cekung
                aktif_knob = on or abs(dx) > 0.15 or abs(dy) > 0.15
                p.setPen(QtGui.QPen(OL, 1.6))
                p.setBrush(_dgn_alpha(acc, 190) if aktif_knob else BODY)
                kx = cx + max(-12.0, min(12.0, dx * 12.0))
                ky = cy + max(-12.0, min(12.0, dy * 12.0))
                p.drawEllipse(R(kx - 12, ky - 12, 24, 24))
                p.setPen(QtGui.QPen(_dgn_alpha(GLYPH, 120), 1.2))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawEllipse(R(kx - 4, ky - 4, 8, 8))
                if self._nyala(el3):
                    p.setPen(wpen(True, 2.0))
                    p.drawEllipse(R(cx - 13, cy - 13, 26, 26))
                teks_kecil(R(cx - 46, cy + 30, 92, 12),
                           "LEFT STICK" if el == "stick_l" else "RIGHT STICK", px=7.5)
        finally:
            p.end()


class PadMapDialog(QtWidgets.QDialog):
    """See + change + save the pad mapping (named presets)."""

    def __init__(self, gamepad, parent=None):
        super().__init__(parent)
        self.gamepad = gamepad
        self.setModal(False)
        # ukuran mengikuti layar: jangan pernah lebih besar dari area tersedia
        # (audit 13 Okt: di layar laptop 768p dialog versi lama terpotong di bawah)
        try:
            scr = QtWidgets.QApplication.primaryScreen().availableGeometry()
            self.resize(max(theme.px(700), min(theme.px(900), scr.width() - theme.px(40))),
                        max(theme.px(460), min(theme.px(560), scr.height() - theme.px(60))))
        except Exception:                              # noqa: BLE001
            self.resize(theme.px(900), theme.px(560))
        t = QtWidgets.QLabel("GAMEPAD MAPPING — see & change every control")
        t.setObjectName("PanelTitle")
        ket = QtWidgets.QLabel(
            "Press any button or move a stick on the real pad — the picture lights up "
            "live. To remap: click CHANGE on a row, then press the new button / move "
            "the new stick. Save it as a named preset (e.g. \u201cCustom A\u201d).")
        ket.setWordWrap(True)
        theme.set_peran(ket, "dim")
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(t)
        v.addWidget(ket)

        tengah = QtWidgets.QHBoxLayout()
        tengah.setSpacing(theme.px(12))
        self.gambar = PadDiagram()
        tengah.addWidget(self.gambar, 0)

        self.grid = QtWidgets.QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.lbl = {}
        self.tipe = {}
        self.tandai = {}
        baris_no = 0
        for key, label, tipe, tip, hl in FUNGSI:
            l1 = QtWidgets.QLabel(label)
            l1.setObjectName("RowLabel")
            l1.setToolTip(tip)
            l2 = QtWidgets.QLabel("—")
            l2.setObjectName("RowValue")
            b = QtWidgets.QPushButton("CHANGE")
            b.setObjectName("BtnTiny")
            b.setToolTip("click, then press the button / move the stick you want")
            b.clicked.connect(lambda _=False, k=key: self._mulai_tangkap(k))
            self.grid.addWidget(l1, baris_no, 0)
            self.grid.addWidget(l2, baris_no, 1)
            self.grid.addWidget(b, baris_no, 2)
            self.lbl[key] = l2
            self.tipe[key] = tipe
            self.tandai[key] = hl
            baris_no += 1
        # grid mapping dibungkus SCROLL (audit 13 Okt: dialog harus muat di layar
        # laptop 768p — tinggi tetap, isi baris digulung bila perlu)
        isi = QtWidgets.QWidget()
        isi.setLayout(self.grid)
        gulung = QtWidgets.QScrollArea()
        gulung.setWidgetResizable(True)
        gulung.setFrameShape(QtWidgets.QFrame.NoFrame)
        gulung.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        gulung.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        gulung.setWidget(isi)
        gulung.setMinimumWidth(theme.px(390))
        tengah.addWidget(gulung, 1)
        self.gulung = gulung
        v.addLayout(tengah)

        legenda = QtWidgets.QLabel(
            "LEFT STICK = LX [0] ←/→ · LY [1] ↑/↓   |   RIGHT STICK = RX [3] ←/→ · "
            "RY [4] ↑/↓   |   D-PAD = camera tilt [7]   |   LB/RB (L1/R1) = deadman   |   "
            "LT (L2) = depth-hold. Numbers in [ ] are the raw pad indexes.")
        legenda.setWordWrap(True)
        theme.set_peran(legenda, "faint")
        v.addWidget(legenda)

        self.lbl_status = QtWidgets.QLabel("")
        self.lbl_status.setObjectName("MicroLabel")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        lab_p = QtWidgets.QLabel("PRESET")
        lab_p.setObjectName("MicroLabel")
        self.cb_preset = QtWidgets.QComboBox()
        self.cb_preset.setToolTip("saved pad presets (including your own)")
        b_apply = QtWidgets.QPushButton("APPLY")
        b_apply.setObjectName("BtnTiny")
        b_apply.clicked.connect(self._terapkan)
        b_save = QtWidgets.QPushButton("SAVE AS…")
        b_save.setObjectName("BtnTiny")
        b_save.setToolTip("save the current mapping as a preset with a free name")
        b_save.clicked.connect(self._simpan)
        row.addWidget(lab_p)
        row.addWidget(self.cb_preset, 1)
        row.addWidget(b_apply)
        row.addWidget(b_save)
        v.addLayout(row)
        row_close = QtWidgets.QHBoxLayout()
        row_close.addStretch(1)
        b_close = QtWidgets.QPushButton("CLOSE")
        b_close.setObjectName("BtnTiny")
        b_close.setToolTip("close this page")
        b_close.clicked.connect(self.close)
        row_close.addWidget(b_close)
        v.addLayout(row_close)
        v.addSpacing(theme.px(2))
        self.b_close = b_close          # dipakai probe/test utk cek margin bawah

        self._tangkap = None
        self._prev_btn = set()
        self._prev_axes = None
        try:
            self.gamepad.sig_live.connect(self._live)
        except Exception:                              # noqa: BLE001
            pass
        # MODE MAPPING (permintaan pemilik 13 Okt): selama halaman ini terbuka,
        # input pad TIDAK boleh menggerakkan ROV — mis. D-PAD atas tidak men-tilt
        # kamera. Output diblokir sampai dialog ditutup.
        try:
            self.gamepad.set_output_block(True)
        except Exception:                              # noqa: BLE001
            pass
        self.finished.connect(self._lepas_blokir)
        self._muat_presets()
        self._segarkan()

    def _lepas_blokir(self, *_a):
        try:
            self.gamepad.set_output_block(False)
        except Exception:                              # noqa: BLE001
            pass

    def closeEvent(self, ev):                          # noqa: N802 (Qt)
        # semua jalur tutup (tombol CLOSE, X jendela, ESC) WAJIB melepas blokir;
        # `finished` tidak selalu terpancar utk dialog non-modal yang di-close().
        self._lepas_blokir()
        super().closeEvent(ev)

    # ------------------------------------------------------------------ preset
    @property
    def path(self):
        return getattr(self.gamepad, "presets_path",
                       os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "gamepad_presets.json"))

    def _baca_file(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("presets", {})
            return data
        except Exception:                              # noqa: BLE001
            return {"aktif": None, "presets": {}}

    def _muat_presets(self):
        data = self._baca_file()
        self.cb_preset.clear()
        self.cb_preset.addItem("Default (built-in)", "Default")
        for nama in sorted(data["presets"]):
            if nama != "Default":
                self.cb_preset.addItem(nama, nama)
        aktif = data.get("aktif")
        if aktif:
            i = self.cb_preset.findData(aktif)
            if i >= 0:
                self.cb_preset.setCurrentIndex(i)

    def _terapkan(self):
        nama = self.cb_preset.currentData()
        data = self._baca_file()
        if nama and nama != "Default":
            ganti = (data["presets"] or {}).get(nama)
            if isinstance(ganti, dict):
                self.gamepad.terapkan_cfg(ganti)
        data["aktif"] = nama
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:                              # noqa: BLE001
            pass
        self.lbl_status.setText("active preset: %s" % nama)
        self._segarkan()

    def _simpan(self):
        nama, ok = QtWidgets.QInputDialog.getText(
            self, "Save pad preset", "Preset name (free, e.g. “Custom A”):")
        if not ok or not nama.strip():
            return
        nama = nama.strip()[:24]
        if self.gamepad.simpan_preset(nama):
            self.lbl_status.setText("saved as preset “%s” (now active)" % nama)
            self._muat_presets()
        else:
            self.lbl_status.setText("could not save the preset")

    # ------------------------------------------------------------------ remap
    def _target_elemen(self, key):
        """Elemen diagram yang harus menyala saat baris `key` dipilih utk remap."""
        hl = self.tandai.get(key)
        try:
            if hl == "auto_btn":
                val = int(self.gamepad.cfg.get(key, -1))
                return [_BTN_ELEMEN[val]] if val in _BTN_ELEMEN else []
            if hl == "auto_axis":
                val = int(self.gamepad.cfg.get(key, -1))
                return [_AX_ELEMEN[val]] if val in _AX_ELEMEN else []
        except (TypeError, ValueError):
            return []
        if isinstance(hl, (list, tuple)):
            return list(hl)
        return [hl] if hl else []

    def _mulai_tangkap(self, key):
        self._tangkap = key
        self._prev_axes = None
        for _k, l in self.lbl.items():
            l.setStyleSheet("")
        self.lbl[key].setStyleSheet("color:%s; font-weight:800" % theme.warna("accent"))
        self.gambar.set_tandai(self._target_elemen(key))
        self.lbl_status.setText("WAITING FOR INPUT — press a button / move an axis "
                                "for “%s”…" % key)
        try:
            self.gamepad.sig_live.connect(self._live)
        except Exception:                              # noqa: BLE001
            pass

    def _selesai_tangkap(self, teks):
        self._tangkap = None
        self.gambar.set_tandai(None)
        self.lbl_status.setText("changed: " + teks)
        self._segarkan()

    def _live(self, live):
        try:
            self.gambar.set_live(live.get("axes"), live.get("buttons"))
        except Exception:                              # noqa: BLE001
            pass
        try:
            if self._tangkap is None:
                return
            key = self._tangkap
            if self.tipe.get(key) == "tombol":
                pressed = {i for i, b in enumerate(live.get("buttons") or []) if b}
                baru = pressed - self._prev_btn
                self._prev_btn = pressed
                if baru:
                    idx = min(baru)
                    if key == "deadman_buttons":
                        self.gamepad.cfg[key] = [idx]
                    else:
                        self.gamepad.cfg[key] = idx
                    self._selesai_tangkap("%s → %s" % (key, _nama_tombol(idx)))
            else:
                axes = live.get("axes") or []
                if self._prev_axes is None or len(axes) != len(self._prev_axes):
                    self._prev_axes = list(axes)
                    return
                best = None
                for i, (a, b) in enumerate(zip(axes, self._prev_axes)):
                    d = abs(float(a) - float(b))
                    if d > 0.6 and (best is None or d > best[1]):
                        best = (i, d)
                self._prev_axes = list(axes)
                if best is not None:
                    self.gamepad.cfg[key] = int(best[0])
                    self._selesai_tangkap("%s → %s" % (key, _nama_sumbu(best[0])))
        except Exception:                              # noqa: BLE001
            pass

    # ------------------------------------------------------------------ tampil
    def _segarkan(self):
        g = self.gamepad.cfg
        for key, _label, tipe, _tip, _hl in FUNGSI:
            try:
                val = g.get(key)
                if tipe == "axis":
                    teks = _nama_sumbu(val)
                elif key == "deadman_buttons":
                    teks = " or ".join(_nama_tombol(x) for x in (val or []))
                    if not teks:
                        teks = "—"
                else:
                    teks = _nama_tombol(val)
            except Exception:                          # noqa: BLE001
                teks = "—"
            self.lbl[key].setText(teks)
