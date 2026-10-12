"""Menemukan dan menanam (embed) jendela simulator Stonefish ke dalam widget Qt.

Window Stonefish adalah jendela X11 milik proses `stonefish_simulator` (SDL/GLFW).
Qt5 bisa menampung jendela asing lewat QWindow.fromWinId() + createWindowContainer(),
sehingga viewport simulator tampil di dalam dashboard (bukan jendela terpisah).
"""
import re
import subprocess
import time

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtCore import Qt

WIN_RE = re.compile(
    r'^\s*(0x[0-9a-fA-F]+)\s+"([^"]*)":\s*\(([^)]*)\)\s+(\d+)x(\d+)\+(-?\d+)\+(-?\d+)')


def list_windows():
    try:
        out = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:
        return []
    wins = []
    for line in out.splitlines():
        m = WIN_RE.match(line)
        if m:
            wins.append({
                "id": int(m.group(1), 16),
                "name": m.group(2),
                "cls": m.group(3),
                "w": int(m.group(4)),
                "h": int(m.group(5)),
            })
    return wins


def find_window(needle="stonefish", min_size=200):
    for w in list_windows():
        hay = f"{w['name']} {w['cls']}".lower()
        if needle.lower() in hay and w["w"] >= min_size and w["h"] >= min_size:
            return w
    return None


def window_alive(wid):
    r = subprocess.run(["xwininfo", "-id", hex(wid)], capture_output=True)
    return r.returncode == 0


def wm_present():
    """True bila ada window manager (baca _NET_SUPPORTING_WM_CHECK di root).

    Penting: reparent jendela asing untuk embed hanya andal saat ada WM yang
    mengelola tata letak. Di display tanpa WM (Xvfb murni) reparent membuat
    jendela utama Qt kolaps dan bisa merusak permukaan OpenGL simulator.
    """
    import ctypes
    import os
    try:
        x11 = ctypes.CDLL("libX11.so.6")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XInternAtom.restype = ctypes.c_ulong
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
            ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

        dpy = x11.XOpenDisplay(os.environ.get("DISPLAY", ":0").encode())
        if not dpy:
            return False
        root = x11.XDefaultRootWindow(dpy)
        atom = x11.XInternAtom(dpy, b"_NET_SUPPORTING_WM_CHECK", False)
        actual_type = ctypes.c_ulong()
        actual_format = ctypes.c_int()
        nitems = ctypes.c_ulong()
        bytes_after = ctypes.c_ulong()
        prop = ctypes.c_void_p()
        x11.XGetWindowProperty(dpy, root, atom, 0, 1, False, 0, ctypes.byref(actual_type),
                               ctypes.byref(actual_format), ctypes.byref(nitems),
                               ctypes.byref(bytes_after), ctypes.byref(prop))
        found = bool(nitems.value)
        if prop.value:
            x11.XFree(prop)
        x11.XCloseDisplay(dpy)
        return found
    except Exception:
        return False


def _reparent_to_root(wid):
    """Kembalikan jendela asing ke root window (lepas dari jendela Qt) via XReparentWindow."""
    import ctypes
    import os
    try:
        x11 = ctypes.CDLL("libX11.so.6")
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XReparentWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                        ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]

        dpy = x11.XOpenDisplay(os.environ.get("DISPLAY", ":0").encode())
        if not dpy:
            return False
        root = x11.XDefaultRootWindow(dpy)
        x11.XReparentWindow(dpy, ctypes.c_ulong(wid), ctypes.c_ulong(root), 0, 0)
        x11.XSync(dpy, False)
        x11.XCloseDisplay(dpy)
        return True
    except Exception:
        return False


