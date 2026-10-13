"""padmap.py — HALAMAN MAPPING GAMEPAD (permintaan pemilik 13 Okt).

Menjelaskan fungsi tiap tombol/sumbu pad dalam bahasa awam, bisa DIREMAP
(tekan tombol baru / gerakkan sumbu baru), dan disimpan sebagai PRESET BERNAMA
(mis. "Custom A" — nama bebas dari user). File: gamepad_presets.json di folder app.

Alur pakai untuk awam:
  1. Buka dialog ini (menu Vehicle ▸ Gamepad mapping, atau tombol PAD MAPPING…).
  2. Klik UBAH di baris yang mau diganti → tekan tombol / gerakkan stick yang baru.
  3. Kalau suka, SIMPAN SEBAGAI… beri nama (mis. "Custom A") → jadi preset aktif.
"""
import json
import os

from PySide6 import QtWidgets
import theme

FUNGSI = (
    ("axis_fwd",   "Maju / mundur",        "axis",
     "Stick yang mengatur gerak maju-mundur ROV"),
    ("axis_lat",   "Geser kiri / kanan",   "axis",
     "Stick yang mengatur gerak samping ROV"),
    ("axis_depth", "Naik / turun",         "axis",
     "Stick yang mengatur kedalaman (naik-turun)"),
    ("axis_yaw",   "Putar (yaw)",          "axis",
     "Stick yang mengatur putaran hadap ROV"),
    ("axis_tilt",  "Tilt kamera (dpad)",   "axis",
     "D-pad X untuk menggerakkan tilt kamera (kanan = turun)"),
    ("deadman_buttons", "DEADMAN (WAJIB ditahan)", "tombol",
     "Tombol keselamatan: input hanya terkirim selama tombol ini DITAHAN"),
    ("btn_arm_toggle",  "ARM / DISARM",    "tombol",
     "Tekan = minta arm/disarm (tetap ada konfirmasi checklist)"),
    ("btn_mode_cycle",  "Ganti mode",      "tombol",
     "Tekan = pindah mode (MANUAL → ALT_HOLD → …)"),
    ("btn_depth_hold",  "Depth-hold (tahan)","tombol",
     "TAHAN tombol = ALT_HOLD, lepas = kembali MANUAL"),
    ("btn_center_cam",  "Kamera ke tengah","tombol",
     "Tekan = tilt kamera kembali ke 50%"),
)

_TOMBOL_NAMA = ("X", "O", "△", "□", "L1", "R1", "L2", "R2", "Share", "Options",
                "PS", "L3", "R3")
_SUMBU_NAMA = ("LX", "LY", "L2axis", "RX", "RY", "R2axis", "dpadX", "dpadY")


def _nama_tombol(idx):
    try:
        idx = int(idx)
        return "%s [%d]" % (_TOMBOL_NAMA[idx], idx) if 0 <= idx < len(_TOMBOL_NAMA) \
            else "[%d]" % idx
    except (TypeError, ValueError):
        return str(idx)


def _nama_sumbu(idx):
    try:
        idx = int(idx)
        return "%s [%d]" % (_SUMBU_NAMA[idx], idx) if 0 <= idx < len(_SUMBU_NAMA) \
            else "[%d]" % idx
    except (TypeError, ValueError):
        return str(idx)


class PadMapDialog(QtWidgets.QDialog):
    """Lihat + ubah + simpan mapping pad (preset bernama)."""

    def __init__(self, gamepad, parent=None):
        super().__init__(parent)
        self.gamepad = gamepad
        self.setModal(False)
        self.resize(640, 560)
        t = QtWidgets.QLabel("GAMEPAD MAPPING — see & change every control")
        t.setObjectName("PanelTitle")
        ket = QtWidgets.QLabel(
            "For beginners: click CHANGE on a row, then press the button / move the "
            "stick you want there. Save it as a named preset (e.g. “Custom A”).")
        ket.setWordWrap(True)
        theme.set_peran(ket, "dim")
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(t)
        v.addWidget(ket)

        self.grid = QtWidgets.QGridLayout()
        self.grid.setSpacing(4)
        self.lbl = {}
        self.tipe = {}
        baris_no = 0
        for key, label, tipe, tip in FUNGSI:
            l1 = QtWidgets.QLabel(label)
            l1.setObjectName("RowLabel")
            l1.setToolTip(tip)
            l2 = QtWidgets.QLabel("—")
            l2.setObjectName("RowValue")
            b = QtWidgets.QPushButton("CHANGE")
            b.setObjectName("BtnTiny")
            b.setToolTip("klik, lalu tekan tombol / gerakkan sumbu yang baru")
            b.clicked.connect(lambda _=False, k=key: self._mulai_tangkap(k))
            self.grid.addWidget(l1, baris_no, 0)
            self.grid.addWidget(l2, baris_no, 1)
            self.grid.addWidget(b, baris_no, 2)
            self.lbl[key] = l2
            self.tipe[key] = tipe
            baris_no += 1
        v.addLayout(self.grid)

        self.lbl_status = QtWidgets.QLabel("")
        self.lbl_status.setObjectName("MicroLabel")
        self.lbl_status.setWordWrap(True)
        v.addWidget(self.lbl_status)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(4)
        lab_p = QtWidgets.QLabel("PRESET")
        lab_p.setObjectName("MicroLabel")
        self.cb_preset = QtWidgets.QComboBox()
        self.cb_preset.setToolTip("preset pad tersimpan (termasuk buatan Anda)")
        b_apply = QtWidgets.QPushButton("APPLY")
        b_apply.setObjectName("BtnTiny")
        b_apply.clicked.connect(self._terapkan)
        b_save = QtWidgets.QPushButton("SAVE AS…")
        b_save.setObjectName("BtnTiny")
        b_save.setToolTip("simpan mapping sekarang sebagai preset dengan nama bebas")
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
        except Exception:                                  # noqa: BLE001
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
        except Exception:                                  # noqa: BLE001
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
        except Exception:                                  # noqa: BLE001
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
        for k, l in self.lbl.items():
            l.setStyleSheet("")
        self.lbl[key].setStyleSheet("color:%s; font-weight:800" % theme.warna("accent"))
        self.lbl_status.setText("WAITING FOR INPUT — press a button / move an axis "
                                "for “%s”…" % key)
        try:
            self.gamepad.sig_live.connect(self._live)
        except Exception:                                  # noqa: BLE001
            pass

    def _selesai_tangkap(self, teks):
        self._tangkap = None
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
        except Exception:                                  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ tampil
    def _segarkan(self):
        g = self.gamepad.cfg
        for key, _label, tipe, _tip in FUNGSI:
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
            except Exception:                              # noqa: BLE001
                teks = "—"
            self.lbl[key].setText(teks)
