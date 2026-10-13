# Files, folders and the storage guard

## One folder per dive

```
~/DeepVertex-ROV-Captures/
└── 20261014_103000_coral_survey_a/        ← <date>_<time>_<session-name>
    ├── compressed/                        ← everything from the live streams
    │   ├── front_103512.mkv               ←.mkv video (or.h264 fallback)
    │   ├── stereo_L_103512.mkv
    │   └── snap_stereo_L_103844.png       ← SNAP photos (.png)
    ├── raw/                               ← uncompressed chunks pulled from the Pi
    │   ├── left_20261014_103000/chunk_00000.yuv
    │   └── right_20261014_103000/chunk_00000.yuv
    ├── mavlink.tlog                       ← full MAVLink telemetry log
    ├── session.json                       ← start time, session name, endpoints
    └── session_end.json
```

- The **session name** is asked at startup (or set with `--nama-sesi "…"`), and can
  be changed mid-dive with **SESSION NAME…** in the CAPTURE panel — the whole folder
  renames, open recordings and the tlog keep working.
- **COMPRESSED vs RAW is chosen per camera** in the CAPTURE panel: COMPRESSED records
  video from the live BlueOS stream; RAW instructs the onboard agent to write
  *uncompressed* chunks on the Pi (full quality for photogrammetry).
- RAW chunks live on the Pi until you pull them: **TRANSFER ►** downloads the selected
  files into this session's `raw/` folder, verifies **sha256** and only then marks
  them safe to **DELETE ✓** from the Pi.

## The storage guard (why REC can refuse to start)

The console protects a dive from a full disk, on both sides:

| Where | Warn | Hard stop |
|---|---|---|
| Topside (`compressed/`) | < 5 GB free → amber alert | < 1.5 GB free → **REC & SNAP blocked** with a "transfer/clean up" message |
| Onboard Pi (`raw/`) | — | RAW start refused when the Pi is within 200 MB of the agent's `--min-free-mb` margin (agent also auto-stops capture) |

While recording, the CAPTURE panel shows a live **estimate** of remaining time
(`~34 min of recording left (@ 2 MB/min) · ≈ 23000 snaps`) measured from the actual
write rate — so you can decide before the dive how long each pass can be.

## Settings files — inside the app

Operator settings live in the app folder, not scattered around the system:

```
gui_t1/saved/
├── failsafe.json              ← FAILSAFE panel → SAVE FILE
├── params_<timestamp>.json    ← PARAMETERS panel → SAVE…  (full parameter dump)
└── README.txt
```

Both panels say `save/load → saved/ (inside the app folder)` and their file dialogs
open there by default. LOAD… from the parameter browser sends every value in the file
back to the vehicle as PARAM_SET — handy for restoring a tuned setup on another ROV.

## Gamepad presets

Per-laptop: `gui_t1/gamepad_presets.json` (active preset + all saved presets). Delete
it to fall back to factory defaults.
