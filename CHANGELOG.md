# Changelog

All notable changes to the DeepVertex Marine Technology Console (ROV mode).
This project follows [Keep a Changelog](https://keepachangelog.com/) and SemVer for
the application package (`MAJOR.MINOR.PATCH` — autonomy (next) features will ship as 2.x).

## [1.0.14] — 2026-10-15

### Changed
- Clearer Connection-dialog help for the robot SSH fields (owner question):
  the hint now says exactly WHAT to fill — the same user + password used
  for a terminal `ssh` to the robot (BlueOS default user `pi`) — and the
  “fill first” message also names the fields and focuses the empty one.

## [1.0.13] — 2026-10-15

### Added
- **Automatic storage cleanup for update files (owner policy):** the
  downloaded installer is now saved with its version in the name
  (`setup-<version>.exe`) and, at every start, the console deletes
  installers older than the running version — downloads can no longer
  pile up and eat storage. (After a successful install the apply script
  already deletes the payload.) Test [52].
- Supersedes the unannounced v1.0.12 — same post-download stall fixes
  (Content-Length cap, complete-file tolerance, auto-continue notice,
  Help ▸ Finish pending update), plus this cleanup.

## [1.0.12] — 2026-10-15

### Fixed
- **Update could stall right after the download** (owner report:
  "downloaded but still not applied"). Three fixes:
  * the reader now stops exactly at Content-Length — no more waiting for
    EOF on a keep-alive connection (a "ghost timeout" that threw away an
    already-complete download);
  * if the file on disk is complete (size == expected), the flow proceeds
    to apply even after a tail timeout or a Cancel pressed after the last
    byte;
  * the final notice auto-continues after 12 s — applying no longer
    depends on clicking anything.

### Added
- **Recovery path for pending installers** (the missing "apply"
  affordance): when a downloaded installer sits unused, the console shows
  a banner at startup and Help gains **"Finish pending update (run
  installer)…"** which launches it directly. Test [51].

## [1.0.11] — 2026-10-15

### Added
- **One-click RAW agent start from the Connection dialog** (owner request:
  "pencet tes connection aja langsung connect"): fill the robot SSH
  user/password (BlueOS default: `pi`) and the agent path; pressing
  **Test now** — if the agent is not answering, the console SSHes to the
  robot, starts it (nohup) and re-tests automatically. A manual **Start RAW
  agent on the robot** button is included as well.
- **Robot-side auto-start kit**: `install/robot/install_agent_service.sh` —
  run once on the robot to install the RAW agent as a systemd service
  (auto-start at every boot, auto-restart on failure; service name
  `dv-raw-agent`, log at `/tmp/dv-raw-agent.log`).
- New `robotops.py` (paramiko-based SSH helpers; degrades gracefully when
  paramiko is missing); `paramiko` bundled by both installers; tests [50].

### Changed
- `koneksi.json` gains `ssh_user` / `ssh_pass` / `agent_path` (stored in
  the local user data folder).

## [1.0.10] — 2026-10-15

### Fixed
- **Camera panels overflowed/clipped at large window sizes** (owner
  screenshot at 1920×986): the fixed-height tile forced the front video
  past its panel — title row squeezed, bottom edge cut. The tile now caps
  at its aspect height but SHRINKS smoothly when the column is tight:
  title + bottom border always visible, image stays centered. Verified by
  geometry measurements at the owner's exact window size.
- **Save & Restart could relaunch into a broken extraction** (traceback:
  `numpy._core` missing under `_MEI…`): Windows recycles PIDs, and the
  relaunch (150 ms after close) extracted into the SAME `_MEI<pid>` folder
  the old bootloader was still deleting. The relaunch is now delayed ~4 s
  via a detached command line (+ PyInstaller-env hygiene), so cleanup
  always finishes first. Verified with the recording probe.

## [1.0.9] — 2026-10-15

### Fixed
- **"Update hits 100% and then nothing happens" — root cause found, fixed,
  and verified end-to-end.** The Windows apply-script generator formatted
  the .bat text with Python `%`-formatting over batch tokens like `%~dp0` —
  which raises `ValueError: unsupported format character '~'` BEFORE the
  script is ever written. The download completed, the install never started
  (owner report). The script is now built by pure concatenation (regression
  probe included), the installer is invoked directly (no `start /wait`
  wrapper), the payload cleanup retries with a cap, and every step + the
  installer exit code is logged to `update\apply_log.txt`. Verified on
  Windows with the real Setup: install exit code 0, files replaced, app
  relaunched, cleanup done.
