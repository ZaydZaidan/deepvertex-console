"""Widget custom untuk ROV Mission Console (PyQt5, digambar manual dengan QPainter).

Tidak memakai QChart/pyqtgraph supaya tampilan bisa dikendalikan penuh (dan konsisten
dengan bahasa desain: garis 1 px, tanpa gradien, angka monospace).
"""
import math
import time
from collections import deque

import numpy as np

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt, QPointF, QRectF

import theme


# --------------------------------------------------------------------------- #
# Panel: wadah dengan header mikro
# --------------------------------------------------------------------------- #
class DomainMark(QtWidgets.QWidget):
    """Kotak 6 px penanda domain panel — warna diambil dari palet AKTIF saat menggambar.

    Warna tidak pernah menjadi satu-satunya pembawa makna: judul panel tetap ada di
    sebelahnya, dan tooltip menyebut domainnya dengan kata (rancangan §6.3).
    """

    def __init__(self, domain="neutral", parent=None):
        super().__init__(parent)
        self.domain = domain
        self.setFixedSize(6, 6)
        self.setToolTip(theme.DOMAIN_LABEL.get(domain, domain))

    def set_domain(self, domain):
        self.domain = domain
        self.setToolTip(theme.DOMAIN_LABEL.get(domain, domain))
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(theme.domain(self.domain)))
        p.end()


class Panel(QtWidgets.QFrame):
    def __init__(self, title, hint="", domain="neutral", parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QtWidgets.QWidget()
        head.setObjectName("PanelHead")
        # TINGGI ALAMI (bukan dipatok): kalau judul/hint tidak muat, kepala panel
        # membungkus ke baris berikutnya — bukan memotong teks (aturan keras pemilik).
        head.setMinimumHeight(24)     # mockup: .phead min-height 25px
        h = QtWidgets.QHBoxLayout(head)
        h.setContentsMargins(theme.px(12), theme.px(6), theme.px(12), theme.px(6))
        h.setSpacing(6)
        self.domain = domain
        self.mark = DomainMark(domain)
        self.title = QtWidgets.QLabel(title.upper())
        self.title.setObjectName("PanelTitle")
        self.hint = QtWidgets.QLabel(hint)
        self.hint.setObjectName("PanelHint")
        self.hint.setWordWrap(True)
        self.hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # JANGAN biarkan hint panjang melar/terpotong: label tidak boleh memaksa lebar
        # panel (pelajaran 23 Sep: hint "TAHAN L1/LB ..." membuat kotak kanan berantakan).
        self.hint.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Preferred)
        self.hint.setToolTip(hint)
        h.addWidget(self.mark)
        h.addWidget(self.title)
        # TANPA spacer di tengah: hint yang menerima sisa ruang (stretch 1). Dengan spacer
        # + policy Ignored, Qt menyusutkan hint ke lebar minimum sehingga teksnya membungkus
        # ke baris kedua walaupun panelnya lebar — kepala panel jadi 39 px padahal mockup 24 px.
        h.addWidget(self.hint, 1)

        self.body = QtWidgets.QWidget()
        self.body.setObjectName("PanelBody")
        self.body.setAttribute(Qt.WA_StyledBackground, True)   # lihat komentar di StatusBar
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(theme.px(12), theme.px(10),
                                            theme.px(12), theme.px(12))
        self.body_layout.setSpacing(6)

        outer.addWidget(head)
        outer.addWidget(self.body, 1)

    def sisip_kontrol(self, w):
        """Tempatkan kontrol kecil di kepala panel (mis. pilihan MAP/NED).

        Rancangan menaruh pemilih tampilan di kepala panel, bukan di badan; widget
        ditempatkan SEBELUM hint supaya susunannya tetap [mark][judul][kontrol][hint].
        """
        h = self.hint.parentWidget().layout()
        h.insertWidget(max(0, h.count() - 1), w)
        return w

    def set_hint(self, text):
        self.hint.setText(text)
        self.hint.setToolTip(text)

    def set_domain(self, domain):
        """Pindahkan penanda domain panel (mis. saat panel berganti peran)."""
        self.domain = domain
        self.mark.set_domain(domain)


# --------------------------------------------------------------------------- #
# Readout: label mikro + nilai monospace + satuan
# --------------------------------------------------------------------------- #
class Readout(QtWidgets.QWidget):
    """Label mikro + nilai + satuan.

    Warna nilai diatur lewat PROPERI `peran` (QSS), bukan warna keras di stylesheet:
    dengan begitu mengganti tema tidak meninggalkan widget bergaya lama. Gaya inline
    hanya memuat ukuran huruf (tidak mengandung warna).
    """

    def __init__(self, label, unit="", size=14, color=None, fmt="{:s}", parent=None,
                 peran=None):
        super().__init__(parent)
        self.fmt = fmt
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(1)
        self.lab = QtWidgets.QLabel(label.upper())
        self.lab.setObjectName("MicroLabel")
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        # `color` (cara lama) tetap diterima: bila hex-nya warna aksen palet aktif,
        # nilainya tampil sebagai aksen; selain itu nilai biasa.
        self.peran = peran or ("accent" if theme.token_dari(color) == "accent" else "ink")
        self._size = size
        self.val = QtWidgets.QLabel("—")
        self.val.setStyleSheet(theme.mono(size, 500))
        theme.set_peran(self.val, "faint")
        self.unit = QtWidgets.QLabel(unit)
        self.unit.setObjectName("Unit")
        row.addWidget(self.val)
        row.addWidget(self.unit, 0, Qt.AlignBottom)
        row.addStretch(1)
        v.addWidget(self.lab)
        v.addLayout(row)

    def set_value(self, value, fmt=None):
        if value is None:
            self.val.setText("—")
            theme.set_peran(self.val, "faint")
            return
        theme.set_peran(self.val, self.peran)
        self.val.setText((fmt or self.fmt).format(value))

    def set_peran(self, peran):
        """Ubah peran warna nilai (ink/accent/ok/warn/err/dim)."""
        self.peran = peran
        if self.val.text() not in ("", "—"):
            theme.set_peran(self.val, peran)


