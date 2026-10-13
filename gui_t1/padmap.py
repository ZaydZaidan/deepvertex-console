"""padmap.py — GAMEPAD MAPPING page (owner request 13 Oct).

Shows a PICTURE of the controller (like the BlueROV2 operation guide diagram)
with game-style button names (LB / RB / left stick / D-PAD ...), so users can
see exactly which physical control each mapping means ("LY [1] is which one?").

- CHANGE captures the next pressed button / moved axis (live from the pad).
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

# (cfg key, title, kind, tooltip description, diagram highlight target)
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
     "bumpers"),
    ("btn_arm_toggle",  "ARM / DISARM",     "tombol",
     "Face buttons — △ = Y (top) · ○ = B (right) · × = A (bottom) · □ = X (left)",
     "face"),
    ("btn_mode_cycle",  "Cycle flight mode", "tombol",
     "SHARE (Select) — MANUAL → ALT_HOLD → back",
     "share"),
    ("btn_depth_hold",  "Depth hold — hold", "tombol",
     "Hold LT (L2) = ALT_HOLD; release = back to MANUAL",
     "triggers"),
    ("btn_center_cam",  "Camera back to center", "tombol",
     "Press the button = camera tilt returns to 50%",
     "face"),
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
    """Top-down drawing of the DualShock pad with game-style labels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tandai = None
        self.setMinimumSize(theme.px(440), theme.px(220))

    def set_tandai(self, target):
        self.tandai = target
        self.update()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, _ev):                          # noqa: N802 (Qt)
        p = QtGui.QPainter(self)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            acc = _acc()
            f = min(self.width() / 440.0, self.height() / 220.0)
            ox = (self.width() - 440.0 * f) / 2.0
            oy = (self.height() - 220.0 * f) / 2.0

            def R(x, y, w, h):
                return QtCore.QRectF(ox + x * f, oy + y * f, w * f, h * f)

            def teks_kecil(r, txt, warna=(235, 235, 235, 190), px=8.5):
                p.setPen(QtGui.QColor(*warna))
                ft = p.font()
                ft.setPixelSize(max(7, int(theme.px(px)) - 2))
                p.setFont(ft)
                p.drawText(r, Qt.AlignCenter, txt)

            def kotak(x, y, w, h, txt=None, tandai=False, px=7.5):
                r = R(x, y, w, h)
                p.setPen(QtGui.QPen(acc if tandai else QtGui.QColor(255, 255, 255, 60),
                                    1.6 if tandai else 1.0))
                p.setBrush(_dgn_alpha(acc, 70) if tandai
                           else QtGui.QColor(255, 255, 255, 14))
                p.drawRoundedRect(r, 6 * f, 6 * f)
                if txt:
                    teks_kecil(r, txt, (240, 240, 240, 220) if not tandai else acc.getRgb())

            # body
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 52), 1.2))
            p.setBrush(QtGui.QColor(255, 255, 255, 10))
            p.drawRoundedRect(R(20, 8, 400, 198), 36 * f, 36 * f)

            hl = self.tandai
            # triggers + bumpers
            kotak(50, 2, 120, 12, "LT (L2)", tandai=(hl == "triggers"))
            kotak(270, 2, 120, 12, "RT (R2)", tandai=(hl == "triggers"))
            kotak(50, 17, 120, 13, "LB (L1)", tandai=(hl == "bumpers"))
            kotak(270, 17, 120, 13, "RB (R1)", tandai=(hl == "bumpers"))

            # share / options / touchpad
            kotak(150, 62, 36, 16, "SHARE", tandai=(hl == "share"), px=6.5)
            kotak(254, 62, 36, 16, "OPTIONS", px=6.5)
            kotak(188, 44, 64, 38, "TOUCHPAD", px=6.5)

            # D-PAD cross
            p.setPen(QtGui.QPen(acc if hl == "dpad" else QtGui.QColor(255, 255, 255, 60),
                                1.6 if hl == "dpad" else 1.0))
            p.setBrush(_dgn_alpha(acc, 70) if hl == "dpad"
                       else QtGui.QColor(255, 255, 255, 16))
            p.drawRoundedRect(R(97, 70, 16, 62), 5 * f, 5 * f)
            p.drawRoundedRect(R(74, 93, 62, 16), 5 * f, 5 * f)
            teks_kecil(R(74, 50, 62, 12), "D-PAD")

            # sticks
            for cx, cy, target, label in ((118, 160, "stick_l", "LEFT STICK"),
                                          (322, 160, "stick_r", "RIGHT STICK")):
                on = hl == target
                p.setPen(QtGui.QPen(acc if on else QtGui.QColor(255, 255, 255, 60),
                                    1.6 if on else 1.0))
                p.setBrush(_dgn_alpha(acc, 70) if on
                           else QtGui.QColor(255, 255, 255, 16))
                p.drawEllipse(R(cx - 24, cy - 24, 48, 48))
                p.setBrush(QtGui.QColor(255, 255, 255, 40))
                p.drawEllipse(R(cx - 9, cy - 9, 18, 18))
                teks_kecil(R(cx - 46, cy + 28, 92, 12), label)

            # face buttons diamond
            face = hl == "face"

            def tombol_bulat(cx, cy, r, txt, tandai):
                p.setPen(QtGui.QPen(acc if tandai else QtGui.QColor(255, 255, 255, 72),
                                    1.6 if tandai else 1.0))
                p.setBrush(_dgn_alpha(acc, 70) if tandai
                           else QtGui.QColor(255, 255, 255, 16))
                p.drawEllipse(R(cx - r, cy - r, 2 * r, 2 * r))
                teks_kecil(R(cx - r, cy - r, 2 * r, 2 * r), txt,
                           (240, 240, 240, 230) if not tandai else acc.getRgb(), px=10.5)

            tombol_bulat(335, 76, 13, "△", face)     # Y top
            tombol_bulat(362, 103, 13, "○", face)    # B right
            tombol_bulat(335, 130, 13, "×", face)    # A bottom
            tombol_bulat(308, 103, 13, "□", face)    # X left
            teks_kecil(R(286, 50, 98, 12), "FACE BUTTONS")
        finally:
            p.end()


