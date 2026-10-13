# Marine Technology — ROV Console (Stage 1 · ROV mode) — working notes

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

Or just type the launcher installed on this machine (shell function in `~/.bashrc`):

    deepvertexrov                              # ink theme, 120% text, RAW agent 192.168.2.2:8765
    deepvertexrov --tema ivory --teks 1.4      # extra arguments are forwarded
    deepvertexrov --raw-agent http://127.0.0.1:8791   # e.g. local test agent

Sessions: at startup the console asks for this session's name (dialog, prefilled
"dive"; Cancel = "dive"). You can also set it up-front with `--nama-sesi "Coral
Survey A"`, or rename the running session anytime from the CAPTURE panel
(SESSION NAME…). The folder under ~/DeepVertex-ROV-Captures/<date>_<time>_<name>/
moves together with its compressed/ + raw/ subfolders and the tlog. Automation
uses `--no-prompt`.

Options: `--front-port/--left-port/--right-port` (default 5600/5601/5602 — UDP stream
ports from the BlueOS camera manager), `--raw-agent http://127.0.0.1:8765`,
`--preset pilot-focus|mapping|setup|capture`, `--tlog auto|off|PATH` (per-session
MAVLink telemetry log; default `auto` → `<session>/mavlink.tlog`),
`--tema abyss|ink|ivory` (ink = neutral-black theme, same family as the sim),
`--teks 1.0|1.2|1.4` (global text scale — for boat/bright-sun readability; default 1.2).

**Theme & text size are switchable live**: menu View ▸ Appearance ▸ *Ink · black /
Abyss / Ivory*, and *Ukuran teks: Normal 100% / Besar 120% / Ekstra 140%*.

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


## What's new (13 Oct 2026)

- **English-only UI** across the console and all runtime modules (labels, buttons,
  tooltips, dialogs, notifications, status bar, CLI help).
- **GAMEPAD MAPPING page** (Vehicle menu ▸ Gamepad mapping…, or PAD MAPPING… in TELEOP):
  a painted controller diagram with game-style names (LB/RB, left stick, D-PAD …),
  LIVE interactivity (pressing real buttons/moving sticks lights up the picture),
  one-click CHANGE remap, and named presets saved to `gamepad_presets.json`
  (auto-loaded at startup). New actions **btn_record** (default □) and **btn_photo**
  (default ○) run the same code path as the GUI ● REC / SNAP buttons.
- **Beginner SETUP GUIDE** panel (steps 1-7), **one-click failsafe defaults** for
  BlueROV2 + Tattu 4S 10000 mAh (still editable), **water-temperature alerts**
  (defaults warn 38 °C / crit 45 °C, editable).
- **File flow**: session folders split `compressed/` vs `raw/`; CAPTURE rows have a
  per-camera COMPRESSED/RAW mode; RAW agent supports the front camera too
  (`--device-front`, ≤640×480); snapshots always land in `compressed/`.
- **Per-sensor PRE-FLIGHT**: a SENSORS row in PRE-FLIGHT CHECK with GYRO / ACCEL /
  MAG (compass count from COMPASS_DEV_ID*) / BARO / BATTERY / MOTOR / RC IN chips,
  driven by the SYS_STATUS present+healthy bitmasks — a broken or missing sensor is
  named directly ("FAULT" / "NOT FOUND"); the PRE-ARM chip tooltip now carries the
  latest vehicle status text.
- **Storage guard + time estimate**: while recording you see the estimated minutes
  left until the SAFE LIMIT (topside for compressed .mkv, the Pi for RAW), plus a
  snapshot-count estimate. Below the warn margin the ALERT strip turns amber; below
  the hard margin (default 1.5 GB topside) REC and SNAP are BLOCKED with a
  "transfer/clean up first" message — RAW start is blocked when the Pi hits its own
  auto-stop margin + 200 MB.
- **Session naming**: type the session name at startup (dialog, prefilled) or via
  `--nama-sesi`; rename anytime from CAPTURE ▸ SESSION NAME… (folder + subfolders move
  together); `--no-prompt` for automation.
- **Saved settings live inside the app**: `gui_t1/saved/` — failsafe profile
  (`failsafe.json`) and full parameter dumps (`params_<time>.json`); both panels show
  the folder in-line, file dialogs open there by default (see `saved/README.txt`).
- **Design pass**: larger paddings everywhere, explicit spin-box up/down arrow
  buttons, clear combo dropdown arrows, compact right-aligned CLOSE buttons, dialogs
  auto-fit the available screen, header subtitle removed (clean DeepVertex lockup),
  rails widened and measured (no horizontal clipping in any preset).
- Legacy simulator modules moved out of the app folder (see `../legacy_sim/` in the
  work tree; they are not part of the delivery).