- **Updates are no longer silent at the end:** the final dialog states the
  size, what happens next, and where the manual fallback installer lives;
  on the next start the console shows a banner — "Update complete — now
  running vX" or "Update to vX did NOT apply (see apply_log.txt)".

### Changed
- The apply spawn uses explicit DEVNULL std handles (windowed PyInstaller
  builds can fail to spawn children with invalid inherited handles).

## [1.0.8] — 2026-10-15

### Fixed
- **"Save & Restart" left two consoles running.** The connection dialog's
  modal loop kept the old instance alive while the new one started (both
  fought over the same session/ports — reported by the owner). The dialog
  now closes first; the old instance exits cleanly before the new one takes
  over (verified: single process, correct connection args, clean exit).
- **Connection test rebuilt to match reality** (owner report "can't connect
  although SSH to BlueOS is open"): rows are now SSH/BlueOS (TCP 22), BlueOS
  web (HTTP 80/8080), MAVLink (informational — the console *listens* on
  14550 and the dialog now shows the exact laptop IP BlueOS must send to),
  and RAW agent (with the command to start it on the robot). The old "video
  port" row wrongly probed a TCP server that never exists — video is UDP
  sent TO the laptop.
- Language sweep round 2 — "CHECKLIST PRE-DIVE (prosedur…)" → "(Blue
  Robotics procedure)"; third automated pass over all modules is clean.

### Changed
- Offline MAVLink message no longer says "waiting for SITL" on a real robot:
  now "waiting for a MAVLink source (robot or SITL)".

## [1.0.7] — 2026-10-15

### Fixed
- **Update download progress now actually moves.** Downloads run in a
  background thread inside a proper progress dialog with a live MB counter
  and a working Cancel button; the freeze that left the bar stuck at 0%
  (and never even wrote the file) is gone.
- `--preview-update` flag: shows the update dialog with demo data (small real
  download) so the design and progress can be reviewed without waiting for a
  release; nothing is installed in preview mode.

### Changed
- **Update popup redesigned** — header with the app icon, "A new version is
  available", version chips (current → new, accent), readable release notes,
  and a footer with Open release page / Not now / Update now.
- **The in-app UI is now fully English** — comprehensive audit across menus,
  dialogs, tooltips, notifications, logs, checklist, connection dialog and
  updater. `--nama-sesi` keeps working and gains the alias `--session-name`.

## [1.0.6] — 2026-10-15

### Added
- **Connection settings dialog** (menu **Connection**, both platforms):
  BlueOS preset (192.168.2.x) / SITL preset / fully custom endpoints — robot
  host, MAVLink endpoint, video ports and RAW agent URL. Shows your network
  interfaces, generates the exact per-OS ethernet setup commands (DHCP or
  static IP; `nmcli`/`ip` on Linux, `netsh` on Windows with a one-click UAC
  "Apply"), runs a live connection test (SSH/BlueOS, video port, RAW agent)
  and offers **Save & Restart** to relaunch the console on the new endpoints.
  Saved to the user data folder (`koneksi.json`) and used as defaults at
  every start (CLI flags still win).
- **Windows gamepad support** — DualShock 4 **and** Xbox pads (hotplug):
  the console reads pads through SDL (pygame) and translates them to the
  same internal mapping as the Linux evdev path, so mapping presets, deadman
  rules and the PAD MAPPING page work identically.
- Right-rail order in the Pilot Focus / Capture presets reworked (owner
  request): stereo L/R on top, capture below them, teleop just under
  notifications and above Autonomous.
- New regression checks: [48] connection presets/args/ethernet commands /
  persistence; [49] SDL→evdev gamepad translation with a fake pad.

### Changed
- **Default theme is now Ink · black** (was Dark · abyss); still switchable
  live in View ▸ Appearance.
- Windows auto-update now applies through the official Setup silently
  (portable-EXE swap kept as fallback for older updaters); new releases list
  the Setup + the Linux package only.

## [1.0.5] — 2026-10-15

### Added
- **Update checker + one-click auto-update** (both platforms): at every start
  the console checks the GitHub Releases API (online; offline = silent) and —
  when a newer release exists — shows a themed popup with the release notes,
  a link to the release page and an optional **Update now**. Updating
  downloads the right artifact per platform, then swaps the EXE (Windows) or
  the source tree keeping `.venv` and user data (Linux), relaunching itself.
  Opt-out: `--no-update` / `DV_NO_UPDATE=1`.
