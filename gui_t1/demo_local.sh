#!/usr/bin/env bash
# Demo TAMPILAN konsol tanpa hardware:
#   3 stream dummy (port 5600-5602) + kendaraan MAVLink palsu + agen RAW uji → konsol.
# Tutup jendela konsol = semua helper dimatikan otomatis.
#
#   bash demo_local.sh                       # buka konsol (demo penuh)
#   bash demo_local.sh --shot cek.png        # + screenshot otomatis ke cek.png
#   bash demo_local.sh --mavlink udpin:0.0.0.0:14550   # opsi konsol lain ikut diteruskan
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$DIR/.."
export DISPLAY="${DISPLAY:-:1}"
export XAUTHORITY="${XAUTHORITY:-/run/user/1000/gdm/Xauthority}"
PIDS=()

spawn() { "$@" & PIDS+=($!); }

echo "[demo] 3 stream dummy (port 5600/5601/5602) …"
for p in 5600 5601 5602; do
  spawn gst-launch-1.0 -q videotestsrc is-live=true ! video/x-raw,width=640,height=360,framerate=25/1 ! \
    x264enc tune=zerolatency speed-preset=ultrafast key-int-max=25 bitrate=600 ! \
    rtph264pay config-interval=1 pt=96 ! udpsink host=127.0.0.1 port=$p
done

echo "[demo] kendaraan MAVLink palsu …"
spawn /usr/bin/python3 "$ROOT/tests/fake_vehicle.py" --secs 7200

if curl -s --max-time 1 http://127.0.0.1:8765/status >/dev/null 2>&1; then
  echo "[demo] agen RAW sudah jalan di:8765 — dilewati"
else
  echo "[demo] agen RAW uji (port 8765, data di /tmp/dv_raw_demo) …"
  spawn /usr/bin/python3 "$ROOT/onboard/raw_agent.py" --test --dir /tmp/dv_raw_demo \
      --chunk-mb 32 --port 8765 --width 640 --height 360 --fps 25
  sleep 1
  curl -s -X POST http://127.0.0.1:8765/capture/start -H 'Content-Type: application/json' \
       -d '{"cam":"both","chunk_mb":32}' >/dev/null 2>&1
  sleep 2.5
  curl -s -X POST http://127.0.0.1:8765/capture/stop -H 'Content-Type: application/json' \
       -d '{"cam":"both"}' >/dev/null 2>&1
fi

echo "[demo] membuka konsol …"
"$DIR/run_console_real.sh" "$@"
RC=$?

echo "[demo] konsol ditutup (rc=$RC) — mematikan helper …"
for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null; done
pkill -f "[v]ideotestsrc.*udpsink" 2>/dev/null
pkill -f "[f]ake_vehicle.py" 2>/dev/null
pkill -f "[r]aw_agent.py" 2>/dev/null
wait 2>/dev/null
echo "[demo] selesai — data raw uji ada di /tmp/dv_raw_demo (boleh dihapus)"
