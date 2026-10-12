# ROV Mission Console — STAGE 1 (ROV mode) — working notes

Console for the real ROV (BlueROV2 + BlueOS). Design basis & widgets = the current
sim GUI (ROV Mission Console, including the Sep 28, 2026 redesign); the binding is now
**PySide6**. T1 features: 3 camera streams (front + stereo L/R), MAVLink telemetry,
compressed capture + snapshot (topside), and **onboard RAW capture** (option B — via
the `../onboard/raw_agent.py` agent). AUTONOMOUS and VIO = *coming soon* (Stage 2, AUV mode).

## Run

    ./run_console_real.sh [--mavlink udpin:0.0.0.0:14550] [--outdir ~/DeepVertex-ROV-Captures]

Options: `--front-port/--left-port/--right-port` (default 5600/5601/5602 — UDP stream
ports from the BlueOS camera manager), `--raw-agent http://127.0.0.1:8765`.

## Display demo (no hardware)

    bash demo_local.sh                 # 3 stream dummy + MAVLink palsu + agen RAW → konsol
    bash demo_local.sh --shot cek.png  # + screenshot otomatis (--shot-after detik)

Closing the console window = all demo helpers are shut down too; demo RAW data in /tmp/dv_raw_demo.

## Dependencies

- `.venv` (PySide6 6.12 + `--system-site-packages`: GStreamer & pymavlink from the system).
- GStreamer: `rtph264depay`, `avdec_h264`, `matroskamux` (base/good/libav — already present).
- `.mkv` recording needs `h264parse` (the `gstreamer1.0-plugins-bad` package). If absent,
  recording automatically falls back to the elementary `.h264` format (still works).
  Sudo-free option: extract the package then place `libgstvideoparsersbad.so` in
  `plugins/gstreamer-1.0/` (+ supporting libs in `plugins/lib/`) — read automatically
  by `run_console_real.sh`.
- `libxcb-cursor0` is **required** for the window to appear on X11 (Qt6 xcb). The installer
  bundles it into `tools/xcb/` (no sudo); alternative: `sudo apt install libxcb-cursor0`.

## Tests (no hardware)

    ../tests/test_video.py      # display + rekam + snapshot lawan stream dummy
    ../tests/test_raw.py        # agen RAW: start/stop → transfer+sha256 → hapus
    ../tests/test_console.py    # konsol penuh offscreen + MAVLink palsu + agen RAW

Prerequisite for test_raw/test_console: the RAW agent running on :8791, e.g.

    /usr/bin/python3 ../onboard/raw_agent.py --test --dir /tmp/dv_raw \
        --chunk-mb 4 --port 8791 --width 640 --height 360 --fps 25

## Key technical recipes (do not change without re-testing)

- **RTP relay**: `tee → queue → valve(drop) → udpsink(127.0.0.1:P+100, sync=false, async=false)`.
  `async=false` is REQUIRED — without it the pipeline stalls in PAUSED while the valve is closed.
- **Recorder** = a `gst-launch-1.0 -q -e` subprocess with **TOKEN** argv (not a single
  quoted string — gst-launch parses per argument). Stop = close the valve, then
  SIGINT the subprocess (`-e` → EOS → final file).
- **Display without the GstApp typelib**: frames are grabbed via a pad probe on `fakesink`.
- Depay default = AVC format; a capsfilter is needed for byte-stream; matroskamux only
  accepts AVC/avc3 → that is why h264parse (the -bad plugin) is the main path.
