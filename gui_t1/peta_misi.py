"""peta_misi.py — panel PETA MISI T1: rencana survei + jejak estimasi (dead-reckoning).

Posisi presisi menyusul di T2 (VIO). Di T1 peta menggambar:
  - area survei (default 10x10 m) + lajur rencana (spacing 1.0 m) — mirror geometri sim
  - jejak ESTIMASI posisi (integrasi heading kompas + perintah manual pilot)
  - penanda peristiwa (snapshot / mulai rekam) + tombol ANCOR (posisi sekarang = 0,0)
  - label "ESTIMASI" yang jelas — bukan posisi terukur.
"""
import math

from PySide6 import QtGui, QtWidgets
from PySide6.QtCore import Qt, QPointF, QRectF

import theme

BOX = 5.0            # setengah sisi area survei (m) — mirror area_x/y ±5
SPACING = 1.0        # jarak baris lajur (m) — mirror fixed_line_spacing_m
VMAX_FWD = 0.5       # m/s estimasi saat stick penuh (VERIFY di kolam)
VMAX_LAT = 0.35


class PlotMisi(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pos = [0.0, 0.0]        # [N, E] meter relatif anchor
        self.heading = 0.0
        self.trail = []
        self.markers = []
        self.setMinimumSize(280, 210)

    # ------------------------------------------------------------- data
    def ancor(self):
        self.pos = [0.0, 0.0]
        self.trail = []
        self.update()

    def set_pose(self, n, e, heading):
        self.pos = [float(n), float(e)]
        try:
            self.heading = float(heading) % 360.0
        except (TypeError, ValueError):
            pass
        self.update()

    def tambah_jejak(self):
        if not self.trail or (abs(self.trail[-1][0] - self.pos[0]) +
                              abs(self.trail[-1][1] - self.pos[1])) > 0.02:
            self.trail.append((self.pos[0], self.pos[1]))
            if len(self.trail) > 4000:
                self.trail = self.trail[-3000:]

    def add_marker(self, label=""):
        self.markers.append((self.pos[0], self.pos[1], label))
        if len(self.markers) > 500:
            self.markers = self.markers[-400:]
        self.update()

    # ------------------------------------------------------------- gambar
    def paintEvent(self, _):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        skala = min(w, h) / (2 * (BOX + 1.2))
        cx, cy = w / 2, h / 2

        def xy(n, e):
            return QPointF(cx + e * skala, cy - n * skala)

        # grid 1 m
        p.setPen(QtGui.QPen(QtGui.QColor(theme.HAIRLINE_HI), 1))
        for i in range(-int(BOX) - 1, int(BOX) + 2):
            p.drawLine(xy(-BOX - 1, i), xy(BOX + 1, i))
            p.drawLine(xy(i, -BOX - 1), xy(i, BOX + 1))

        # area survei + lajur rencana (garis arah N-S, spacing SPACING)
        p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT_DIM), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(xy(BOX, -BOX), xy(-BOX, BOX)))
        p.setPen(QtGui.QPen(QtGui.QColor(theme.warna("line_hi")
                                         if "line_hi" in dir(theme) else theme.HAIRLINE_HI), 1))
        x = -BOX
        while x <= BOX + 0.01:
            p.drawLine(xy(BOX, x), xy(-BOX, x))
            x += SPACING

        # jejak estimasi
        if len(self.trail) > 1:
            p.setPen(QtGui.QPen(QtGui.QColor(theme.ACCENT), 2))
            path = QtGui.QPainterPath()
            first = xy(*self.trail[0])
            path.moveTo(first)
            for n, e in self.trail[1:]:
                path.lineTo(xy(n, e))
            p.drawPath(path)

        # penanda
        p.setPen(QtGui.QPen(QtGui.QColor(theme.WARN), 1))
        p.setBrush(QtGui.QColor(theme.WARN))
        for n, e, _lab in self.markers[-60:]:
            c = xy(n, e)
            p.drawEllipse(c, 3, 3)

        # posisi ROV (segitiga mengikuti heading; utara = atas)
        c = xy(*self.pos)
        p.save()
        p.translate(c)
        p.rotate(self.heading)
        p.setBrush(QtGui.QColor(theme.ACCENT))
        p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT), 1))
        p.drawPolygon(QtGui.QPolygonF([QPointF(0, -8), QPointF(-6, 6), QPointF(6, 6)]))
        p.restore()

        # label
        f = p.font()
        f.setFamily(theme.FONT_UI)
        f.setPixelSize(theme.px(9))
        p.setFont(f)
        p.setPen(QtGui.QColor(theme.TEXT_FAINT))
        p.drawText(QPointF(6, h - 8), "ESTIMASI (dead-reckoning) — posisi presisi: T2/VIO")
        p.drawText(QPointF(6, 14), "area %.0fx%.0f m · lajur %.1f m" %
                   (2 * BOX, 2 * BOX, SPACING))
        # panah utara
        p.setPen(QtGui.QPen(QtGui.QColor(theme.TEXT_DIM), 1))
        p.drawLine(QPointF(w - 22, 24), QPointF(w - 22, 8))
        p.drawPolygon(QtGui.QPolygonF([QPointF(w - 22, 4), QPointF(w - 26, 12),
                                       QPointF(w - 18, 12)]))
        p.end()