class PadMapDialog(QtWidgets.QDialog):
    """See + change + save the pad mapping (named presets)."""

    def __init__(self, gamepad, parent=None):
        super().__init__(parent)
        self.gamepad = gamepad
        self.setModal(False)
        self.resize(theme.px(880), theme.px(610))
        t = QtWidgets.QLabel("GAMEPAD MAPPING — see & change every control")
        t.setObjectName("PanelTitle")
        ket = QtWidgets.QLabel(
            "For beginners: click CHANGE on a row, then press the button / move the "
            "stick you want. The picture shows which physical control each mapping "
            "means. Save it as a named preset (e.g. \u201cCustom A\u201d).")
        ket.setWordWrap(True)
        theme.set_peran(ket, "dim")
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(t)
        v.addWidget(ket)

        tengah = QtWidgets.QHBoxLayout()
        tengah.setSpacing(theme.px(10))
        self.gambar = PadDiagram()
        tengah.addWidget(self.gambar, 0)

        self.grid = QtWidgets.QGridLayout()
        self.grid.setSpacing(4)
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
        tengah.addLayout(self.grid, 1)
        v.addLayout(tengah)

        legenda = QtWidgets.QLabel(
            "LEFT STICK ←/→ = LX [0] · LEFT STICK ↑/↓ = LY [1] · "
            "RIGHT STICK ←/→ = RX [3] · RIGHT STICK ↑/↓ = RY [4] · "
            "D-PAD ↑/↓ = camera tilt [7] · LB/RB (L1/R1) = deadman · "
            "LT (L2) = depth-hold. Numbers in [ ] are the raw pad index.")
        legenda.setWordWrap(True)
        theme.set_peran(legenda, "faint")
        v.addWidget(legenda)

        self.lbl_status = QtWidgets.QLabel("")
        self.lbl_status.setObjectName("MicroLabel")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
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
        b_close = QtWidgets.QPushButton("CLOSE")
        b_close.setObjectName("BtnTiny")
        b_close.clicked.connect(self.close)
        v.addWidget(b_close)

        self._tangkap = None
        self._prev_btn = set()
        self._prev_axes = None
        try:
            self.gamepad.sig_live.connect(self._live)
        except Exception:                              # noqa: BLE001
            pass
        self._muat_presets()
        self._segarkan()

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
    def _mulai_tangkap(self, key):
        self._tangkap = key
        self._prev_axes = None
        for _k, l in self.lbl.items():
            l.setStyleSheet("")
        self.lbl[key].setStyleSheet("color:%s; font-weight:800" % theme.warna("accent"))
        self.gambar.set_tandai(self.tandai.get(key))
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