- **Windows installer** (`DeepVertexMarineConsole-Setup-<ver>.exe`, Inno
  Setup): mandatory “I accept” End-User Terms page, install folder page with
  a sensible default (user-changeable), per-user install by default, Start
  Menu entry + optional desktop shortcut + uninstaller.
- **Linux installer rework**: explicit ToS acceptance (type `agree`), asks
  for the install folder (default `~/DeepVertexMarineConsole`), copies the
  sources there, then builds the venv; `--yes` / `--dir=` / `--dry-run` for
  automation; CI runs a dry-run check of the whole flow.
- **Tidy per-OS data layout** (`paths.py`, docs/data-and-paths.md): settings
  live in `~/.config/deepvertex-console` (Linux) /
  `%LOCALAPPDATA%\DeepVertexMarineConsole` (Windows); a rolling
  `logs/console.log` mirrors the notifications panel; legacy `<app>/saved`
  files migrate automatically once; captures stay in
  `~/DeepVertex-ROV-Captures` (never touched by updates).
- New regression checks: [46] dragging a panel ABOVE a neighbour inside a
  rail + ordering off-by-one regression; [47] update-checker version
  parsing / asset selection / offline behaviour + data-folder resolution.

### Fixed
- **Organize drag: panels can move UP again** (owner report: “only
  downwards”). “Above/below” drop bands now work over rail panels too
  (top/bottom bands of the hovered panel); the middle band of a rail panel
  still appends to that rail; moving an item downwards inserts it exactly
  below the target instead of landing at the end of the list.

### Changed
- `session.json` records `versi` from a single `__version__` constant.
- README: installer downloads, update-checker and data-location sections;
  `docs/platform-parity.md` documents the Windows/Linux 1:1 audit.

## [1.0.4] — 2026-10-15

### Fixed
- **Display-scale opt-out now actually effective on Windows.** v1.0.3 pinned
  `QT_SCREEN_SCALE_FACTORS=1`, which Qt does not honour as an override of the
  Windows platform DPI (it is a per-screen factor list applied by screen name).
  The supported opt-out is `QT_ENABLE_HIGHDPI_SCALING=0` (+ `QT_FONT_DPI=96`);
  the console now pins those before the QApplication is created — widgets
  render 1:1 in physical pixels regardless of the Windows 125/150 % display
  scale, while staying Per-Monitor-V2 aware (no blurry OS stretching).
- The startup notification log now reports the detected device pixel ratio and
  work area (`tampilan: dpr=…`) for field diagnostics.

## [1.0.3] — 2026-10-15

### Fixed
- **The app no longer inherits the OS display scale** (owner-directed fix):
  on Windows with 125–150 % display scaling the logical canvas shrank, the
  centre column got squeezed between the rails and needed horizontal
  scrolling. The console now pins `QT_SCREEN_SCALE_FACTORS=1` before the
  QApplication is created — the UI renders 1:1 in physical pixels and is
  scaled ONLY by the app's own text scale (`--teks` 1.0/1.2/1.4, View ▸
  Appearance), matching the documentation pixel-for-pixel. Window fitting
  stays as the safety net for genuinely small screens.
- **NOTIFICATIONS log styled explicitly** (new `QPlainTextEdit` QSS rule):
  the log widget had no stylesheet rule at all — it looked dark on Linux only
  because the desktop (GNOME) palette leaked into the app, and rendered
  white-on-white (unreadable) on Windows.

## [1.0.2] — 2026-10-15

### Fixed
- **Window now always fits the screen** (owner report from the Windows laptop
  audit): on Windows the console opened larger than the display (right side and
  bottom clipped, status bar off-screen) because system-font metrics + display
  scaling inflated the layout minimum. The window is now fitted to the
  available work area at startup (centred when it fits, clamped to the full
  work area otherwise, minimum relaxed so the resize is honoured; rails stay
  scrollable). New headless check [45] covers the geometry maths for
  1920×1040, 1536×816, 1280×720 and 2560×1440.
- **Fonts bundled into the Windows .EXE** (`--add-data gui_t1/fonts;fonts`):
  the packaged app previously fell back to the system font, which changed the
  layout metrics and the on-brand type. The bundled DeepVertex Sans family now
  loads on Windows exactly like on Linux.