# --------------------------------------------------------------------------- #
# Meter: bar bertitik-nol (untuk roll/pitch/gyro)
# --------------------------------------------------------------------------- #
class Meter(QtWidgets.QWidget):
    def __init__(self, label, lo, hi, unit="", color=theme.CYAN, decimals=1,
                 signed=True, label_w=30, parent=None):
        super().__init__(parent)
        self.label, self.lo, self.hi, self.unit = label, lo, hi, unit
        # Disimpan sebagai TOKEN (mis. 'accent'/'cyan'), bukan hex: hex dari pemanggil
        # bisa basi setelah tema diganti — token selalu ikut palet aktif saat menggambar.
        self.token, self.decimals, self.signed = theme.token_dari(color), decimals, signed
        self.color = theme.hexa(self.token)
        self.label_w = label_w
        self._v = None
        self.setFixedHeight(16)          # dipadatkan (dulu 18) supaya rel kanan muat utuh
        self.setMinimumWidth(110)        # 2 meter sebaris wajib muat di rel tersempit (260 px)

    def set_value(self, v):
        self._v = v
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        bar_x = self.label_w
        num_w = 46
        bar_w = max(10, w - bar_x - num_w - 6)
        cy = h / 2

        p.setPen(QtGui.QColor(theme.TEXT_FAINT))
        f = p.font()
        f.setFamily(theme.FONT_UI)
        f.setPixelSize(theme.px(10))
        p.setFont(f)
        p.drawText(QRectF(0, 0, self.label_w - 4, h), Qt.AlignLeft | Qt.AlignVCenter, self.label.upper())

        # track 4 px seperti rancangan (.meter .track height:4px) — bukan balok 8 px
        p.setPen(QtGui.QColor(theme.HAIRLINE))
        p.setBrush(QtGui.QColor(theme.HAIRLINE))     # track 4 px (rancangan: warna --line)
        p.drawRect(QRectF(bar_x, cy - 2, bar_w, 4))
        zero_x = bar_x + bar_w / 2

        if self._v is not None:
            v = max(self.lo, min(self.hi, self._v))
            frac = (v - self.lo) / (self.hi - self.lo)
            x = bar_x + frac * bar_w
            x0, x1 = (zero_x, x) if x >= zero_x else (x, zero_x)
            p.setPen(Qt.NoPen)
            p.setBrush(QtGui.QColor(theme.hexa(self.token)))
            p.drawRect(QRectF(x0, cy - 2, max(1.0, x1 - x0), 4))

        p.setPen(QtGui.QColor(theme.HAIRLINE))
        for frac in (0.25, 0.75):
            x = bar_x + frac * bar_w
            p.drawLine(QPointF(x, cy - 2), QPointF(x, cy + 2))
        p.setPen(QtGui.QColor(theme.HAIRLINE_HI))
        p.drawLine(QPointF(zero_x, cy - 4), QPointF(zero_x, cy + 4))

        p.setPen(QtGui.QColor(theme.TEXT))
        f.setFamily(theme.FONT_MONO)
        f.setPixelSize(theme.px(10))
        p.setFont(f)
        txt = "--" if self._v is None else (
            f"{self._v:+.{self.decimals}f}" if self.signed else f"{self._v:.{self.decimals}f}")
        p.drawText(QRectF(w - num_w, 0, num_w, h), Qt.AlignRight | Qt.AlignVCenter, txt)
        p.end()


