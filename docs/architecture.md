# Architecture

## Process overview

```
┌─────────────────────────── Topside (Ubuntu 24.04) ────────────────────────────┐
│  rov_console_real.py  (PySide6, single process, Qt main thread + helpers)     │
│   ├─ maviface.MavBridge     MAVLink 2 I/O thread, tlog, param/cmd queues      │
│   ├─ gamepad.Gamepad        evdev reader thread → 20 Hz send + sig_live       │
│   ├─ video.VideoPlayer ×3   GStreamer udpsrc → decode → QImage (pad probe)    │
│   ├─ rawcap.RawPanel        HTTP client for the onboard agent                 │
│   └─ recorder               gst-launch subprocess per recording               │
└──────────────────────────────────────────────────────────────────────────────┘
        ▲ MAVLink 14550            ▲ video 5600-5602            ▲ HTTP 8765
        │                          │                           │
┌───────┴──────────────────────────┴───────────────────────────┴───────────────┐
│  BlueROV2 / BlueOS (stock)         +  raw_agent.py (single-file extension)   │
└───────────────────────────────────────────────────────────────────────────────┘
```

## Modules (gui_t1/)

| Module | Responsibility |
|---|---|
| `rov_console_real.py` | Window, header/banner/alerts, presets (layouts), panels, session folder, storage guard, pad actions |
| `maviface.py` | Connection + reconnect watchdog, heartbeat filtering (component 1, autopilot ≠ INVALID), SYS_STATUS bitmasks, param cache/queue, command ACK tracking, tlog |
| `video.py` | Per-stream GStreamer pipeline (`rtpjitterbuffer → tee` → decode branch + relay branch), frame→QImage via pad probe, record/stop via child `gst-launch`, status signals |
| `gamepad.py` | evdev axis/button model, dead-man, RC-override keep-alive for tilt/lights, edge actions (arm/mode/center/record/photo), output block, named presets |
| `vehicleops.py` | Calibration dialogs (gyro/accel 6-pos/compass/level/baro), motor test + MOTOR_DETECT, failsafe editor + recommended defaults, parameter browser, pre-flight panel incl. SENSORS + checklist |
| `rawcap.py` | RAW agent client + panel (start/stop per camera incl. front, transfer with sha256 verify, delete verified, Pi free-space estimate) |
| `peta_panel.py`, `peta_misi.py`, `peta_geo.py`, `peta_penyedia.py` | OSM tile map, offline cache, providers, anchor, dead-reckoning plot, NED view |
| `theme.py` | Palettes (ink/ivory/abyss), global text scale (QSS px rewrite), runtime-painted QSS assets (spin/combo chevrons) |
| `widgets.py`, `ui_common.py`, `padmap.py` | Custom widgets (Panel, Chip, Horizon, Kompas, tiles, bars), shared UI helpers, the GAMEPAD MAPPING page |
| `avatar`… none | no dead code — legacy simulator modules were moved out of the app |

## Key design decisions (and why)

- **PySide6 (LGPLv3)**, not PyQt5 (GPLv3) — the console ships as part of a commercial
  delivery; LGPL + dynamic linking keeps compliance simple (see THIRD_PARTY_NOTICES).
- **Robot stays stock BlueOS** — the only onboard addition is the single-file RAW
  agent (deployed as an extension), because uncompressed stereo (≈138 MB/s per camera)
  can never cross a 100 m tether.
- **Subprocess recorder** — recording via `gst-launch` child processes proved far more
  robust than in-pipeline muxing across plugin sets.
- **Heartbeat filtering** — BlueOS emits a second heartbeat (its own companion); only
  component 1 with a real autopilot is accepted, killing mode/arm flicker.
- **Global text scale** — one knob rewrites QSS px + custom paint metrics, so 140%
  text stays proportioned on every screen.
- **Everything testable without hardware** — fake autopilot (pymavlink), GStreamer
  test sources, agent `--test` mode, and geometry probes that assert "no clipped text,
  no clipped panels, scroll reachable" across all layouts.

## Onboard agent protocol (raw_agent.py)

| Endpoint | Method | Purpose |
|---|---|---|
| `/capture/start` | POST | `{cam: left|right|front|both, chunk_mb}` → starts `gst-launch` writing `chunk_%05d.yuv` |
| `/capture/stop` | POST | stops capture for a camera (RAW files need no EOS) |
| `/status` | GET | active captures, disk free, min-free margin, dir, test flag |
| `/files` | GET | chunk list with sizes |
| `/download/<rel>` | GET | stream a chunk |
| `/sha256/<rel>` | GET | hash for verification |
| `/delete` | POST | delete verified chunks |

Chunk size default 1024 MB; front camera RAW is capped (≤640×480) because of USB2.
The agent also refuses to start when free space is below `--min-free-mb + 200 MB`.
