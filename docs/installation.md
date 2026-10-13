# Installation

Two sides to install: the **topside** (the laptop that runs the console) and the
**robot** (the BlueROV2's companion computer, which only needs the RAW agent).

---

## 1. Topside (Ubuntu 24.04)

### One-shot installer (recommended)

```bash
cd deepvertex-console./install/install_topside.sh
```

The script is idempotent and safe to re-run. It:

1. creates `gui_t1/.venv` (system site packages) and installs **PySide6**;
2. checks the GStreamer elements the video pipeline needs
   (`rtph264depay`, `rtpjitterbuffer`, `avdec_h264`, `matroskamux`, `multifilesink`,
   `valve`, `tee`) and tells you the apt packages if any are missing;
3. if `h264parse` is absent (needed for `.mkv` recording), downloads
   `gstreamer1.0-plugins-bad` **without sudo** and bundles the plugin into
   `gui_t1/plugins/` (the launcher picks it up automatically).

### What the console needs at runtime

| Requirement | Why | Fallback if missing |
|---|---|---|
| PySide6 (in `gui_t1/.venv`) | The application itself | — |
| GStreamer base/good/libav | Video decode + recording | streams won't start |
| `h264parse` (plugins-bad) | Matroska `.mkv` recording | console falls back to raw `.h264` files |
| `libxcb-cursor0` | Qt6 xcb platform plugin | bundling workaround in `gui_t1/tools/xcb/` is picked up by the launcher automatically |
| `libevdev` access to the pad | Gamepad teleop | console runs without teleop (`--no-pad`) |

### Verify

```bash./gui_t1/run_console_real.sh --help          # CLI reference
cd gui_t1 &&.venv/bin/python../tests/test_console.py   # 30/30 PASS without hardware
```

## 2. Tether & network

The console expects the BlueOS companion at **192.168.2.2** over the Fathom-X tether.

1. Connect the tether's USB-Ethernet adapter; Ubuntu usually names it `enx…`.
2. Give it a static address in the BlueOS subnet:

```bash
nmcli con mod "Wired connection 1" ipv4.method manual \
      ipv4.addresses 192.168.2.1/24 ipv4.gateway "" ipv4.dns ""
nmcli con up "Wired connection 1"
```

3. Verify:

```bash
cd gui_t1 && .venv/bin/python ../tests/real_rov_linkcheck.py
# expects: ping ~5 ms, BlueOS web 200, MAVLink heartbeat stream
```

Ports: MAVLink `udpin:0.0.0.0:14550`; video streams `5600` (front), `5601`/`5602`
(stereo L/R) from the BlueOS camera-manager; RAW agent `http://192.168.2.2:8765`.

## 3. Robot side — RAW agent (single small component)

The ROV stays on **stock BlueOS**; the only custom onboard piece is the RAW agent,
deployed as a BlueOS extension (`onboard/extension/` — see its README). It is a
single-file HTTP service:

- `POST /capture/start {cam: left|right|front|both, chunk_mb}` — start recording
  uncompressed chunks to the Pi's disk;
- `GET /status`, `GET /files`, `GET /download/<rel>`, `GET /sha256/<rel>`,
  `POST /capture/stop`, `POST /delete`;
- disk guard: auto-stops capture below `--min-free-mb` (default 3000 MB);
- no dependencies beyond GStreamer; runs fine on the Pi 4 (2 GB).

Manual test (no hardware):

```bash
python3 onboard/raw_agent.py --test --port 8765
```

Production install inside BlueOS: extension manifest in `onboard/extension/`
(`docker-compose` + `metadata`), device video nodes mapped as `--device-left/right/front`.