- **QSS icon paths use forward slashes**: the runtime chevron PNGs (spin-box /
  combo arrows) were referenced with Windows backslash paths in `url()` — they
  loaded inconsistently; URLs are now emitted POSIX-style (works on both OSes).
- **Session-name dialog is centred on the screen** (previously it appeared in
  the top-left corner on Windows as a parentless dialog).

## [1.0.1] — 2026-10-15

### Added
- **Chrome-style organize windows**: press & drag any panel header — cyan snap
  suggestions appear for **left rail / right rail / above / below** (Chrome
  split feel incl. reordering inside a rail); dropping a panel OUTSIDE the
  console pops it out as a free-floating window (drag it back to re-dock);
  ESC cancels a drag; **Custom** segment in the LAYOUT bar + View ▸
  *Organize windows* (Apply / Save as Custom / Forget) — the Custom layout is
  stored in `gui_t1/saved/layout_custom.json` and becomes the startup layout.
- Snap-engine regression tests [36]–[40] (rail snap + anti-stuck, mid
  above/below insert, float round-trip, custom persistence, ESC + "asal"
  return); suite now 40 checks, all headless (incl. raw-agent tests).
- README assets re-taken from the REAL desktop (X11 window capture), so the
  images match the actual on-screen rendering pixel-for-pixel.
- **Distribution builds** (owner request 15 Oct): Linux **.tar.gz** source
  package (git archive, extract → `install/install_topside.sh`) and a Windows
  **one-file .EXE** built by CI on windows-latest with PyInstaller
  (`windows_entry.py` shim, icon bundled) — attached automatically to the
  `v1.0.1` release. Windows scope: telemetry/recording/capture work; live
  preview & gamepad stay Linux-side (PyGObject/evdev), video.py degrades
  gracefully without `gi`. App version bumped to 1.0.1.

## [1.0.0] — 2026-10-14

First field-complete release of the ROV mode console, verified against the real
BlueROV2 over the Fathom-X tether.

### Added

- Three simultaneous camera streams (front + stereo L/R) with 16:9 tiles, per-tile
  health and recording state.
- Full MAVLink 2 telemetry: attitude/compass dials, depth/temperature, battery
  V/A/%, QGC-style sub instruments, per-session `mavlink.tlog`.
- Gamepad teleop (DualShock 4 via evdev): dead-man enforced, MAX SPEED caps,
  depth-hold, camera tilt/lights with % readback, keep-alive RC overrides.
- **Per-camera COMPRESSED | RAW recording.** RAW runs through the onboard agent
  (BlueOS extension) and is pulled to the topside with sha256 verification.
- **GAMEPAD MAPPING page**: painted DS4 diagram, live button/stick feedback,
  one-click remap, named presets, and full output blocking while the page is open.
- Pad actions: □ toggles recording, ○ takes a photo — same code path as the GUI.
- **Storage guard**: live time-to-full estimates (topside EMA rate + Pi RAW rate),
  warn/full margins, red alert and hard REC/SNAP block below the safe limit.
- **Per-sensor pre-flight**: GYRO/ACCEL/MAG(+compass count)/BARO/BATTERY/MOTOR/RC
  chips from SYS_STATUS present+healthy bitmasks; failing sensors are named.
- Beginner SETUP GUIDE (7 steps), one-click failsafe defaults for BlueROV2 +
  Tattu 4S 10000 mAh, editable water-temperature alerts with beep.
- Session naming (startup dialog, `--nama-sesi`, live rename), file layout
  `compressed/` vs `raw/`, in-app `saved/` for settings and parameter dumps.
- Default 120% text scale, ink/ivory/abyss themes, English-only UI, DS4-shaped
  mapping diagram, real chevron assets for spin/combo controls.
- Test suite (30 checks) + geometry/scroll/pad probes, all hardware-free; CI.

### Fixed (highlights from the field-testing gauntlet)

- Stuck windows when switching layouts quickly; PAD MAPPING crash on open.
- Mode/ARM/depth flicker from BlueOS companion heartbeat; MAG false-FAULT before
  parameter download; spin/combo arrows rendering as blank squares.
- Camera tiles aspect drift; storage panel clipping in the right rail.

## [0.x] — 2026-09 … 2026-10 (pre-release, summary)

Bootstrap: PyQt5 → PySide6 migration, video pipeline (GStreamer), MAVLink layer,
calibration/motor-test/failsafe tooling, OSM map, session folders, RAW agent v1
(stereo), themes and the layout system. Kept for history in `progress/`.
