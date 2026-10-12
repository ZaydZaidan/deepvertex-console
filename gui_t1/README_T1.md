# ROV Mission Console — STAGE 1 (ROV mode) — working notes

Console for the real ROV (BlueROV2 + BlueOS). Design basis & widgets = the current
simulator GUI (ROV Mission Console, including the Sep 28, 2026 redesign); the binding is
now **PySide6**.

Status (Oct 12, 2026): **console v2.2 + ArduSub/QGC tech-spec fixes** — all tests PASS
without hardware (`test_console` 17/17). T1 features: 3 camera streams (front + stereo
L/R), MAVLink 2 telemetry (attitude/compass dials, depth, temperature, battery, QGC-style
INSTRUMENTS, SYS_STATUS pre-arm bit), telemetry tlog per session, teleop with dead-man,
vehicle ops (arm/mode/SURFACE, calibration, motor test, failsafe, parameter browser,
pre-dive checklist + ARM soft-gate), OSM map + NED plot (dead-reckoning estimate until
Stage 2/VIO), compressed capture + snapshot (topside), and **onboard RAW capture**
(option B — via the `../onboard/raw_agent.py` agent). AUTONOMOUS and VIO = *coming soon*
(Stage 2, AUV mode).

## Run

    ./run_console_real.sh [--mavlink udpin:0.0.0.0:14550] [--outdir ~/DeepVertex-ROV-Captures]

Options: `--front-port/--left-port/--right-port` (default 5600/5601/5602 — UDP stream
ports from the BlueOS camera manager), `--raw-agent http://127.0.0.1:8765`,
`--preset pilot-focus|mapping|setup|capture`, `--tlog auto|off|PATH` (per-session
MAVLink telemetry log; default `auto` → `<session>/mavlink.tlog`).

## Display demo (no hardware)

    bash demo_local.sh                 # 3 dummy streams + fake MAVLink + RAW agent → console
    bash demo_local.sh --shot cek.png  # + automatic screenshot (--shot-after seconds)

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
- pymavlink runs in **MAVLink 2** mode (`MAVLINK20=1` is set before import): required for
  18-channel RC override (lights ch9+) and modern message extensions.

## Tests (no hardware)

    ../tests/test_video.py      # display + record + snapshot against a dummy stream
    ../tests/test_raw.py        # RAW agent: start/stop → transfer+sha256 → delete
    ../tests/test_console.py    # full offscreen console + fake MAVLink + RAW agent (17/17)
    ../tests/probe_layout.py    # real-DPI layout probe (run on a real display; 0 truncation)
    ../tests/debug_misc*.py     # diagnostics (tlog / RC-override investigations)

Prerequisite for test_raw/test_console: the RAW agent running on :8791, e.g.

    .venv/bin/python ../onboard/raw_agent.py --test --port 8791 \
        --dir /tmp/dv_raw --chunk-mb 4 --width 640 --height 360 --fps 25

(The agent's default port is 8765; tests expect `--port 8791`.)

## Key technical recipes (do not change without re-testing)

- **RTP relay**: `tee → queue → valve(drop) → udpsink(127.0.0.1:P+100, sync=false, async=false)`.
  `async=false` is REQUIRED — without it the pipeline stalls in PAUSED while the valve is closed.
- **Recorder** = a `gst-launch-1.0 -q -e` subprocess with **TOKEN** argv (not a single
  quoted string — gst-launch parses per argument). Stop = close the valve, then
  SIGINT the subprocess (`-e` → EOS → final file).
- **Display without the GstApp typelib**: frames are grabbed via a pad probe on `fakesink`.
- Depay default = AVC format; a capsfilter is needed for byte-stream; matroskamux only
  accepts AVC/avc3 → that is why h264parse (the -bad plugin) is the main path.
- **RC override keep-alive**: ArduSub's RC override times out (~3 s) — the console
  re-sends tilt (ch8) / lights (ch9) every 1.5 s while no gamepad is active.
- **Motor test** (ArduSub): `p6=2` (ORDER_BOARD) is mandatory, `p4` timeout is ignored —
  the dialog re-sends every 0.4 s (500 ms watchdog) and requires the ROV to be ARMED.
- **Compass calibration**: CANCEL → START (autosave) → on success with `autosaved=0`,
  send `DO_ACCEPT_MAG_CAL`; `MAG_CAL_PROGRESS/REPORT` ride the EXTRA3 stream and are
  explicitly subscribed.
