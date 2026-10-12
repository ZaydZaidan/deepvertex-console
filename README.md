# work/ — STAGE 1 workspace (Project Coral Mapping UAV)

Working hub for Step B (all without hardware first). The original sources in
`~/ros2_ws/...` are NOT touched; all T1 experiments & code live here.

GitHub repo (private): `DeepVertex-Underwater-Drone-GUI-Installer` — versioned T1 code lives here.
This repo MUST stay private (closed-source); the distribution artifacts for the client (binary-only +
license) are prepared separately during packaging; `-SBC-Installer` is parked.
How to push: rsync `work/` → clone of `repos/DeepVertex-Underwater-Drone-GUI-Installer` → commit/push.

- `gui_t1/` — **the real T1 console** (working copy of the sim GUI → PySide6 migration DONE
  + new modules: `video.py`, `rawcap.py`, `rov_console_real.py`).
  How to run & key recipes: `gui_t1/README_T1.md`.
- `onboard/` — **raw_agent.py**: onboard RAW recorder (production on Pi/BlueOS;
  local test: `--test`). Its HTTP protocol is used by `gui_t1/rawcap.py`.
- `tests/` — hardware-free tests:
  - `test_video.py` (display/record/snapshot vs dummy stream),
  - `test_raw.py` (agent + transfer/verify/delete),
  - `test_console.py` (full offscreen console + `fake_vehicle.py` fake MAVLink).
- `migrate_pyqt5_to_pyside6.py` — binding migration script (re-runnable, 58 replacements).

Local dependency note (dev): the `h264parse` plugin is extracted without sudo to
`~/.hermes/cache/scratch/badpkgs/` (from the `gstreamer1.0-plugins-bad` package).
For production: install that package, or bundle the plugin in `gui_t1/plugins/`.