class SimEmbed(QtWidgets.QWidget):
    """Wadah yang otomatis mencari jendela Stonefish dan menanamnya."""

    def __init__(self, needle="stonefish", parent=None):
        super().__init__(parent)
        self.needle = needle
        self.wid = None
        self.window = None
        self.container = None

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(0)
        self.holder = QtWidgets.QWidget(self)
        self.holder.setObjectName("SimHolder")
        self.holder_lay = QtWidgets.QHBoxLayout(self.holder)
        self.holder_lay.setContentsMargins(0, 0, 0, 0)
        self.holder_lay.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.holder, 1)

        self.placeholder = QtWidgets.QLabel("MENUNGGU JENDELA SIMULATOR STONEFISH …")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("SimPlaceholder")
        try:
            import theme as _t
            _t.set_peran(self.placeholder, "faint")
        except Exception:                                     # noqa: BLE001
            pass
        self.holder_lay.addWidget(self.placeholder)

    # ------------------------------------------------------------------ #
    def attach_once(self):
        """Cari + tanam kalau belum tertanam. Mengembalikan True bila tertanam."""
        if self.window is not None:
            return True
        w = find_window(self.needle)
        if w is None:
            return False
        try:
            self.window = QtGui.QWindow.fromWinId(w["id"])
            self.container = QtWidgets.QWidget.createWindowContainer(self.window, self.holder)
            self.container.setFixedSize(w["w"], w["h"])
            self.placeholder.hide()
            self.holder_lay.addWidget(self.container)
            self.wid = w["id"]
            return True
        except Exception:
            self.window = None
            self.container = None
            return False

    def embed_sane(self, own_wid):
        """Cek apakah jendela dashboard masih berukuran wajar setelah penanaman.

        Di display tanpa window manager (mis. Xvfb) reparent jendela asing membuat
        jendela utama kolaps jadi 1x1 -> deteksi ini memicu fallback yang jujur.
        """
        try:
            out = subprocess.run(["xwininfo", "-id", hex(own_wid)],
                                 capture_output=True, text=True, timeout=4).stdout
            m = re.search(r"Width:\s+(\d+)", out)
            m2 = re.search(r"Height:\s+(\d+)", out)
            if not (m and m2):
                return False
            return int(m.group(1)) > 200 and int(m2.group(1)) > 200
        except Exception:
            return False

    def detach(self):
        """Lepas jendela asing SEBELUM jendela Qt dihancurkan.

        Penting: selama ditanam, jendela simulator menjadi ANAK jendela dashboard.
        Kalau dashboard ditutup/di-kill tanpa melepasnya, X11 ikut menghancurkan jendela
        anak -> simulator kehilangan permukaan OpenGL dan berhenti publish citra.
        """
        if self.wid is not None:
            _reparent_to_root(self.wid)
        if self.container is not None:
            self.holder_lay.removeWidget(self.container)
            self.container.setParent(None)
            self.container.deleteLater()
            self.container = None
        self.window = None
        self.wid = None
        self.placeholder.show()

    def check_alive(self):
        """Deteksi simulator restart (window id berubah) -> tanam ulang."""
        if self.window is not None and not window_alive(self.wid):
            self.detach()
            return self.attach_once()
        return self.window is not None


# --------------------------------------------------------------------------- #
# Mode MIRROR (aman): salin isi jendela simulator sebagai gambar.
# Tidak ada reparent sama sekali -> tidak bisa mengganggu window manager/sesi desktop.
# --------------------------------------------------------------------------- #
def grab_window_image(wid):
    """Ambil isi jendela X11 sebagai PIL.Image (via xwd, read-only). None bila gagal."""
    try:
        r = subprocess.run(["xwd", "-silent", "-id", hex(wid)], capture_output=True, timeout=6)
        if r.returncode != 0 or len(r.stdout) < 1024:
            return None
        import io
        from PIL import Image
        return Image.open(io.BytesIO(r.stdout)).convert("RGB")
    except Exception:
        return None


class MirrorWorker(QtCore.QThread):
    """Thread pengambil cuplikan jendela simulator (mode mirror)."""

    sig_frame = QtCore.Signal(object)      # PIL.Image
    sig_state = QtCore.Signal(str)         # teks status

    def __init__(self, needle="stonefish", fps=6.0, parent=None):
        super().__init__(parent)
        self.needle = needle
        self.period = 1.0 / max(fps, 0.5)
        self._run = True
        self.wid = None

    def stop(self):
        self._run = False
        self.wait(1500)

    def run(self):
        while self._run:
            t0 = time.monotonic()
            if self.wid is None or not window_alive(self.wid):
                w = find_window(self.needle)
                self.wid = w["id"] if w else None
                if self.wid is None:
                    self.sig_state.emit("menunggu jendela simulator")
            if self.wid is not None:
                img = grab_window_image(self.wid)
                if img is not None:
                    self.sig_frame.emit(img)
                    self.sig_state.emit(f"mirror · {img.width}×{img.height}")
                else:
                    self.sig_state.emit("mirror gagal (jendela tertutup objek lain?)")
            dt = time.monotonic() - t0
            time.sleep(max(0.0, self.period - dt))
