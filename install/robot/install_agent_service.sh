#!/usr/bin/env bash
# One-time setup (run ON THE ROBOT): make the DeepVertex RAW agent start
# automatically at every boot, with auto-restart on failure.
#
# Usage (on the robot, via SSH):
#   bash install_agent_service.sh [/full/path/to/raw_agent.py]
#
# Default path: $HOME/onboard/raw_agent.py   (change with the argument)
set -euo pipefail

AGENT="${1:-$HOME/onboard/raw_agent.py}"
if [ ! -f "$AGENT" ]; then
  echo "raw_agent.py not found at: $AGENT"
  echo "Usage: bash install_agent_service.sh /full/path/to/raw_agent.py"
  exit 1
fi

USER_NOW="$(whoami)"
UNIT=/etc/systemd/system/dv-raw-agent.service

sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=DeepVertex RAW capture agent (ROV)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER_NOW
ExecStart=/bin/bash -lc 'exec python3 $AGENT --port 8765 >> /tmp/dv-raw-agent.log 2>&1'
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now dv-raw-agent.service
sleep 1
systemctl --no-pager --lines=5 status dv-raw-agent.service || true
echo
echo "Done. The RAW agent now starts automatically with the robot"
echo "(service: dv-raw-agent; log: /tmp/dv-raw-agent.log on the robot)."
