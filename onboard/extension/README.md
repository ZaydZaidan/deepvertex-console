# DeepVertex Raw Capture — BlueOS Extension (Step C scaffold)

Wraps `raw_agent.py` (the onboard RAW recorder) as a BlueOS extension, so the
installation on the Pi is tidy (can be stopped/removed without a trace).

> **Status: scaffold.** The metadata/manifest format will be verified against the
> official BlueOS "Extensions" documentation during Step C (once the Pi 4 is on hand),
> together with the camera device numbers & mounting path.

## Contents

| File | Function |
|---|---|
| `raw_agent.py` | recorder — **copy first**: `cp ../raw_agent.py .` (a single source of truth stays in `../`) |
| `Dockerfile` | minimal image: python3 + GStreamer (v4l2src, multifilesink) |
| `docker-compose.yml` | service + host network access + camera device + data volume |

## How to install on the Pi (Step C — first time)

1. Copy this FOLDER to BlueOS (File Browser to `/root/extensions/deepvertex-raw/`,
   or `scp` over the tether).
2. Make sure `raw_agent.py` has been copied into this folder (`cp ../raw_agent.py .`), then build & run from the BlueOS Terminal:
   `cd /root/extensions/deepvertex-raw && docker compose up -d --build`
   (or register it via the "+" button in Extensions Manager if the manifest format has
   been adapted to the installed BlueOS version).
3. Check: `curl http://127.0.0.1:8765/status` on the Pi.
4. From the laptop: `./run_console_real.sh --raw-agent http://192.168.2.2:8765`
   (192.168.2.2 = default BlueOS IP).

## What MUST be verified on the Pi (do not assume)

- Stereo device numbers: `ls /dev/video*` → update `--device-left/--device-right`
  (in docker-compose or CMD).
- Storage path: the `/data` volume → ensure enough room & fast (SD vs SSD).
- YUYV caps format & B0495 resolution: `v4l2-ctl --list-formats-ext` (detection test).
- v4l2src `io-mode` (dmabuf vs mmap) & CPU load with 2 cameras simultaneous.
- Auth token: set `--token` (do not leave it open on the network).
