#!/usr/bin/env bash
# Jalankan ROV mission console — ROV MODE (mode ROV).
#./run_console_real.sh [--mavlink udpin:0.0.0.0:14550] [--outdir ~/DeepVertex-ROV-Captures]
set +u
DIR="$(cd "$(dirname "$0")" && pwd)"
# Plugin GStreamer tambahan yang dipaket bersama (mis. h264parse untuk rekaman .mkv):
# taruh di plugins/gstreamer-1.0 + plugins/lib (hasil ekstraksi paket plugins-bad).
if [ -d "$DIR/plugins/gstreamer-1.0" ]; then
  export GST_PLUGIN_PATH="$DIR/plugins/gstreamer-1.0${GST_PLUGIN_PATH:+:$GST_PLUGIN_PATH}"
  export LD_LIBRARY_PATH="$DIR/plugins/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
# libxcb-cursor0 (wajib Qt6 xcb) — hasil ekstraksi tanpa sudo, bila ada
if [ -d "$DIR/tools/xcb" ]; then
  export LD_LIBRARY_PATH="$DIR/tools/xcb${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
cd "$DIR" || exit 1
exec .venv/bin/python rov_console_real.py "$@"
