#!/usr/bin/env python3
"""Entry point for the Windows one-file build (PyInstaller).

The Linux launcher is `run_console_real.sh`; this shim exists so the packaged
.EXE boots identically (`python windows_entry.py`) with gui_t1/ on the path.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "gui_t1"))

from rov_console_real import main  # noqa: E402

if __name__ == "__main__":
    main()
