#!/usr/bin/env python3
"""Migrasi PyQt5 -> PySide6 untuk work/gui_t1 (Delivery, Langkah B).

Aturan (mekanis, re-runnable):
  - from PyQt5 / import PyQt5        -> PySide6
  - QtCore.pyqtSignal                -> QtCore.Signal
  - pyqtSignal / pyqtSlot (sisa)     -> Signal / Slot
  - exec_()                          -> exec()            (Qt6)
  - QtWidgets.QActionGroup           -> QtGui.QActionGroup (Qt6: pindah ke QtGui)
  - QtWidgets.QAction                -> QtGui.QAction      (Qt6: pindah ke QtGui)

Catatan: komentar/docstring yang menyebut "PyQt5" secara historis TIDAK diubah
(mis. catatan bug di widgets.py) supaya tetap akurat.
Jalankan:  /usr/bin/python3 migrate_pyqt5_to_pyside6.py
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent / "gui_t1"

RULES = [
    ("from PyQt5", "from PySide6", re.compile(r"\bfrom PyQt5\b")),
    ("import PyQt5", "import PySide6", re.compile(r"\bimport PyQt5\b")),
    ("QtCore.pyqtSignal", "QtCore.Signal", re.compile(r"\bQtCore\.pyqtSignal\b")),
    ("pyqtSignal->Signal", "Signal", re.compile(r"\bpyqtSignal\b")),
    ("pyqtSlot->Slot", "Slot", re.compile(r"\bpyqtSlot\b")),
    ("exec_()", "exec()", re.compile(r"\bexec_\(\)")),
    ("QtWidgets.QActionGroup", "QtGui.QActionGroup", re.compile(r"\bQtWidgets\.QActionGroup\b")),
    ("QtWidgets.QAction", "QtGui.QAction", re.compile(r"\bQtWidgets\.QAction\b")),
]


def main():
    total = 0
    for path in sorted(ROOT.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        out = src
        counts = []
        for label, new_name, rx in RULES:
            out, n = rx.subn(new_name, out)
            if n:
                counts.append("%s x%d" % (label, n))
                total += n
        if out != src:
            path.write_text(out, encoding="utf-8")
            print("[OK] %s: %s" % (path.name, ", ".join(counts)))
        else:
            print("[--] %s: tidak ada perubahan" % path.name)
    print("\nTotal penggantian: %d" % total)


if __name__ == "__main__":
    main()
