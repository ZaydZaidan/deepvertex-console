# Third-Party Notices — DeepVertex Underwater Drone Software (ROV mode)

This source repository is private. The shipped application dynamically links the
following third-party components:

| Component | License | Compliance note |
|---|---|---|
| PySide6 (Qt for Python) | LGPLv3 | Dynamic linking only (import), no modifications; ship license text with distribution. |
| Qt 6 | LGPLv3 | Same as above; no modifications. |
| GStreamer (core/base/good/libav/ugly) | LGPL | Used dynamically, unmodified. |
| `h264parse` plugin (gstreamer1.0-plugins-bad) | LGPL | Optionally bundled by the installer (no sudo) for `.mkv` recording; unmodified. |
| pymavlink | LGPLv3 | Dynamic import. |
| numpy | BSD-3-Clause | |
| DeepVertex Sans font | derived from Plus Jakarta Sans (OFL) | See `gui_t1/fonts/OFL.txt`. |

No GPL components are shipped in ROV mode (VINS-Fusion / Ceres are autonomy items with
a separate compliance plan if reintroduced). PyQt5 (GPL) is **not** used — the code
was migrated to PySide6.

This repository's own source is licensed under the PolyForm Strict License
1.0.0 (see LICENSE). The components listed above remain under their own
licenses; the product license does not change those terms.