# --------------------------------------------------------------------------- #
# Chip status
# --------------------------------------------------------------------------- #
class Chip(QtWidgets.QWidget):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._text = text
        # TOKEN, bukan hex: chip selalu diwarnai ulang dari palet aktif saat menggambar.
        self._token = "ink_faint"
        self._detail = ""
        self.setMinimumHeight(theme.px(20))
        self.setMinimumWidth(70)

    def lebar_teks(self):
        """Lebar yang dibutuhkan titik + kunci + nilai (agar nilai tidak terpotong)."""
        f = QtGui.QFont(theme.KELUARGA if hasattr(theme, "KELUARGA") else theme.FONT_UI)
        f.setPixelSize(theme.px(9))
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 0.9)
        lebar = theme.px(16) + QtGui.QFontMetricsF(f).horizontalAdvance(self._text.upper())
        if self._detail:
            fv = QtGui.QFont(f)
            fv.setPixelSize(theme.px(10))
            fv.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 0)
            lebar += 5 + QtGui.QFontMetricsF(fv).horizontalAdvance(str(self._detail))
        return int(lebar) + 8

    def sizeHint(self):
        return QtCore.QSize(max(70, self.lebar_teks()), theme.px(20))

    def minimumSizeHint(self):
        return QtCore.QSize(max(70, self.lebar_teks()), theme.px(20))

    def set_state(self, color, detail=""):
        self._token, self._detail = theme.token_dari(color), detail
        self.setMinimumWidth(self.lebar_teks())
        self.updateGeometry()
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.setPen(QtGui.QColor(theme.HAIRLINE_HI))
        p.setBrush(Qt.NoBrush)
        r = theme.px(6)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), r, r)
        p.setPen(Qt.NoPen)
        p.setBrush(QtGui.QColor(theme.hexa(self._token)))
        p.drawEllipse(QPointF(theme.px(8), h / 2), 3, 3)
        f = p.font()
        f.setFamily(theme.FONT_UI)
        f.setPixelSize(theme.px(9))
        p.setFont(f)
        # Dua gaya teks seperti rancangan: KUNCI kecil redup (uppercase, tracking) dan
        # NILAI lebih terang (angka tabular) — bukan satu deret teks berwarna sama.
        fk = p.font()
        fk.setFamily(theme.FONT_UI)
        fk.setPixelSize(theme.px(9))
        fk.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 0.9)
        kunci = self._text.upper()
        p.setFont(fk)
        p.setPen(QtGui.QColor(theme.TEXT_FAINT))
        p.drawText(QRectF(theme.px(16), 0, w - theme.px(20), h),
                   Qt.AlignLeft | Qt.AlignVCenter, kunci)
        if self._detail:
            lebar_kunci = QtGui.QFontMetricsF(fk).horizontalAdvance(kunci) + theme.px(5)
            fv = p.font()
            fv.setPixelSize(theme.px(10))
            fv.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 0)
            p.setFont(fv)
            p.setPen(QtGui.QColor(theme.TEXT))
            p.drawText(QRectF(theme.px(16) + lebar_kunci, 0,
                              max(4.0, w - theme.px(20) - lebar_kunci), h),
                       Qt.AlignLeft | Qt.AlignVCenter, str(self._detail))
        p.end()


