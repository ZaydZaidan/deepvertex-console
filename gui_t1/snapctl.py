"""snapctl.py — snap controller: Chrome-style organize-drag for console panels.

Manual drag on an app-level event filter (no QDrag): total control over
pop-out floats, ESC cancel, hover zones, and testability (`simulate_*`).
Geometry & zone logic lives here; the arrangement MODEL (spec rebuild,
persistence, applying) lives in the console (`RovConsole.tata_*`).
"""

from __future__ import annotations

import os

from PySide6 import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt
QEvent = QtCore.QEvent

from laysnap import (_RATIO_SPLIT_BOTTOM, _RATIO_SPLIT_TOP, DRAG_OFFSET_PX,
                     DragOverlay, _truncate, custom_path, theme_px)

DBG_ENABLE = os.environ.get("DV_LAYDBG") == "1"
_DBG = DBG_ENABLE

# zone kinds handed to the console model editor:
#   ("rail", "left"/"right")  -> append to that rail
#   ("above", key)/("below", key) -> insert next to that visible panel
#   ("end",)                  -> append to the middle (camera) column
Zone = tuple

GHOST_W, GHOST_H = 250, 30


# --------------------------------------------------------------------- ghost
class DragGhost(QtWidgets.QWidget):
    """Small always-on-top chip that follows the cursor while dragging."""

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint |
                         Qt.WindowStaysOnTopHint | Qt.X11BypassWindowManagerHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._title = ""
        self.resize(theme_px(GHOST_W), theme_px(GHOST_H))
        self.hide()

    def set_title(self, text: str):
        self._title = _truncate(text or "PANEL", 26)

    def follow(self, gpos: QtCore.QPoint):
        self.move(gpos.x() - self.width() // 2,
                  gpos.y() - self.height() - theme_px(12))
        self.raise_()
        self.show()

    def paintEvent(self, ev):  # noqa: N802
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        accent = QtGui.QColor("#00E5FF")
        path = QtGui.QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1, self.height() - 1,
                            theme_px(8), theme_px(8))
        p.fillPath(path, QtGui.QColor(12, 16, 20, 225))
        pen = QtGui.QPen(accent)
        pen.setWidthF(max(2, theme_px(2)))
        p.setPen(pen)
        p.drawPath(path)
        f = p.font()
        f.setPixelSize(theme_px(12))
        f.setBold(True)
        p.setFont(f)
        p.setPen(QtGui.QPen(QtGui.QColor("#EAF6FF")))
        p.drawText(self.rect().adjusted(theme_px(14), 0, -theme_px(14), 0),
                   Qt.AlignVCenter | Qt.AlignLeft, self._title)


