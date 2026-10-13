#!/usr/bin/env bash
# Installer topside  (Ubuntu 24.04) — venv + cek GStreamer + bundel plugin mkv (opsional).
# Aman dijalankan ulang (idempotent).
set -uo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"        # .../work
GUI="$DIR/gui_t1"

echo "== 1) venv + PySide6 =="
if [ ! -d "$GUI/.venv" ]; then
  /usr/bin/python3 -m venv --system-site-packages "$GUI/.venv"
  echo "   venv dibuat: $GUI/.venv"
fi
"$GUI/.venv/bin/python" -m pip install --quiet PySide6 && echo "   PySide6 OK"

echo "== 2) cek elemen GStreamer wajib =="
miss=0
for e in rtph264depay rtpjitterbuffer avdec_h264 matroskamux multifilesink fakesink valve tee; do
  if gst-inspect-1.0 "$e" >/dev/null 2>&1; then echo "   $e OK"; else echo "   $e HILANG"; miss=1; fi
done
[ "$miss" = 1 ] && echo "   → pasang: sudo apt install gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-libav"

echo "== 3) h264parse (rekaman .mkv) =="
if gst-inspect-1.0 h264parse >/dev/null 2>&1; then
  echo "   h264parse OK — rekaman .mkv tersedia"
else
  echo "   h264parse TIDAK ADA — mencoba ambil plugin tanpa sudo …"
  tmp="$(mktemp -d)"
  (cd "$tmp" && apt-get download gstreamer1.0-plugins-bad >/dev/null 2>&1) || true
  deb="$(ls "$tmp"/gstreamer1.0-plugins-bad*.deb 2>/dev/null | head -1 || true)"
  if [ -n "$deb" ]; then
    dpkg-deb -x "$deb" "$tmp/x"
    mkdir -p "$GUI/plugins/gstreamer-1.0" "$GUI/plugins/lib"
    cp "$tmp/x/usr/lib/x86_64-linux-gnu/gstreamer-1.0/libgstvideoparsersbad.so" "$GUI/plugins/gstreamer-1.0/" 2>/dev/null || true
    cp "$tmp/x/usr/lib/x86_64-linux-gnu/"*.so* "$GUI/plugins/lib/" 2>/dev/null || true
    echo "   plugin dibundel ke $GUI/plugins/ (run_console_real.sh memakai otomatis)"
  else
    echo "   GAGAL ambil paket — rekaman akan fallback ke .h264."
    echo "   atau pasang manual: sudo apt install gstreamer1.0-plugins-bad"
  fi
  rm -rf "$tmp"
fi

echo "== 3b) libxcb-cursor (WAJIB agar jendela tampil di X11/Qt6) =="
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
    echo "   libxcb-cursor dibundel ke $GUI/tools/xcb/ (run_console_real.sh memakai otomatis)"
  else
    echo "   GAGAL ambil paket — pasang manual: sudo apt install libxcb-cursor0"
  fi
  rm -rf "$tmp2"
fi

echo "== 4) uji cepat (opsional, tanpa hardware) =="
echo "   jalankan: $GUI/run_console_real.sh   (konsol)"
echo "   uji     : cd $GUI && .venv/bin/python ../tests/test_video.py"
echo "SELESAI."