# --------------------------------------------------------------------------- #
# Tile kamera
# --------------------------------------------------------------------------- #
class CameraTile(QtWidgets.QWidget):
    """Satu stream gambar (mono8/rgb8): letterbox, label, rate, dan status OFF.

    Pelajaran 23 Sep 2026: (1) panel kamera harus MENGIKUTI RASIO gambar -- gambar lebar
    di panel vertikal memakan ruang; (2) kalau kamera dimatikan JANGAN bekukan frame
    terakhir: tampilkan "CAMERA OFF" + alasannya; (3) kalau aliran frame berhenti, tandai
    juga supaya pilot tidak melihat gambar basi yang dikira hidup.
    """

    # Ambang "aliran berhenti": 4 s cukup longgar untuk render GPU maupun software
    # (llvmpipe bisa ~0,3 Hz) sehingga kamera hidup-lambat tidak salah ditandai mati.
    STALE_S = 4.0

    def __init__(self, title, parent=None, judul=True):
        super().__init__(parent)
        self.title = title
        self.gambar_judul = bool(judul)
        self._img = None
        self._buf = None
        self._rate = None
        self._stamp = None
        self._aspect = 16.0 / 9.0     # rasio frame terakhir (default 16:9 ala stream)
        self.mode_gambar = "cover"    # 'cover' = isi kotak lalu POTONG (sim);
                                      # 'contain' = tampilkan UTUH (konsol nyata)
        self._off = None              # alasan kamera mati (None = hidup)
        self._off_title = "CAMERA OFF"  # judul besar saat mati ("CAMERA OFF" / "NO FRAMES")
        # preview dimatikan (kamera TETAP mengalir untuk VINS): frame jangan digambar.
        # WAJIB sticky: kalau tidak, frame berikutnya langsung menghapus status ini.
        self._muted = None
        self.tinggi_maks = None       # batas tinggi opsional (mockup: kotak kamera 124-150 px)
        self._h_natural = None        # tinggi ideal terkini (fixed-height: layout stabil)
        self.status = None            # teks pengganti "NO SIGNAL"
        self.setMinimumSize(240, 135)
        pol = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                    QtWidgets.QSizePolicy.MinimumExpanding)
        pol.setHeightForWidth(True)   # tanpa flag ini layout MENGABAIKAN heightForWidth
        self.setSizePolicy(pol)
        t = QtCore.QTimer(self)
        t.timeout.connect(self._cek_basi)
        t.start(500)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._sync_tinggi()

    def _sync_tinggi(self):
        """Kunci tinggi panel = rasio frame (FIXED, bukan max) + minta layout ulang.

        FIXED-height (13 Okt): maximumHeight TIDAK cukup — susunan kolom memberi tinggi
        ekstra ke panel kamera (terukur rasio 1.55, bukan 1.78). Dengan setFixedHeight,
        panel kamera = tile + chrome PERSIS; sisa kolom jatuh ke peta (Expanding).
        """
        try:
            if self.width() <= 1:
                return
            h = self.heightForWidth(self.width())
            if self.tinggi_maks:
                h = min(h, int(self.tinggi_maks))
            if h != self._h_natural:
                self._h_natural = h
                self.setFixedHeight(h)
                self.updateGeometry()
        except Exception:                  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- status
    def set_off(self, reason, title="CAMERA OFF"):
        """Kamera dimatikan (kebijakan mode) atau aliran berhenti: jangan freeze."""
        self._off = reason or "camera off"
        self._off_title = title
        self._img = None
        self._buf = None
        self._rate = None
        self.update()

    def set_on(self):
        self._off = None
        self._off_title = "CAMERA OFF"
        self.update()

    # ------------------------------------------------- preview dimatikan (sticky)
    def set_muted(self, reason):
        """Preview dimatikan: kamera tetap mengalir (VINS butuhnya), GUI jangan gambar.

        Berbeda dari set_off: status ini TIDAK dihapus oleh frame yang datang.
        """
        self._muted = reason or "preview off"
        self._img = None
        self._buf = None
        self._rate = None
        self.update()

    def set_unmuted(self):
        self._muted = None
        self.update()

    def is_muted(self):
        return self._muted is not None

    def is_off(self):
        return self._off is not None

    def _cek_basi(self):
        if self._stamp is None or self._off is not None or self._muted is not None:
            return
        if time.monotonic() - self._stamp > self.STALE_S:
            self.set_off("stream stalled — no frames for %.0f s" % self.STALE_S,
                         title="NO FRAMES")

    # ---------------------------------------------------------------- ukuran
    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        """Tinggi ideal = lebar / rasio gambar (+ header). Panel jadi pas, tidak vertikal."""
        return int(w / max(0.2, self._aspect)) + 18

    def sizeHint(self):
        """LEBAR TETAP 320 (pelajaran 28 Sep: hint ber-lebar-dinamis -> panel berayun).

        Tinggi = `_h_natural` (hasil _sync_tinggi terakhir) supaya panel selalu pas rasio.
        """
        return QtCore.QSize(320, self._h_natural or self.heightForWidth(320))

    def minimumSizeHint(self):
        """Minimum kecil: tile TIDAK boleh memaksa panel/rel menjadi lebih lebar."""
        return QtCore.QSize(120, self.heightForWidth(120))

    # ---------------------------------------------------------------- gambar
    def set_qimage(self, qimg, rate=None):
        if self._muted is not None:
            return
        self._img = qimg
        self._rate = rate
        self._off = None
        if qimg is not None and qimg.height() > 0:
            self._set_aspect(qimg.width() / float(qimg.height()))
        self._stamp = time.monotonic()
        self.update()

    def set_static_image(self, qimg, note=None):
        """Gambar STATIS (mis. orthomosaic SfM) — bukan stream.

        Tahap H-map (25 Sep): mozaik hasil sfm_colmap.py tidak "mengalir", jadi
        pemeriksaan 'stream stalled -> NO FRAMES' harus dimatikan (_stamp=None);
        tanpa ini panel berubah jadi "NO FRAMES" 4 s setelah gambar dimuat.
        """
        self.set_qimage(qimg, rate=None)
        self._stamp = None
        if note:
            self.status = note
        self.update()

    def set_status(self, text):
        """Teks yang ditampilkan saat belum ada gambar (mis. 'standby (mode MANUAL)')."""
        self.status = text
        self.update()

    def set_frame(self, array, rate=None):
        """array: numpy uint8 HxW (mono8) atau HxWx3 (rgb8)."""
        if self._muted is not None:
            return                    # preview dimatikan: buang frame (jangan hapus status)
        if array.ndim == 3:
            if array.shape[2] == 4:
                array = array[:, :, :3]
            c = array.shape[2]
            h, w = array.shape[:2]
            arr = np.ascontiguousarray(array)
            self._buf = arr
            self._img = QtGui.QImage(arr.data, w, h, w * c,
                                     QtGui.QImage.Format_RGB888).copy()
        else:
            h, w = array.shape[:2]
            self._buf = array
            self._img = QtGui.QImage(array.data, w, h, w,
                                     QtGui.QImage.Format_Grayscale8).copy()
        self._rate = rate
        self._off = None
        if h:
            self._set_aspect(w / float(h))
        self._stamp = time.monotonic()
        self.update()

    def _set_aspect(self, a):
        """Ubah rasio frame + minta layout ulang.

        WAJIB updateGeometry(): tanpa ini panel induk (yang tingginya mengikuti
        rasio gambar) tidak tahu sizeHint berubah -- frame pertama masuk (4:3 -> 16:9)
        tidak pernah memicu reflow dan panel tetap di tinggi lama.
        """
        if abs(a - self._aspect) > 1e-3:
            self._aspect = a
            self.updateGeometry()

    def clear(self):
        self._img = None
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(theme.warna("vid")))
        p.setPen(QtGui.QColor(theme.warna("line_hi")))     # tepi kotak citra (tegas di 2 mode)
        p.drawRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1))

        # strip atas hanya dipakai kalau ada judul atau laju (mockup: kotak kamera depan
        # tidak punya judul di dalam kotak — judul panel sudah menyebutnya)
        top = 16 if (getattr(self, "gambar_judul", True) or self._rate is not None) else 1
        area = QRectF(1, top, self.width() - 2, self.height() - top - 1)
        f = p.font()
        if self._img is None and (self._off is not None or self._muted is not None):
            # kamera dimatikan / preview dimatikan / aliran berhenti -> tegas, bukan frame beku
            judul = self._off_title if self._off is not None else "PREVIEW OFF"
            alasan = self._off if self._off is not None else self._muted
            f.setFamily(theme.FONT_UI)
            f.setPixelSize(theme.px(11))
            f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.6)
            p.setFont(f)
            p.setPen(QtGui.QColor(theme.WARN if self._off is not None else theme.TEXT_DIM))
            p.drawText(QRectF(area.x(), area.y(), area.width(), area.height() / 2),
                       Qt.AlignBottom | Qt.AlignHCenter, judul)
            f.setPixelSize(theme.px(9))
            p.setFont(f)
            p.setPen(QtGui.QColor(theme.TEXT_FAINT))
            p.drawText(QRectF(area.x() + 6, area.y() + area.height() / 2 + 2,
                              area.width() - 12, area.height() / 2),
                       Qt.AlignTop | Qt.AlignHCenter | Qt.TextWordWrap, alasan)
        elif self._img is not None:
            # GAMBAR MENGISI KOTAK (padanan `object-fit: cover` di mockup): pakai skala
            # TERBESAR lalu potong kelebihannya. Sebelumnya letterbox (skala terkecil)
            # sehingga muncul kolom/bar hitam di sisi gambar (temuan pemilik 28 Sep).
            iw, ih = self._img.width(), self._img.height()
            if self.mode_gambar == "contain":
                # UTUH: skala terkecil -> seluruh frame terlihat (bar di sisi bila perlu)
                s = min(area.width() / max(1, iw), area.height() / max(1, ih))
            else:
                s = max(area.width() / max(1, iw), area.height() / max(1, ih))
            w, h = iw * s, ih * s
            x = area.x() + (area.width() - w) / 2
            y = area.y() + (area.height() - h) / 2
            p.save()
            p.setClipRect(QtCore.QRectF(area.x(), area.y(),
                                        max(0.0, area.width()), max(0.0, area.height())))
            p.drawImage(QRectF(x, y, w, h), self._img)
            p.restore()
        else:
            p.setPen(QtGui.QColor(theme.TEXT_FAINT))
            f.setFamily(theme.FONT_UI)
            f.setPixelSize(theme.px(10))
            f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.6)
            p.setFont(f)
            p.drawText(area, Qt.AlignCenter, self.status or "NO SIGNAL")

        f = p.font()
        f.setFamily(theme.FONT_UI)
        f.setPixelSize(theme.px(9))
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.4)
        p.setFont(f)
        p.setPen(QtGui.QColor(theme.TEXT_DIM))
        if getattr(self, "gambar_judul", True):
            p.drawText(QRectF(6, 0, self.width() - 12, top), Qt.AlignLeft | Qt.AlignVCenter,
                       self.title.upper())
        if self._rate is not None:
            p.setPen(QtGui.QColor(theme.OK if self._rate > 15 else theme.WARN))
            f.setFamily(theme.FONT_MONO)
            f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 0)
            f.setPixelSize(theme.px(9))
            p.setFont(f)
            p.drawText(QRectF(6, 0, self.width() - 12, top), Qt.AlignRight | Qt.AlignVCenter,
                       f"{self._rate:5.1f} Hz")
        p.end()


