"""laysnap.py — Chrome-style drag & snap window organizer (owner request 14 Oct).

Lets the operator re-arrange console panels BY HAND:
 * press a panel header and DRAG it;
 * while dragging, snap suggestions appear (half-transparent accent frames):
     - rail KIRI              -> panel docks to the left rail (bottom of stack)
     - rail KANAN             -> panel docks to the right rail
     - atas (~35%) / bawah in any visible panel -> insert above/below that
       panel (Chrome split feel; inside a rail it reorders, in the middle
       column it re-sorts the live camera/map stack)
 * drop OUTSIDE the console (or onto a floating window) -> the panel becomes
   a FREE-FLOATING top-level window (Chrome "pop out"). Dragging a floating
   panel back into the console docks it again;
 * ESC cancels a drag (panel snaps home unchanged);
 * arrangements persist as the "Custom" layout (`saved/layout_custom.json`,
   in-app saved/ folder) and are applied on the next start-up; the four
   built-in presets keep overwriting them until the user saves again.

Design rules kept from the rest of the console:
 * the anti-stuck pattern is reused: applying any arrangement FIRST hides
   every panel (takeAt() does not re-parent), then re-shows only placed ones;
 * drag machinery is QDrag-based (native, DnD events) BUT the controller
   exposes `simulate_*` calls used by tests so the suite never needs real
   mouse motion;
 * every string the user can read is English; Pewaris: Asian? no.. English only.
"""

from __future__ import annotations

import copy
import json
import os

from PySide6 import QtCore, QtGui, QtWidgets

Qt = QtCore.Qt

DRAG_OFFSET_PX = 8          # minimum travel before a drag starts
_RATIO_SPLIT_TOP = 0.35     # top zone of a targeted panel = "ABOVE"
_RATIO_SPLIT_BOTTOM = 0.65  # bottom zone = "BELOW"
ZONE_THICKNESS = 46         # thickness px of the "ABOVE/BELOW" highlight bar
LABEL_COLOR_ON = True

CUSTOM_FILENAME = "layout_custom.json"


def custom_path(app_dir: str) -> str:
    return os.path.join(app_dir, "saved", CUSTOM_FILENAME)


def _truncate(text: str, n: int = 22) -> str:
    return text if len(text) <= n else text[: n - 1] + "\u2026"


# --------------------------------------------------------------------- overlay
class DragOverlay(QtWidgets.QWidget):
    """Full-console transparent overlay that paints snap suggestions.

    Zones are (QRect, label, title-fragment) tuples recomputed on every drag
    move.  Own look: accent fill ~26% + 2 px accent border + short label,
    matching the console's Apple-grade language without new theme colors.
    """

    def __init__(self, console):
        super().__init__(console)
        self.setObjectName("DragOverlay")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setWindowFlags(Qt.SubWindow | Qt.FramelessWindowHint)
        self._zones = []
        self.hide()

    def set_zones(self, zones):
        self._zones = zones
        if not zones:
            self.hide()
            return
        self.setGeometry(self._console_rect_local())
        self.show()
        self.raise_()
        self.update()

    def _console_rect_local(self):
        try:
            cr = self.parentWidget().contentsRect()
        except Exception:
            cr = QtCore.QRect(self.parentWidget().rect())
        return cr

    def paintEvent(self, event: QtGui.QPaintEvent):  # noqa: N802
        if not self._zones:
            return
        paint = QtGui.QPainter(self)
        paint.setRenderHint(QtGui.QPainter.Antialiasing)

        accent = QtGui.QColor("#00E5FF")
        c_fill = QtGui.QColor(accent)
        c_fill.setAlpha(60)
        for rect, label, _title in self._zones:
            path = QtGui.QPainterPath()
            path.addRoundedRect(QtGui.QRectF(rect), theme_px(8), theme_px(8))
            paint.fillPath(path, c_fill)
            pen = QtGui.QPen(accent)
            pen.setWidthF(theme_px(2))
            paint.setPen(pen)
            paint.drawPath(path)
            if label:
                f = paint.font()
                f.setPixelSize(theme_px(11))
                f.setBold(True)
                f.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, theme_px(1))
                paint.setFont(f)
                paint.setPen(QtGui.QPen(accent))
                paint.drawText(rect, Qt.AlignCenter, label)


def theme_px(v: int) -> int:
    """theme.px without importing theme (avoids circular imports)."""
    try:
        import theme  # local import by design
        return theme.px(v)
    except Exception:
        return int(v)


# ------------------------------------------------------- pure spec editing
def move_into_spec(spec, floats, key, rail, index):
    """Pure edit: add `key` into rail `rail` at `index`, removing it from
    wherever it currently lives (rails, mid stack, floating map)."""
    spec = {k: (list(v) if isinstance(v, list) else v) for k, v in spec.items()}
    floats = dict(floats or {})
    for r in ("left", "right"):
        spec[r] = [x for x in spec.get(r, []) if _key_of(x) != key]
    spec["mid"] = [it for it in spec.get("mid", []) if _key_of(it) != key]
    floats.pop(key, None)
    if rail in ("left", "right"):
        lst = spec.setdefault(rail, [])
        lst.insert(min(len(lst), max(0, index)), key)
    elif rail == "mid":
        keep = [it for it in spec.get("mid", [])]
        # store (key, stretch) — stretch 0 (hug content); cameras keep their
        # own stretch inside their original tuples; normal panels use 0.
        keep.insert(min(len(keep), max(0, index)), (key, 0))
        spec["mid"] = keep
    else:
        raise ValueError("unknown rail: %r" % (rail,))
    return spec, floats


def float_in_spec(spec, floats, key):
    """Pure edit: detach `key` from every rail/mid and mark it floating."""
    floats = dict(floats or {})
    floats[key] = {"geo": None, "on": True, "asal": (floats.get(key) or
                                                     {}).get("asal")}
    for r in ("left", "right"):
        spec[r] = [x for x in spec.get(r, []) if _key_of(x) != key]
    spec["mid"] = [it for it in spec.get("mid", []) if _key_of(it) != key]
    return spec, floats


def _key_of(item):
    return item[0] if isinstance(item, (tuple, list)) else item


