#!/usr/bin/env bash
# Jalankan ROV Mission Console (dashboard lokal).
#   bash run_dashboard.sh [opsi…]        lihat: python3 rov_dashboard.py --help
set +u
export PATH=/usr/bin:/bin:/usr/local/bin:$PATH
export DISPLAY="${DISPLAY:-:1}"
export XAUTHORITY="${XAUTHORITY:-/run/user/1000/gdm/Xauthority}"
source /opt/ros/jazzy/setup.bash >/dev/null 2>&1
source /home/zayd/ros2_ws/install/setup.bash >/dev/null 2>&1
# profil Fast DDS wajib: tanpa ini stream citra 720p dari relay kolap ke ~1 Hz
export FASTRTPS_DEFAULT_PROFILES_FILE="$HOME/.ros/fastdds_shm_large.xml"
cd "$(dirname "$0")" || exit 1
exec python3 rov_dashboard.py "$@"