# --------------------------------------------------------------------------- #
# Placeholder: jendela kosong berisi teks samar (jujur: belum ada isi)
# --------------------------------------------------------------------------- #
class Placeholder(QtWidgets.QWidget):
    """Area kosong penuh (mengisi sisa ruang) dengan pesan status samar.

    Dipakai untuk jendela hasil SfM (24 Sep 2026): ruang yang dibebaskan dari
    panel kamera VIO yang dipas-in disediakan untuk output orthomosaic, tapi
    SfM-nya belum dikerjakan -> tampilkan "belum ada isi", JANGAN angka karangan.
    Kalau nanti sudah ada gambar, lewati widget ini (set_qimage pada pengganti).
    """

    def __init__(self, title, note, parent=None):
        super().__init__(parent)
        self.title = title
        self.note = note
        self.setMinimumSize(240, 120)
        # Mengisi sisa ruang (tanpa HFW): inilah jendelanya sendiri.
        pol = QtWidgets.QSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                    QtWidgets.QSizePolicy.Expanding)
        self.setSizePolicy(pol)

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, False)
        p.fillRect(self.rect(), QtGui.QColor(theme.warna("vid")))
        p.setPen(QtGui.QColor(theme.warna("line_hi")))
        p.drawRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1))
        f = p.font()
        f.setFamily(theme.FONT_UI)
        f.setPixelSize(theme.px(11))
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.6)
        p.setFont(f)
        p.setPen(QtGui.QColor(theme.TEXT_DIM))
        p.drawText(QRectF(0, self.height() / 2 - 22, self.width(), 22),
                   Qt.AlignCenter, self.title.upper())
        f.setPixelSize(theme.px(9))
        f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 0.4)
        p.setFont(f)
        p.setPen(QtGui.QColor(theme.TEXT_FAINT))
        p.drawText(QRectF(12, self.height() / 2 + 2, self.width() - 24, 60),
                   Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, self.note)
        p.end()


