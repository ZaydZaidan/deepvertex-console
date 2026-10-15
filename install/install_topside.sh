#!/usr/bin/env bash
# DeepVertex Marine Console — topside installer (Ubuntu/Debian-based).
#
# Flow:
#   1) show the End-User Terms and require EXPLICIT acceptance ("agree"),
#   2) ask WHERE to install (a default folder is offered; ~ expands),
#   3) copy the sources there and build the runtime (.venv, GStreamer
#      checks, libxcb-cursor fallback).
#
# Non-interactive (CI / automation):
#   install/install_topside.sh --yes --dir="$HOME/DeepVertexMarineConsole"
#   install/install_topside.sh --dry-run          # plan only, changes nothing
#
# Re-running upgrades/repairs an existing installation in place (idempotent).
set -uo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"          # extracted source tree root
YES=0
DRY=0
TUJUAN=""
for arg in "$@"; do
  case "$arg" in
    --yes|-y)  YES=1 ;;
    --dry-run) DRY=1 ;;
    --dir=*)   TUJUAN="${arg#--dir=}" ;;
    -h|--help)
      echo "usage: install/install_topside.sh [--yes] [--dry-run] [--dir=PATH]"
      exit 0 ;;
    *) echo "unknown option: $arg"; exit 2 ;;
  esac
done

echo "============================================================"
echo " DeepVertex Marine Technology Console — top-side installer"
echo "============================================================"

# ----------------------------------------------------------- 1) terms gate
echo
echo "End-User Terms: $SRC/docs/END-USER-TERMS.txt"
echo "(PolyForm Strict 1.0.0 — free to use, no redistribution/modification;"
echo " commercial use requires written permission: founders@deepvertex.tech)"
echo
echo "--- terms summary (first 25 lines) -------------------------"
sed -n '1,25p' "$SRC/docs/END-USER-TERMS.txt" 2>/dev/null
echo "------------------------------------------------------------"
if [ "$YES" = 1 ]; then
  echo "[--yes] End-User Terms accepted automatically."
else
  printf 'Type "agree" to accept the End-User Terms and continue: '
  read -r jawab
  if [ "$jawab" != "agree" ]; then
    echo "Terms not accepted — installation aborted."
    exit 1
  fi
fi

# ----------------------------------------------------------- 2) target dir
DEF="$HOME/DeepVertexMarineConsole"
if [ -z "$TUJUAN" ]; then
  if [ "$YES" = 1 ]; then
    TUJUAN="$DEF"
  else
    printf 'Install folder [%s]: ' "$DEF"
    read -r jawab
    TUJUAN="${jawab:-$DEF}"
  fi
fi
case "$TUJUAN" in "~"*) TUJUAN="$HOME${TUJUAN#\~}" ;; esac
TUJUAN="$(realpath -m "$TUJUAN")"

echo
echo "Source     : $SRC"
echo "Install to : $TUJUAN"
echo "Data (user): ~/.config/deepvertex-console (settings) · ~/DeepVertex-ROV-Captures (sessions)"
if [ "$DRY" = 1 ]; then
  echo "[--dry-run] Nothing changed. Planned steps:"
  echo "  1. terms accepted (above);  2. copy sources (unless installing in place);"
  echo "  3. build gui_t1/.venv (PySide6, evdev, pymavlink, numpy);"
  echo "  4. verify GStreamer + libxcb-cursor;  5. ready to launch."
  exit 0
fi

# ----------------------------------------------------------- 3) copy sources
if [ "$SRC" != "$TUJUAN" ]; then
  mkdir -p "$TUJUAN"
  echo "== copying sources -> $TUJUAN"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude .venv --exclude 'saved' --exclude '*.log' \
      "$SRC/" "$TUJUAN/"
  else
    (cd "$SRC" && tar --exclude=.venv --exclude=saved -cf - .) | \
      (cd "$TUJUAN" && tar -xf -)
  fi
fi

GUI="$TUJUAN/gui_t1"

echo "== 1) venv + PySide6"
if [ ! -d "$GUI/.venv" ]; then
  /usr/bin/python3 -m venv --system-site-packages "$GUI/.venv"
  echo "   venv created: $GUI/.venv"
fi
"$GUI/.venv/bin/python" -m pip install --quiet PySide6 evdev pymavlink numpy \
  && echo "   PySide6 + evdev + pymavlink + numpy OK"   # evdev = gamepad reader

echo "== 2) required GStreamer elements"
miss=0
for e in rtph264depay rtpjitterbuffer avdec_h264 matroskamux multifilesink fakesink valve tee; do
  if gst-inspect-1.0 "$e" >/dev/null 2>&1; then echo "   $e OK"; else echo "   $e MISSING"; miss=1; fi
done
[ "$miss" = 1 ] && echo "   → install: sudo apt install gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav"

echo "== 3) h264parse (.mkv recording)"
if gst-inspect-1.0 h264parse >/dev/null 2>&1; then
  echo "   h264parse OK — .mkv recording available"
else
  echo "   h264parse MISSING — trying to fetch the plugin without sudo …"
  tmp="$(mktemp -d)"
  (cd "$tmp" && apt-get download gstreamer1.0-plugins-bad >/dev/null 2>&1) || true
  deb="$(ls "$tmp"/gstreamer1.0-plugins-bad*.deb 2>/dev/null | head -1 || true)"
  if [ -n "$deb" ]; then
    dpkg-deb -x "$deb" "$tmp/x"
    mkdir -p "$GUI/plugins/gstreamer-1.0" "$GUI/plugins/lib"
    cp "$tmp/x/usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstvideoparsersbad.so" "$GUI/plugins/gstreamer-1.0/" 2>/dev/null || true
    cp "$tmp/x/usr/lib/x86_64-linux-gnu/"*.so* "$GUI/plugins/lib/" 2>/dev/null || true
    echo "   plugin bundled into $GUI/plugins/ (run_console_real.sh picks it up)"
  else
    echo "   could not fetch the package — recording falls back to .h264."
    echo "   or install manually: sudo apt install gstreamer1.0-plugins-bad"
  fi
  rm -rf "$tmp"
fi

echo "== 3b) libxcb-cursor (REQUIRED for the Qt6 window on X11)"
if ldconfig -p 2>/dev/null | grep -q "libxcb-cursor.so.0"; then
  echo "   libxcb-cursor OK"
else
  tmp2="$(mktemp -d)"
  for p in libxcb-cursor0; do
    (cd "$tmp2" && apt-get download "$p" >/dev/null 2>&1) || true
  done
  d="$(ls "$tmp2"/libxcb-cursor0*.deb 2>/dev/null | head -1 || true)"
  if [ -n "$d" ]; then
    dpkg-deb -x "$d" "$tmp2/x"
    mkdir -p "$GUI/tools/xcb"
    cp "$tmp2/x/usr/lib/x86_64-linux-gnu/"*.so* "$GUI/tools/xcb/" 2>/dev/null || true
    echo "   libxcb-cursor bundled into $GUI/tools/xcb/ (run_console_real.sh picks it up)"
  else
    echo "   could not fetch the package — install manually: sudo apt install libxcb-cursor0"
  fi
  rm -rf "$tmp2"
fi

echo "== 4) next steps"
echo "   launch : $GUI/run_console_real.sh"
echo "   tests  : cd $GUI && .venv/bin/python ../tests/test_video.py"
echo "   update : the console checks GitHub releases at every start (optional one-click update)"
echo "   removal: delete $TUJUAN ; user data stays in ~/.config/deepvertex-console + ~/DeepVertex-ROV-Captures"
echo "DONE."
