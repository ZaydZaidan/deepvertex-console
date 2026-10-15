# Where your data lives

Version v1.0.5. One page, no surprises — so you can back up, move and clean
things safely.

## Application data (settings, saved layouts, presets)

| Platform | Folder |
|---|---|
| Linux | `~/.config/deepvertex-console/` (respects `$XDG_CONFIG_HOME`) |
| Windows | `%LOCALAPPDATA%\DeepVertexMarineConsole\` |
| Override (any OS) | env `DV_DATA_DIR=/path` (used by tests/CI) |

Inside:

- `saved/` — `layout_custom.json` (your organize layout),
  `params_<time>.json` (parameter dumps), `failsafe.json`,
  `gamepad_presets.json`.
- `logs/console.log` — rolling file mirror of the notifications panel
  (rotates at ~2 MB to `console.log.1`).

Legacy note: files that older versions kept inside the application folder
(`gui_t1/saved/`) are migrated automatically on first start (copied, never
deleted).

## Captures (dives)

Default root: `~/DeepVertex-ROV-Captures/<YYYYMMDD_HHMMSS>_<session>/`

- `compressed/` — `front_*.mkv` videos + `snap_*.png` snapshots
- `raw/` — transferred RAW chunks (sha256-verified before delete-on-Pi)
- `mavlink.tlog`, `preflight.json`, `session.json`, `session_end.json`

Change the root with `--outdir`. Upgrades and the uninstaller never touch
this folder — captures are yours.

## What updates touch

The update checker only replaces application code (the .EXE on Windows, the
source tree on Linux, `.venv` preserved). Your `saved/`, `logs/` and captures
are preserved.

## Uninstall

- **Windows** — Apps & Features → DeepVertex Marine Console → Uninstall
  (application only; user data stays).
- **Linux** — delete the install folder (e.g. `~/DeepVertexMarineConsole`);
  user data stays.

## Quick map

```
~/.config/deepvertex-console/     (Windows: %LOCALAPPDATA%\DeepVertexMarineConsole\)
├── saved/        layout_custom.json · failsafe.json · params_*.json · gamepad_presets.json
└── logs/         console.log (+ console.log.1)

~/DeepVertex-ROV-Captures/        dive sessions (compressed/ · raw/ · session.json …)
```
