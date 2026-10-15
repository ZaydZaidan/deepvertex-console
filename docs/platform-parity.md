# Platform parity — Windows (.EXE) vs Linux (source)

Version: v1.0.15 · 2026-10-15

Audit scope: the ROV-mode console as shipped in this repository. The console
is the same application, same layout engine and the same automated test suite
on both platforms; the differences left are OS-level I/O with no
cross-platform equivalent yet. Verified against the real Windows laptop
(Win10 19045, 1920×1080 at 150 % display scale), the field Linux laptop
(Ubuntu 24.04) and the headless suite (47 checks + video + RAW suites).

## Feature matrix

| Feature | Linux | Windows | Notes |
|---|---|---|---|
| MAVLink telemetry (all panels) | yes | yes | `udpin:0.0.0.0:14550` on both |
| Mission map + dead-reckoning trail | yes | yes | OSM tiles (internet) |
| Layout presets, Custom, organize drag/snap/float | yes | yes | tests [36]–[40], [46] |
| Window fit + app-owned UI scale | yes | yes | `QT_ENABLE_HIGHDPI_SCALING=0`; startup log line `tampilan: dpr=…` |
| Capture: REC compressed (.mkv/.h264) + SNAP | yes | yes * | * Windows requires the GStreamer runtime (README §B.3) |
| RAW capture (onboard agent, transfer, sha256 verify) | yes | yes | agent lives on the Pi; the laptop-side code is shared (tethered Windows test pending) |
| PARAMETER browser + save/load + failsafe profiles | yes | yes | data folder = per-OS user dir, see docs/data-and-paths.md |
| Calibration dialogs (gyro/accel/compass/level/baro/motor) | yes | yes | tests [41]–[42] |
| Storage guard + LiPo estimate + LiPo guard | yes | yes | tests [29], [43]–[44] |
| Session folders, `mavlink.tlog`, `preflight.json` | yes | yes | identical layout on both |
| Update checker + one-click auto-update | yes | yes | GitHub Releases API; optional per user; progress + Cancel in a background thread (v1.0.7) |
| In-app language | English | English | full audit end-to-end in v1.0.7 |
| Connection settings (BlueOS + custom network) | yes | yes | menu **Connection** — BlueOS/SITL/custom preset, robot host + MAVLink + ports + RAW URL, per-OS ethernet commands (nmcli/netsh, DHCP or static), connection test lights; apply = save + restart |
| Installer | tar.gz + `install_topside.sh` (ToS gate + folder choice) | Setup .exe (Inno: license page + folder page, per-user default) | |
| Gamepad teleop (DualShock 4 / Xbox) | yes | yes | Linux: evdev · Windows: SDL via pygame — DS4 **and** Xbox pads, hotplug re-detect, same mapping/preset engine |
| Live video PREVIEW (front + stereo) | yes | **no** | Linux decodes in-process (PyGObject); the EXE can *record* via `gst-launch` but cannot draw frames yet |

## Design parity

- **Pixel-for-pixel**: both versions render at the application's own scale
  (1:1 physical pixels; the OS display scale is deliberately ignored), so the
  documentation screenshots match both versions. At 1920×1080 the console is
  the 1620×1000 design window on both platforms.
- **Fonts**: DeepVertex Sans is bundled inside the .EXE — identical type on
  both platforms.
- Historical divergences fixed and covered by regression tests: window larger
  than the screen (v1.0.2), unthemed notifications log (v1.0.3), OS display
  scale shrinking the canvas (v1.0.4), drag-only-downwards (v1.0.5).

## Known platform limits (deliberate)

- Live preview on Windows: recording works with the GStreamer runtime;
  preview needs a native decode path (future work).
