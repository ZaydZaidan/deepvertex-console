"""ui_common.py — komponen UI bersama untuk konsol (Rate UI, Segmen, kosongkan).

Segmen / FlowLayout / FlowBox / StatusBar DISALIN dari konsol sim (sudah migrasi
PySide6) supaya konsol memakai KOMPONEN & GAYA YANG SAMA — satu bahasa desain,
tanpa mengimpor seluruh aplikasi sim. Kalau gaya di salah satu sisi berubah,
ubah KEDUANYA (sinkron) — komponen ini diuji lewat uji tampilan konsol.
"""
from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt


class Segmen(QtWidgets.QWidget):
    """Deretan tombol pilihan EKSKLUSIF (gaya "segmented control" rancangan)."""

    dipilih = QtCore.Signal(str)

    def __init__(self, opsi, parent=None):
        """opsi = [(kunci, label, tooltip), ...]"""
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.tombol = {}
        for kunci, label, tip in opsi:
            b = QtWidgets.QPushButton(label)
            b.setObjectName("SegBtn")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, k=kunci: self._pilih(k))
            lay.addWidget(b)
            self.tombol[kunci] = b

    def _pilih(self, kunci):
        self.set_dipilih(kunci)
        self.dipilih.emit(kunci)

    def set_dipilih(self, kunci):
        for k, b in self.tombol.items():
            b.setChecked(k == kunci)


class FlowLayout(QtWidgets.QLayout):
    """Layout yang MEMBUNGKUS ke baris berikutnya kalau tidak muat."""

    def __init__(self, parent=None, margin=0, spacing=8):
        super().__init__(parent)
        self._item, self._spacing = [], spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._item.append(item)

    def count(self):
        return len(self._item)

    def itemAt(self, i):
        return self._item[i] if 0 <= i < len(self._item) else None

    def takeAt(self, i):
        return self._item.pop(i) if 0 <= i < len(self._item) else None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return False

    def tinggi_untuk(self, w):
        return self._do_layout(QtCore.QRect(0, 0, int(w), 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QtCore.QSize()
        for it in self._item:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, uji):
        m = self.contentsMargins()
        x, y = rect.x() + m.left(), rect.y() + m.top()
        kanan = rect.right() - m.right()
        tinggi = 0
        for it in self._item:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            if x + w > kanan and x > rect.x() + m.left():
                x = rect.x() + m.left()
                y += tinggi + self._spacing
                tinggi = 0
            if not uji:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), it.sizeHint()))
            x += w + self._spacing
            tinggi = max(tinggi, h)
        return y + tinggi - rect.y() + m.bottom()


class FlowBox(QtWidgets.QWidget):
    """Wadah satu baris yang MEMBUNGKUS bila sempit; tingginya dihitung ulang."""

    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self.setObjectName("FlowBox")
        self.lay = FlowLayout(self, margin=0, spacing=spacing)

    def tambah(self, w):
        self.lay.addWidget(w)
        self.sesuaikan()

    def sesuaikan(self):
        lebar = self.width() if self.width() > 50 else self.sizeHint().width()
        perlu = max(18, self.lay.tinggi_untuk(max(60, lebar)))
        lama = self.height()
        ubah = (self.minimumHeight() != perlu or self.maximumHeight() != perlu)
        if ubah:
            self.setMinimumHeight(perlu)
            self.setMaximumHeight(perlu)
        self.lay.activate()
        self.lay.setGeometry(self.rect())
        # repaint — sisa piksel layout lama jangan tertinggal sebagai "tulisan
        # berbayang" (laporan pemilik 17 Okt: ghost teks status di PRE-FLIGHT).
        # Repaint PENUH induk tiap ada perubahan: strip sempit TIDAK cukup —
        # elemen di bawah ikut bergeser & area lama bisa tertinggal.
        self.update()
        if ubah and self.parentWidget() is not None:
            self.parentWidget().update()
        self.updateGeometry()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.sesuaikan()

    def sizeHint(self):
        m = self.lay.contentsMargins()
        lebar = m.left() + m.right()
        tinggi = 18
        for i in range(self.lay.count()):
            it = self.lay.itemAt(i)
            s = it.sizeHint()
            lebar += s.width() + self.lay._spacing
            tinggi = max(tinggi, s.height())
        return QtCore.QSize(lebar, tinggi)


class StatusBar(QtWidgets.QWidget):
    """Bilah status bersegmen: satu baris kelompok, dipisah garis 1 px (rancangan §9)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusBar")
        # WAJIB untuk QWidget biasa: tanpa atribut ini Qt MENGABAIKAN `background:`
        # dari stylesheet (bilah jadi tembus pandang) — pelajaran konsol sim.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(30)
        self.lay = QtWidgets.QHBoxLayout(self)
        self.lay.setContentsMargins(8, 0, 8, 0)
        self.lay.setSpacing(8)

    def tambah_pemisah(self):
        sep = QtWidgets.QFrame()
        sep.setObjectName("StatusSep")
        sep.setFixedSize(1, 18)
        self.lay.addWidget(sep)

    def sesuaikan(self):
        """Tidak ada perhitungan tinggi: bilah dipatok 30 px seperti rancangan."""


def kolom_rel(min_w=330, max_w=470):
    """Kolom rel (kiri/kanan): isinya tinggi ALAMI, kelebihannya jadi scrollbar."""
    inner = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(inner)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)                # rel: jarak antar panel 6 px
    lay.addStretch(1)
    sa = QtWidgets.QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(inner)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    sa.setFrameShape(QtWidgets.QFrame.NoFrame)
    sa.setMinimumWidth(min_w)
    sa.setMaximumWidth(max_w)
    return sa, lay


def kosongkan(lay):
    """Keluarkan semua item dari layout tanpa menghancurkan widget-nya."""
    while lay.count():
        lay.takeAt(0)


def isi_kolom(lay, items, vis):
    """items = [(key, widget, stretch)]; daftarkan widget ke `vis` (untuk toggle View)."""
    keys = []
    for key, wdg, stretch in items:
        lay.addWidget(wdg, stretch)
        vis[key] = wdg
        keys.append(key)
    return keys