# ---------------------------------------------------------------- controller
class SnapController(QtCore.QObject):
    """Install once per console: reads panel-head presses, runs the manual
    drag (ghost + snap overlay), commits to the console model."""

    def __init__(self, console):
        super().__init__(console)
        self.console = console
        self.overlay = DragOverlay(console)
        self._key_by_head = {}
        self._press = None           # (key, gpos, grab_offset)
        self._drag = None            # active drag dict
        self._ghost = DragGhost()
        self._pending = None         # zone kind tuple under cursor
        self._app_filter = False

    # ------------------------------------------------------------ lifecycle
    def attach(self):
        app = QtWidgets.QApplication.instance()
        for key, panel in self.console.P.items():
            head = self._panel_head(panel)
            if head is not None:
                self._key_by_head[head] = key
                head.setCursor(Qt.OpenHandCursor)
        if not self._app_filter and app is not None:
            app.installEventFilter(self)
            self._app_filter = True

    def detach(self):
        app = QtWidgets.QApplication.instance()
        if self._app_filter and app is not None:
            app.removeEventFilter(self)
            self._app_filter = False
        self._ghost.hide()

    @staticmethod
    def _panel_head(panel):
        lay = panel.layout()
        if lay is None or lay.count() == 0:
            return None
        return lay.itemAt(0).widget()

    # --------------------------------------------------------------- events
    def eventFilter(self, obj, ev):  # noqa: A003
        """App-level router. NEVER raises into Qt: an exception here would
        kill the console (seen live 15 Okt when the engine raced a real
        mouse drag against a preset change)."""
        try:
            return self._event_router(obj, ev)
        except Exception:                                  # noqa: BLE001
            import traceback
            traceback.print_exc()
            try:
                self._cleanup()
            except Exception:                              # noqa: BLE001
                pass
            return False

    def _event_router(self, obj, ev):  # noqa: A003
        t = ev.type()

        # ---- active drag: route everything through us, consume
        if self._drag is not None:
            if t == QEvent.MouseMove:
                self._drag_move(ev.globalPosition().toPoint())
                return True
            if t == QEvent.MouseButtonRelease:
                self._drag_finish(ev.globalPosition().toPoint())
                return True
            if t == QEvent.KeyPress and ev.key() == Qt.Key_Escape:
                self._drag_cancel()
                return True
            if t in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick,
                     QEvent.MouseButtonRelease, QEvent.MouseMove):
                return True
            return False

        # ---- idle: arm a drag from any panel HEAD press
        if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            key = self._head_key_of(obj)
            if _DBG and key is None:
                print("[laydbg] press di luar head:", obj, flush=True)
            if key is not None and not self._over_control(obj, ev.position()):
                if _DBG:
                    print("[laydbg] ARM", key, flush=True)
                panel = self.console.P.get(key)
                gpos = ev.globalPosition().toPoint()
                grab = gpos - panel.mapToGlobal(QtCore.QPoint(0, 0))
                self._press = (key, gpos, grab)
            return False

        if t == QEvent.MouseMove and self._press is not None:
            key, g0, grab = self._press
            g1 = ev.globalPosition().toPoint()
            if (g1 - g0).manhattanLength() >= DRAG_OFFSET_PX:
                self._press = None
                self._drag_begin(key, grab)
            return False

        if t == QEvent.MouseButtonRelease and self._press is not None:
            self._press = None       # plain click, no drag: passthrough
            return False

        return False

    def _head_key_of(self, obj):
        """Map an event object to a panel key when it lives inside a HEAD.
        Note: QWindow (native top-level event carriers) has no parentWidget —
        guard it (crash seen live 15 Okt)."""
        w = obj
        while w is not None:
            for head, key in self._key_by_head.items():
                if head is w:
                    return key
            pw = getattr(w, "parentWidget", None)
            w = pw() if callable(pw) else None
        return None

    @staticmethod
    def _over_control(head, qpos):
        """Press on an in-header control (combo/button) stays interactive."""
        try:
            child = head.childAt(qpos.toPoint())
        except Exception:
            return False
        if child is None or child is head:
            return False
        if isinstance(child, QtWidgets.QLabel):
            return False
        name = getattr(child, "objectName", lambda: "")()
        return name not in ("PanelTitle", "PanelHint", "DomainMark")

    # ----------------------------------------------------------- drag life
    def _drag_begin(self, key, grab):
        panel = self.console.P.get(key)
        if panel is None:
            return
        self._drag = {"key": key, "was_float": panel.isWindow(),
                      "grab": grab}
        title = panel.title.text() if getattr(panel, "title", None) else ""
        self._ghost.set_title(title)
        self._pending = None
        try:
            QtWidgets.QApplication.setOverrideCursor(Qt.ClosedHandCursor)
        except Exception:
            pass
        self._refresh_hook()

    def _drag_move(self, gpos):
        self._ghost.follow(gpos)
        self._refresh_hook(gpos)

    def _refresh_hook(self, gpos=None):
        if gpos is None:
            gpos = QtGui.QCursor.pos()   # Qt.QCursor TIDAK ADA di PySide6
        self._pending = self._kind_at(gpos)
        zones = self._paint_zones(self._pending)
        self.overlay.set_zones(zones if self._pending is not None else [])

    def _drag_finish(self, gpos):
        con = self.console
        kind, key = self._pending, self._drag["key"] if self._drag else None
        was_float = bool(self._drag.get("was_float")) if self._drag else False
        self._cleanup()
        if key is None:
            return
        if kind is not None:
            con.commit_drag(kind, key)          # dock/reorder (Chrome snap)
        elif not was_float:
            con.commit_float(key)               # dropped outside -> pop out
        # floating dropped back outside: window already lives there: no-op

    def _drag_cancel(self):
        self._cleanup()

    def _cleanup(self):
        self._drag = None
        self._press = None
        self._pending = None
        self.overlay.set_zones([])
        self._ghost.hide()
        try:
            QtWidgets.QApplication.restoreOverrideCursor()
        except Exception:
            pass

    # ------------------------------------------------------------- geometry
    def _kind_at(self, gpos):
        """Zone decision for a GLOBAL point; None = outside console."""
        con = self.console
        con_g = con.mapToGlobal(QtCore.QPoint(0, 0))
        loc = gpos - con_g
        w, h = con.width(), con.height()
        if not (-16 <= loc.x() <= w + 16 and -8 <= loc.y() <= h + 16):
            return None
        gl = con.col_left.geometry()
        gr = con.col_right.geometry()
        mid_w = gr.x() - gl.right()
        di_kiri = loc.x() <= gl.right() + mid_w * 0.30
        di_kanan = loc.x() >= gr.x() - mid_w * 0.30
        # Panel DULU (fix 15 Okt — laporan pemilik "tak bisa pindah ke ATAS"):
        # dulu zona rel dicek lebih dahulu sehingga drop di panel rel SELALU
        # "tambah ke ujung rel" (efeknya cuma bisa turun). Sekarang pita
        # atas/bawah panel berlaku JUGA untuk panel di rel; pita tengah
        # panel-rel = tetap "tambah ke ujung rel".
        hit = self._panel_at(loc)
        if hit is not None:
            key, prect = hit
            frac = (loc.y() - prect.y()) / max(1.0, prect.height())
            if frac < _RATIO_SPLIT_TOP:
                return ("above", key)
            if frac > _RATIO_SPLIT_BOTTOM:
                return ("below", key)
            if di_kiri:
                return ("rail", "left")
            if di_kanan:
                return ("rail", "right")
            return ("end",)
        if di_kiri:
            return ("rail", "left")
        if di_kanan:
            return ("rail", "right")
        return ("end",)

    def _panel_at(self, loc):
        """Innermost visible (console-local) panel under the point."""
        best = None
        for key, panel in self.console.P.items():
            if key not in self.console.vis or panel.isWindow():
                continue
            top_left = panel.mapTo(self.console, QtCore.QPoint(0, 0))
            prect = QtCore.QRect(top_left, panel.size())
            if not prect.contains(loc):
                continue
            if best is None or prect.area() < best[1].area():
                best = (key, prect)
        return best

    def _panel_rect(self, key):
        panel = self.console.P.get(key)
        tl = panel.mapTo(self.console, QtCore.QPoint(0, 0))
        return QtCore.QRect(tl, panel.size())

    def _paint_zones(self, kind):
        """Console-LOCAL paint rects describing the current suggestion."""
        if kind is None:
            return []
        con = self.console
        gl = con.col_left.geometry()
        gr = con.col_right.geometry()
        if kind[0] == "rail":
            geo = gl if kind[1] == "left" else gr
            label = "LEFT RAIL" if kind[1] == "left" else "RIGHT RAIL"
            return [(QtCore.QRect(geo), label, "")]
        if kind[0] in ("above", "below"):
            prect = self._panel_rect(kind[1])
            title = _truncate(con.P[kind[1]].title.text(), 24)
            h = min(52, prect.height())
            if kind[0] == "above":
                r = QtCore.QRect(prect.x(), prect.y(), prect.width(), h)
                label = "ABOVE \u2014 %s" % title
            else:
                r = QtCore.QRect(prect.x(), prect.bottom() - h + 1,
                                 prect.width(), h)
                label = "BELOW \u2014 %s" % title
            return [(r, label, title)]
        midband = QtCore.QRect(gl.right(), 0, gr.x() - gl.right(), con.height())
        return [(midband, "CAMERA COLUMN \u2014 ADD TO END", "")]

    # --------------------------------------------------------------- floats
    def _is_floating(self, key):
        p = self.console.P.get(key)
        return p is not None and p.isWindow()

    # --------------------------------------------------------- sim/test API
    def simulate_zone(self, cx, cy):
        """Console-LOCAL probe -> human zone kind (also for screenshots)."""
        kind = self._kind_at(self.console.mapToGlobal(QtCore.QPoint(cx, cy)))
        return kind

    def simulate_zones(self, cx, cy):
        self._pending = self._kind_at(
            self.console.mapToGlobal(QtCore.QPoint(cx, cy)))
        return [(str(r), lbl) for r, lbl, _t in self._paint_zones(self._pending)]

    def simulate_begin(self, key):
        """Test/automation entry: begin a drag without real mouse input."""
        panel = self.console.P.get(key)
        self._drag = {"key": key, "was_float": panel.isWindow() if panel else False,
                      "grab": QtCore.QPoint(4, 6)}
        self._sim_key = key
        self._ghost.set_title(panel.title.text() if panel else "")

    def simulate_move_to(self, cx, cy):
        gp = self.console.mapToGlobal(QtCore.QPoint(cx, cy))
        self._ghost.follow(gp)
        self._pending = self._kind_at(gp)
        self.overlay.set_zones(self._paint_zones(self._pending)
                               if self._pending is not None else [])
        return self._pending

    def simulate_drop(self, cx, cy):
        gp = self.console.mapToGlobal(QtCore.QPoint(cx, cy))
        self._pending = self._kind_at(gp)
        if self._drag is None:
            raise RuntimeError("simulate_begin() required first")
        key = self._sim_key
        was_float = self._drag.get("was_float")
        kind = self._pending
        self._cleanup()
        if kind is not None:
            self.console.commit_drag(kind, key)
        elif not was_float:
            self.console.commit_float(key)

    def simulate_cancel(self):
        self._sim_key = None
        self._cleanup()