# --------------------------------------------------------------------------- #
# Horizon buatan (attitude)
# --------------------------------------------------------------------------- #
class Horizon(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.roll = 0.0
        self.pitch = 0.0
        # Anchor horizontal lingkaran: pusat konten (nilai+satuan) dua readout di
        # bawahnya (Depth & Altitude AGL) -> lingkaran selalu sejajar vertikal
        # dengan titik tengah antara keduanya, berapa pun lebar angkanya.
        # Tanpa anchor (None) -> jatuh ke tengah widget (perilaku lama).
        self.anchor_a = None
        self.anchor_b = None
        self.setMinimumSize(120, 120)

    def set_attitude(self, roll_deg, pitch_deg):
        self.roll, self.pitch = roll_deg, pitch_deg
        self.update()

    @staticmethod
    def _content_cx(anchor, into):
        """Pusat horizontal konten terlihat sebuah Readout (nilai + satuan).

        Pakai mapToGlobal/mapFromGlobal: QWidget::mapTo() ke non-ancestor itu UB
        (PyQt5 -> Segmentation fault), sementara horizon & readout hanya sibling.
        """
        ro = anchor
        left = into.mapFromGlobal(ro.val.mapToGlobal(QtCore.QPoint(0, 0))).x()
        right = into.mapFromGlobal(
            ro.unit.mapToGlobal(QtCore.QPoint(ro.unit.width(), 0))).x()
        return (left + right) / 2.0

    def _anchor_cx(self, fallback, margin):
        """cx lingkaran = midpoint dua konten readout; fallback bila tak valid."""
        if self.anchor_a is None or self.anchor_b is None:
            return fallback
        try:
            a = self._content_cx(self.anchor_a, self)
            b = self._content_cx(self.anchor_b, self)
        except RuntimeError:                 # widget sudah hancur
            return fallback
        cx = (a + b) / 2.0
        # guard: lingkaran wajib tetap muat di dalam widget
        if not (margin <= cx <= self.width() - margin):
            return fallback
        return cx

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        r = min(w, h) / 2 - 2
        cx = self._anchor_cx(w / 2, r)
        cy = h / 2

        clip = QtGui.QPainterPath()
        clip.addEllipse(QPointF(cx, cy), r, r)
        p.save()
        p.setClipPath(clip)

        px_per_deg = r / 32.0
        p.translate(cx, cy)
        p.rotate(-self.roll)
        p.translate(0, self.pitch * px_per_deg)

        p.fillRect(QRectF(-2 * r, -2 * r, 4 * r, 2 * r), QtGui.QColor(theme.SKY))
        p.fillRect(QRectF(-2 * r, 0, 4 * r, 2 * r), QtGui.QColor(theme.warna("ground")))
        p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT_DIM), 1))
        p.drawLine(QPointF(-2 * r, 0), QPointF(2 * r, 0))

        f = p.font()
        f.setFamily(theme.FONT_MONO)
        f.setPixelSize(theme.px(8))
        p.setFont(f)
        for d in (-20, -10, 10, 20):
            y = d * px_per_deg
            half = 16 if d % 20 else 26
            p.setPen(QtGui.QPen(QtGui.QColor(theme.HAIRLINE_HI), 1))
            p.drawLine(QPointF(-half, y), QPointF(half, y))
            p.setPen(QtGui.QColor(theme.TEXT_FAINT))
            p.drawText(QRectF(half + 3, y - 6, 30, 12), Qt.AlignLeft | Qt.AlignVCenter, f"{d:+d}")
        p.restore()

        p.setPen(QtGui.QPen(QtGui.QColor(theme.HAIRLINE_HI), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)

        p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT_DIM), 1))
        p.drawLine(QPointF(cx - 26, cy), QPointF(cx - 8, cy))
        p.drawLine(QPointF(cx + 8, cy), QPointF(cx + 26, cy))
        p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 2))
        p.drawLine(QPointF(cx - 8, cy), QPointF(cx, cy))
        p.drawLine(QPointF(cx, cy), QPointF(cx + 8, cy))

        p.setPen(QtGui.QPen(QtGui.QColor(theme.HAIRLINE_HI), 1))
        for a in range(-60, 61, 15):
            rad = math.radians(a - 90)
            x0, y0 = cx + math.cos(rad) * (r - 5), cy + math.sin(rad) * (r - 5)
            x1, y1 = cx + math.cos(rad) * r, cy + math.sin(rad) * r
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        p.end()


