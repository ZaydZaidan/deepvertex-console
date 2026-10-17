"""wheelguard.py — guard roda-scroll "HARUS DIPENCET DULU" (permintaan pemilik).

Aturan (17 Okt, klarifikasi pemilik):
  * kursor cuma LEWAT di atas kontrol (combo/spinbox/slider) → roda TIDAK
    mengubah nilai; gulirannya diteruskan ke area gulir terdekat;
  * KONTROL BARU AKTIF roda setelah DIKLIK (pencet dulu); selama kursor belum
    meninggalkannya, roda boleh mengubah nilainya;
  * kursor keluar dari kontrol → kembali nonaktif (harus klik lagi).

Catatan desain: aturan lama berbasis FOKUS terlalu lengket — kontrol yang
pernah difokus tetap bisa diubah tanpa sengaja saat kursor lewat. Kini status
"dipegang" (engaged) hanya lahir dari KLIK dan berakhir saat kursor keluar.
"""
from PySide6 import QtCore, QtWidgets

_KELAS_TARGET = (QtWidgets.QComboBox, QtWidgets.QAbstractSpinBox,
                 QtWidgets.QSlider)


class _WheelGuard(QtCore.QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pegang = None              # kontrol yang baru DIKLIK (kursor di atasnya)

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QtCore.QEvent.Type.MouseButtonPress:
            if isinstance(obj, _KELAS_TARGET):
                self._pegang = obj
            return False
        if t == QtCore.QEvent.Type.Leave:
            if obj is self._pegang:
                self._pegang = None
            return False
        if t != QtCore.QEvent.Type.Wheel:
            return False
        if not isinstance(obj, _KELAS_TARGET):
            return False
        if obj is self._pegang:
            return False                     # sudah DIPENCET: roda boleh
        # belum dipencet → telan utk widget + teruskan ke area gulir terdekat
        p = obj.parent()
        while p is not None and not isinstance(p, QtWidgets.QAbstractScrollArea):
            p = p.parent()
        if p is not None:
            try:
                sb = p.verticalScrollBar()
                pd = ev.pixelDelta().y()
                ang = ev.angleDelta().y()
                if pd:
                    sb.setValue(sb.value() - int(pd))
                elif ang:
                    sb.setValue(sb.value() - int(ang / 120.0 * 72))
            except Exception:                # noqa: BLE001
                pass
        return True


def pasang(app):
    """Pasang guard ke QApplication (panggil sekali; mis. dari main())."""
    g = _WheelGuard(app)
    app.installEventFilter(g)
    return g
