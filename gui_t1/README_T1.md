# Konsol Misi ROV — TAHAP 1 (mode ROV) — catatan kerja

Konsol untuk ROV asli (BlueROV2 + BlueOS). Basis desain & widget = GUI sim yang
sekarang (ROV Mission Console, termasuk redesign 28 Sep 2026); binding sudah
**PySide6**. Fitur T1: 3 stream kamera (depan + stereo L/R), telemetri MAVLink,
capture compressed + snapshot (topside), dan **capture RAW onboard** (opsi B — lewat
agen `../onboard/raw_agent.py`). AUTONOMOUS dan VIO = *coming soon* (Tahap 2, mode AUV).

## Jalankan

    ./run_console_real.sh [--mavlink udpin:0.0.0.0:14550] [--outdir ~/DeepVertex-ROV-Captures]

Opsi: `--front-port/--left-port/--right-port` (default 5600/5601/5602 — port UDP
stream dari BlueOS camera manager), `--raw-agent http://127.0.0.1:8765`.

## Dependensi

- `.venv` (PySide6 6.12 + `--system-site-packages`: GStreamer & pymavlink dari sistem).
- GStreamer: `rtph264depay`, `avdec_h264`, `matroskamux` (base/good/libav — sudah ada).
- Rekaman `.mkv` butuh `h264parse` (paket `gstreamer1.0-plugins-bad`). Bila tidak ada,
  rekaman otomatis jatuh ke format elementer `.h264` (tetap jalan).
  Opsi tanpa sudo: ekstrak paketnya lalu taruh `libgstvideoparsersbad.so` di
  `plugins/gstreamer-1.0/` (+ lib pendukung di `plugins/lib/`) — dibaca otomatis
  oleh `run_console_real.sh`.

## Uji (tanpa hardware)

    ../tests/test_video.py      # display + rekam + snapshot lawan stream dummy
    ../tests/test_raw.py        # agen RAW: start/stop → transfer+sha256 → hapus
    ../tests/test_console.py    # konsol penuh offscreen + MAVLink palsu + agen RAW

Prasyarat test_raw/test_console: agen RAW jalan di :8791, mis.

    /usr/bin/python3 ../onboard/raw_agent.py --test --dir /tmp/dv_raw \
        --chunk-mb 4 --port 8791 --width 640 --height 360 --fps 25

## Resep teknis penting (jangan diubah tanpa uji ulang)

- **Relai RTP**: `tee → queue → valve(drop) → udpsink(127.0.0.1:P+100, sync=false, async=false)`.
  `async=false` WAJIB — tanpa itu pipeline macet di PAUSED selama valve tertutup.
- **Perekam** = subproses `gst-launch-1.0 -q -e` dengan argv **TOKEN** (bukan satu
  string kutipan — gst-launch mem-parse per argumen). Stop = tutup valve, lalu
  SIGINT ke subproses (`-e` → EOS → file final).
- **Display tanpa GstApp typelib**: frame diambil lewat pad probe pada `fakesink`.
- Depay default = format AVC; butuh capsfilter untuk byte-stream; matroskamux hanya
  menerima AVC/avc3 → karena itu h264parse (plugin -bad) jalur utamanya.
