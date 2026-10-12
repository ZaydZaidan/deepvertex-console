# work/ — area kerja TAHAP 1 (Project Coral Mapping UAV)

Pusat kerja Langkah B (semua tanpa hardware dulu). Sumber asli di `~/ros2_ws/...`
TIDAK disentuh; semua eksperimen & kode T1 hidup di sini.

Repo GitHub (privat): `DeepVertex-Underwater-Drone-GUI-Installer` — kode T1 ber-versi di sini.
Repo ini WAJIB tetap privat (closed-source); artefak distribusi ke klien (binary-only +
lisensi) disiapkan terpisah saat packaging; `-SBC-Installer` di-park.

- `gui_t1/` — **konsol nyata T1** (salinan kerja GUI sim → migrasi PySide6 SELESAI
  + modul baru: `video.py`, `rawcap.py`, `rov_console_real.py`).
  Cara jalan & resep penting: `gui_t1/README_T1.md`.
- `onboard/` — **raw_agent.py**: perekam RAW onboard (produksi di Pi/BlueOS;
  uji lokal: `--test`). Protokol HTTP-nya dipakai `gui_t1/rawcap.py`.
- `tests/` — uji tanpa hardware:
  - `test_video.py` (display/rekam/snapshot vs stream dummy),
  - `test_raw.py` (agen + transfer/verifikasi/hapus),
  - `test_console.py` (konsol offscreen penuh + `fake_vehicle.py` MAVLink palsu).
- `migrate_pyqt5_to_pyside6.py` — skrip migrasi binding (re-runnable, 58 penggantian).

Catatan dependensi lokal (dev): plugin `h264parse` diekstrak tanpa sudo ke
`~/.hermes/cache/scratch/badpkgs/` (dari paket `gstreamer1.0-plugins-bad`).
Untuk produksi: pasang paket tersebut, atau bundel pluginnya di `gui_t1/plugins/`.
