# DeepVertex — Underwater Drone GUI (Topside)

Paket **topside / Ubuntu 24.04** untuk konsol misi DeepVertex pada BlueROV2:
GUI pilot, VIO (VINS-Fusion), teleop MAVLink, dan recorder. Seluruh pemrosesan
berat berjalan di komputer topside; ROV dihubungkan lewat tether.

Berpasangan dengan repo SBC: `DeepVertex-Underwater-Drone-SBC-Installer`.

> Status: scaffold awal (4 Okt 2026) — konten installer diisi pada Langkah 2
> rencana delivery. Belum ada rilis.

## Rencana isi
- `install/` — skrip + compose instalasi (Docker) untuk topside
- `docs/` — panduan instalasi, operasi pilot, kompatibilitas versi
- `LICENSE` (proprietary) dan `THIRD_PARTY_NOTICES.md`

## Kompatibilitas versi
Tabel versi GUI ↔ SBC akan diisi di `docs/COMPATIBILITY.md` setelah spec
deployment (Langkah 0) selesai.
