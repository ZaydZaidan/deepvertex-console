# Third-Party Notices — DeepVertex Underwater Drone Software (T1)

Repo SOURCE (privat) untuk sistem Tahap 1. Komponen pihak ketiga yang dipakai:

| Komponen | Lisensi | Catatan kepatuhan |
|---|---|---|
| PySide6 (Qt for Python) | LGPLv3 | Dynamic linking (import), tanpa modifikasi; sertakan lisensi saat distribusi. |
| Qt 6 | LGPLv3 | Idem; tanpa modifikasi. |
| GStreamer (core/base/good/libav/ugly) | LGPL | Dipakai dinamis; tanpa modifikasi. |
| Plugin `h264parse` (gstreamer1.0-plugins-bad) | LGPL | Dibundel OPSIONAL oleh installer (tanpa sudo) utk rekaman `.mkv`; tanpa modifikasi. |
| pymavlink | LGPLv3 | Import dinamis. |
| numpy | BSD-3-Clause | |
| Font DeepVertex Sans | turunan Plus Jakarta Sans (OFL) | Lihat `gui_t1/fonts/OFL.txt`. |

Tidak ada komponen GPL yang dikirim di T1 (VINS-Fusion/Ceres = Tahap 2, dengan skema
komplians terpisah bila dipakai lagi). PyQt5 (GPL) TIDAK dipakai — sudah dimigrasi
ke PySide6.