class Kompas(QtWidgets.QWidget):
    """Kompas bulat ala QGC: kartu berputar (heading di atas) + lubber line + angka.

    Gaya visual mengikuti Horizon: latar transparan, garis tipis, aksen tema.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.heading = 0.0
        self.setMinimumSize(120, 120)

    def set_heading(self, deg):
        try:
            self.heading = float(deg) % 360.0
        except (TypeError, ValueError):
            return
        self.update()

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        r = min(w, h) / 2 - 2
        cx, cy = w / 2, h / 2

        # kartu berputar: utara menghadap sesuai heading (heading di atas = lubber)
        p.save()
        p.translate(cx, cy)
        p.rotate(-self.heading)
        for a in range(0, 360, 15):
            rad = math.radians(a - 90)
            if a % 90 == 0:
                r0, wpen = r - 11, 2
            elif a % 45 == 0:
                r0, wpen = r - 8, 1
            else:
                r0, wpen = r - 5, 1
            col = theme.TEXT_DIM if a % 90 == 0 else theme.HAIRLINE_HI
            p.setPen(QtGui.QPen(QtGui.QColor(col), wpen))
            p.drawLine(QPointF(math.cos(rad) * r0, math.sin(rad) * r0),
                       QPointF(math.cos(rad) * r, math.sin(rad) * r))
        f = p.font()
        f.setFamily(theme.FONT_UI)
        f.setPixelSize(theme.px(11))
        f.setBold(True)
        p.setFont(f)
        fm = QtGui.QFontMetricsF(f)
        for lab, a in (("N", 0), ("E", 90), ("S", 180), ("W", 270)):
            rad = math.radians(a - 90)
            p.setPen(QtGui.QColor(theme.ACCENT if lab == "N" else theme.TEXT))
            p.drawText(QPointF(math.cos(rad) * (r - 23) - fm.horizontalAdvance(lab) / 2,
                               math.sin(rad) * (r - 23) + 4), lab)
        # panah utara (merah/aksen) di dalam huruf N
        rad = math.radians(-90)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 2))
        p.drawLine(QPointF(math.cos(rad) * (r - 33), math.sin(rad) * (r - 33)),
                   QPointF(math.cos(rad) * (r - 27), math.sin(rad) * (r - 27)))
        p.restore()

        # cincin luar + lubber line (tetap di atas)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.HAIRLINE_HI), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 2))
        p.drawLine(QPointF(cx, cy - r + 1), QPointF(cx - 5, cy - r + 11))
        p.drawLine(QPointF(cx, cy - r + 1), QPointF(cx + 5, cy - r + 11))

        # angka heading di tengah (tetap)
        f2 = p.font()
        f2.setFamily(theme.FONT_MONO)
        f2.setPixelSize(theme.px(15))
        f2.setBold(True)
        p.setFont(f2)
        fm2 = QtGui.QFontMetricsF(f2)
        teks = "%03d" % (int(round(self.heading)) % 360)
        p.setPen(QtGui.QColor(theme.TEXT))
        p.drawText(QPointF(cx - fm2.horizontalAdvance(teks) / 2, cy + 6), teks)
        f3 = p.font()
        f3.setFamily(theme.FONT_UI)
        f3.setPixelSize(theme.px(8))
        p.setFont(f3)
        fm3 = QtGui.QFontMetricsF(f3)
        p.setPen(QtGui.QColor(theme.TEXT_FAINT))
        p.drawText(QPointF(cx - fm3.horizontalAdvance("HDG") / 2, cy + 20), "HDG")
        p.end()


# --------------------------------------------------------------------------- #
# Plot lintasan (tampak atas NED): VIO vs ground truth
# --------------------------------------------------------------------------- #
class TrajectoryPlot(QtWidgets.QWidget):
    """Peta 2D realtime: north ke atas, east ke kanan, skala 1:1 (meter)."""

    def __init__(self, max_points=6000, parent=None):
        super().__init__(parent)
        self.gt = deque(maxlen=max_points)     # (north, east)
        self.vio = deque(maxlen=max_points)    # (north, east) setelah diselaraskan
        self.pose = None                       # (north, east, yaw_rad) ground truth
        self.stats = {}
        self.setMinimumSize(280, 220)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

    def add_gt(self, n, e):
        self.gt.append((n, e))

    def add_vio(self, n, e):
        self.vio.append((n, e))

    def set_pose(self, n, e, yaw):
        self.pose = (n, e, yaw)

    def set_stats(self, **kw):
        self.stats.update(kw)

    def clear_tracks(self):
        self.gt.clear()
        self.vio.clear()

    # ---------- geometri ----------
    def _bounds(self):
        pts = list(self.gt) + list(self.vio)
        if self.pose:
            pts.append((self.pose[0], self.pose[1]))
        if not pts:
            return None
        ns = [p[0] for p in pts]
        es = [p[1] for p in pts]
        return min(ns), max(ns), min(es), max(es)

    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QtGui.QColor(theme.warna("vid")))

        pad_l, pad_r, pad_t, pad_b = 34, 12, 12, 30
        area = QRectF(pad_l, pad_t, max(10, w - pad_l - pad_r), max(10, h - pad_t - pad_b))

        b = self._bounds()
        if b is None:
            p.setPen(QtGui.QColor(theme.TEXT_FAINT))
            f = p.font(); f.setFamily(theme.FONT_UI); f.setPixelSize(theme.px(10))
            f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, 1.4)
            p.setFont(f)
            p.drawText(area, Qt.AlignCenter, "WAITING FOR TRAJECTORY DATA")
            p.end()
            return

        n0, n1, e0, e1 = b
        span_n, span_e = max(2.0, n1 - n0), max(2.0, e1 - e0)
        margin = 0.12
        span_n *= (1 + margin); span_e *= (1 + margin)
        scale = min(area.width() / span_e, area.height() / span_n)
        cn, ce = (n0 + n1) / 2, (e0 + e1) / 2
        ox = area.center().x() - ce * scale
        oy = area.center().y() + cn * scale      # north = atas

        def to_xy(n, e):
            return QPointF(ox + e * scale, oy - n * scale)

        # ---- grid adaptif ----
        step = 1.0
        while step * scale < 34:
            step *= 2 if step in (1, 2, 5, 10, 20, 50) else 5
        f = p.font(); f.setFamily(theme.FONT_MONO); f.setPixelSize(theme.px(9)); p.setFont(f)
        n_start = math.floor(n0 / step) * step
        while n_start <= n1 + step:
            y = to_xy(n_start, ce).y()
            if area.top() <= y <= area.bottom():
                p.setPen(QtGui.QPen(QtGui.QColor(theme.HAIRLINE), 1, Qt.DotLine))
                p.drawLine(QPointF(area.left(), y), QPointF(area.right(), y))
                p.setPen(QtGui.QColor(theme.TEXT_DIM))
                p.drawText(QRectF(2, y - 7, pad_l - 6, 14), Qt.AlignRight | Qt.AlignVCenter,
                           f"{n_start:+.0f}".replace("+0", "0"))
            n_start += step
        e_start = math.floor(e0 / step) * step
        while e_start <= e1 + step:
            x = to_xy(cn, e_start).x()
            if area.left() <= x <= area.right():
                p.setPen(QtGui.QPen(QtGui.QColor(theme.HAIRLINE), 1, Qt.DotLine))
                p.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
                p.setPen(QtGui.QColor(theme.TEXT_DIM))
                p.drawText(QRectF(x - 22, area.bottom() + 2, 44, 12), Qt.AlignCenter, f"{e_start:+.0f}".replace("+0", "0"))
            e_start += step

        p.setPen(QtGui.QColor(theme.TEXT_FAINT))
        p.drawText(QRectF(2, 0, 40, 12), Qt.AlignLeft, "N↑")
        p.drawText(QRectF(area.right() - 40, area.bottom() + 16, 44, 12), Qt.AlignRight, "E→ m")

        # ---- lintasan ----
        def poly(pts, color, width):
            if len(pts) < 2:
                return
            path = QtGui.QPainterPath()
            first = True
            for (n, e) in pts:
                q = to_xy(n, e)
                if first:
                    path.moveTo(q); first = False
                else:
                    path.lineTo(q)
            p.setPen(QtGui.QPen(QtGui.QColor(color), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)

        poly(self.gt, theme.GT, 2.0)
        poly(self.vio, theme.ACCENT, 1.6)

        # ---- posisi kendaraan (ground truth) ----
        if self.pose:
            n, e, yaw = self.pose
            q = to_xy(n, e)
            p.save()
            p.translate(q)
            p.rotate(math.degrees(-yaw))     # NED yaw -> layar (north up)
            tri = QtGui.QPolygonF([QPointF(0, -7), QPointF(5, 6), QPointF(-5, 6)])
            p.setBrush(QtGui.QColor(theme.TEXT))
            p.setPen(QtGui.QPen(QtGui.QColor(theme.BG), 1))
            p.drawPolygon(tri)
            p.restore()

        # ---- skala bar ----
        bar_m = step if step * scale > 40 else step * 2
        px = bar_m * scale
        bx, by = area.left() + 4, area.bottom() - 8
        p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT_FAINT), 1))
        p.drawLine(QPointF(bx, by), QPointF(bx + px, by))
        p.drawLine(QPointF(bx, by - 3), QPointF(bx, by + 3))
        p.drawLine(QPointF(bx + px, by - 3), QPointF(bx + px, by + 3))
        p.drawText(QRectF(bx, by - 18, px, 12), Qt.AlignCenter, f"{bar_m:.0f} m")

        # ---- legenda ----
        lx = area.right() - 92
        ly = area.top() + 2
        p.setPen(QtGui.QPen(QtGui.QColor(theme.GT), 2))
        p.drawLine(QPointF(lx, ly + 6), QPointF(lx + 16, ly + 6))
        p.setPen(QtGui.QColor(theme.TEXT_DIM))
        p.drawText(QRectF(lx + 20, ly, 70, 12), Qt.AlignLeft | Qt.AlignVCenter, "GT (sim)")
        p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 2))
        p.drawLine(QPointF(lx, ly + 20), QPointF(lx + 16, ly + 20))
        p.setPen(QtGui.QColor(theme.TEXT_DIM))
        p.drawText(QRectF(lx + 20, ly + 14, 70, 12), Qt.AlignLeft | Qt.AlignVCenter, "VIO (VINS)")

        # ---- baris statistik: lebar diukur, jadi tidak mungkin saling tumpuk ----
        s = self.stats
        p.setPen(QtGui.QColor(theme.TEXT_FAINT))
        f.setFamily(theme.FONT_MONO); f.setPixelSize(theme.px(9)); p.setFont(f)
        fm = QtGui.QFontMetrics(f)
        items = [("GT", s.get("gt_len")), ("VIO", s.get("vio_len")), ("ERR", s.get("err")),
                 ("RMSE", s.get("rmse")), ("SKALA", s.get("scale"))]
        x = area.left() + 4
        for k, v in items:
            txt = f"{k} {v}" if v else f"{k} --"
            w = fm.horizontalAdvance(txt) + 16
            p.drawText(QRectF(x, area.bottom() + 16, w, 12), Qt.AlignLeft | Qt.AlignVCenter, txt)
            x += w
        p.end()


class BarTengah(QtWidgets.QWidget):
    """Bar teleop berpusat-NOL (audit 13 Okt).

    Akar masalah: QProgressBar(range -100..100) selalu mengisi dari KIRI —
    nilai 0 (stick netral) tampil 50% terisi sehingga pilot salah baca.
    Widget ini menggambar: track + garis tengah + isian dari titik tengah.
    API: setValue(-100..100), value().
    """

    def __init__(self, tinggi=8, parent=None):
        super().__init__(parent)
        self._v = 0
        self.setFixedHeight(theme.px(tinggi))
        self.setMinimumWidth(60)

    def setValue(self, v):
        try:
            v = int(max(-100, min(100, int(v))))
        except (TypeError, ValueError):
            v = 0
        if v != self._v:
            self._v = v
            self.update()

    def value(self):
        return self._v

    def paintEvent(self, _ev):                          # noqa: N802 (Qt)
        p = QtGui.QPainter(self)
        try:
            p.setRenderHint(QtGui.QPainter.Antialiasing)
            wd, ht = self.width(), self.height()
            r = ht / 2.0
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(255, 255, 255, 22))
            p.drawRoundedRect(0, 0, wd, ht, r, r)
            mid = wd / 2.0
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 46), 1))
            p.drawLine(int(mid), 0, int(mid), ht)
            if self._v != 0:
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(theme.warna("accent"))
                x1 = mid + (mid * (self._v / 100.0))
                kiri = int(min(mid, x1))
                lebar = max(1, int(abs(x1 - mid)))
                p.drawRoundedRect(kiri, 0, lebar, ht, r, r)
        finally:
            p.end()
