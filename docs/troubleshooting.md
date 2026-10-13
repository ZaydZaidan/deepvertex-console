# Troubleshooting (field notes from the real ROV)

## The console won't start: "could not load the Qt platform plugin xcb"

Missing `libxcb-cursor0`. Either install it (`sudo apt install libxcb-cursor0`), or use
the bundled workaround: the launcher automatically adds `gui_t1/tools/xcb/` to
`LD_LIBRARY_PATH` if that folder exists (the installer populates it without sudo).

## Recording produces `.h264` instead of `.mkv`

`h264parse` is missing. Run `install/install_topside.sh` again — it bundles the plugin
from `gstreamer1.0-plugins-bad` into `gui_t1/plugins/` (no sudo needed). Recordings
made meanwhile are still valid; they are raw H.264 elementary streams.

## Mode/ARM/depth flickering

Fixed in v1.x: BlueOS also emits a companion heartbeat. The console accepts only
heartbeat **component 1 with a valid autopilot**. If you see flicker, you're on an old
build — update.

## MAG chip says FAULT but the heading moves

Means: the parameter list hasn't been downloaded yet on an old build (the chip used to
judge compass count too early). Current builds auto-download parameters once per
connection and never claim FAULT without data. If it still shows FAULT after ~10 s
connected, the autopilot's health bit is genuinely down — run the compass calibration
in the Setup layout.

## REC or SNAP refuses to start ("storage full")

The storage guard tripped (< 1.5 GB free topside). Clean up or transfer + delete RAW
chunks, or point `--outdir` at a bigger disk. The alert strip shows the same warning.
For RAW: transfer and delete chunks on the Pi to get under the agent's margin.

## RAW agent unreachable

- Is the extension running in BlueOS? `curl http://192.168.2.2:8765/status`
- Did you pass the right `--raw-agent`? (bench tests use `http://127.0.0.1:8791`
  with `raw_agent.py --test`).
- Token set? The client sends `X-Token` when configured.

## Camera tiles stay black

- BlueOS camera manager streams must be addressed to your laptop on ports
  5600/5601/5602 (UDP). Check with `gst-launch-1.0 udpsrc port=5600 ! ...` or just
  watch the tile's `Hz` counter.
- Firewall: allow UDP 5600-5602.

## The pad does nothing in the console

- `pad: ready` in TELEOP? If `none`, check `/dev/input/event*` permissions (the user
  must be in `input`? no — read access via udev is enough for DS4).
- Some pads enumerate several event nodes; the console picks the one with the DS4
  signature (Sony strings). Replug and watch the notification log.

## Mapping page: buttons don't reach the ROV

That's by design — see [gamepad.md](gamepad.md#safety-mapping-page-blocks-the-vehicle).
Close the page to restore control.

## After renaming the session, my tools still write to the old path

Open file handles follow the folder on Linux; only *new* files use the new name.
The status bar and panel always show the current folder.
