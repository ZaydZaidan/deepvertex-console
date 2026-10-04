# Third-Party Notices — DRAFT (perbarui saat packaging)

Repo/artefak ini memakai komponen pihak ketiga berikut. Sebelum distribusi,
sertakan teks lisensi lengkap untuk setiap komponen yang benar-benar dikirim.

| Komponen | Lisensi | Catatan kepatuhan |
|---|---|---|
| VINS-Fusion (HKUST-Aerial-Robotics) | GPLv3 | Dikirim sebagai komponen terpisah (container/proses sendiri). Sertakan teks GPLv3 + penawaran source (upstream + modifikasi kita bila ada). |
| ROS 2 (rclpy/rclcpp dll) | Apache-2.0 | Sertakan teks lisensi. |
| PySide6 (Qt for Python) | LGPLv3 | Dynamic linking, tanpa modifikasi; sertakan lisensi. |
| PyQt5 | GPLv3 (DIHINDARI) | Jangan masuk artefak distribusi kecuali sudah sesuai rencana (migrasi ke PySide6). |
| OpenCV | Apache-2.0 | |
| Ceres Solver | BSD-3-Clause | |
| Eigen | MPL-2.0 | |
| GStreamer | LGPL | Dipakai dinamis. |
| pymavlink | LGPLv3 | Import dinamis. |

Stonefish (simulator) TIDAK didistribusikan.
