# ROV Mission Console

Dashboard lokal (PyQt5) untuk misi pemetaan koral BlueROV2: memantau simulator
Stonefish, stereo VIO (VINS-Fusion), lintasan VIO vs ground truth, dan telemetri
ArduSub/Pixhawk. **GUI juga membuka seluruh stack-nya sendiri.**

```
bash "/home/zayd/Project BRIN VIO/gui/run_dashboard.sh"     # atau: rovgui
```

Kode ada di `gui/` repo `stonefish_bluerov2`; jalur lokal
`/home/zayd/Project BRIN VIO/gui` adalah symlink ke sana, jadi `rovgui` tetap jalan.

## Apa yang dilakukan GUI saat dibuka

1. membersihkan sisa proses uji lama (sim, relay, SITL, VINS, lawnmower);
2. start **simulator + relay** (`ros2 launch stonefish_bluerov2 bluerov2_sim_spec.launch.py`)
   → menunggu `/bluerov2/odometry` mengalir (>5 Hz);
3. start **ArduSub SITL** (`scripts/start_ardusub.sh`, TCP 5760 + telemetri 5762)
   → menunggu paket MAVLink;
4. start **VINS-Fusion** (`ros2 launch vins_fusion_ros2 vins_bluerov2.launch.py`);
5. tombol **START AUTONOMOUS** menyala begitu langkah 2+3 siap; menekannya menjalankan
   controller lawnmower (`ros2 run stonefish_bluerov2 bluerov2_lawnmower.py`).

Menutup GUI = menghentikan misi dan seluruh stack (`--keep-stack` untuk membiarkan stack hidup).

## Kontrol proses

| Elemen | Fungsi |
|---|---|
| `START ALL` | simpul start seluruh komponen berurutan |
| `STOP ALL` | hentikan misi lalu semua komponen (SIGINT ke group proses, SIGKILL bila perlu) |
| `▶ / ■` per komponen | start/stop satu komponen (dengan cek dependensi) |
| `START AUTONOMOUS` | toggle misi: nyala = lawnmower jalan, mati = lawnmower dihentikan + RC dinetralkan |
| `START MAPPING MANUAL` | toggle sesi pemetaan MANUAL (2 Okt 2026): pilot mengemudi dengan gamepad (deadman), sementara **capture relay + logger + node guide** jalan. Guide menggambar **jalur rencana (putus-putus) + kotak area** di peta. STOP = teleop dinetralkan + run ditutup seperti misi autonomous (analisis + SFM). |
| `START MANUAL PILOT` | pilot saja (mode MANUAL lama): **tidak ada** capture, **tidak ada** guide |

## Auto-PID (menu Tuning)

Menu **Tuning ▸ Auto-PID** (Ctrl+T) menyetel parameter ArduSub ASLI lewat MAVLink
(`rov_pid_autotune.py`): loop depth (`PSC_D_*`), heading/roll/pitch (`ATC_*`), berbasis uji
**gangguan** (pulsa gaya lalu ukur respon), bukan langkah RC.

- **Dialog opsi**: APPLY (simpan kandidat terbaik), ARM otomatis, iterasi, amplitudo, durasi
  langkah, metode awal (auto/SIMC/ZN/...), metode uji (gangguan/tangga), kedalaman minimum uji,
  **REAL ROV**, dan **MAVLink host**.
- **REAL ROV (4 Okt 2026)**: centang REAL ROV untuk robot asli — syarat sim+SITL DILEWATI,
  gangguan otomatis memakai pulsa RC (pola 8-thruster hanya ada di SITL), dan host MAVLink
  diarahkan ke link robot (mis. `udp:192.168.2.1:14550` / `serial:/dev/ttyACM0:115200`).
  Snapshot parameter otomatis; **Ctrl+C = parameter dikembalikan**. Snapshot bisa dipulihkan
  juga via menu Tuning ▸ Restore parameters.
- **Jendela respon+galat** (`pid_view.py`) terbuka OTOMATIS saat mode tuning: tiap iterasi
  digambar sebagai jejak tersendiri + galat. Manual: `/usr/bin/python3 gui/pid_view.py`.
- Verifikasi offscreen: `scripts/tools/uji_gui_tuning.py` (source ROS dulu).

## Panel

- **Stack · proses** — status + uptime tiap komponen, tombol start/stop.
- **Stonefish · simulator view** — **panel sengaja dikosongkan** (23 Sep 2026): mode
  `mirror` dihapus, pemilik sistem menonton jendela Stonefish secara langsung.
  Mode `reparent` (embed sungguhan) hanya bila diminta eksplisit dan berisiko.
- **VIO left / right** — feed 720p; **capture** (opsional) 1920×1200 RGB untuk SfM.
- **Trajectory** — lintasan VIO vs ground truth + RMSE, panjang jalur, rasio skala.
  Di tab **MAP**, selain jejak GT (hitam) & VIO (merah) ada **garis petunjuk (guide)**
  dari node misi: jalur DIRENCANAKAN putus-putus + panah arah gerak + kotak batas area,
  lengkap dengan penanda `NEXT` (waypoint terdekat). Guide muncul **seketika saat tombol
  misi ditekan** — jalur autonomous sudah diketahui sebelum robot bergerak (§2 Okt 2026).
- **Flight / Gyro / Power** — mode, armed, attitude, depth, altitude AGL, heading,
  throttle, gyro, akselerasi, baterai.
- **VIO·sim status**, **ArduSub messages**, **Log stack**.

## Opsi

```
--embed auto|reparent|off          default auto = panel simulasi kosong (mirror dihapus)
--no-autostart                     jangan buka stack otomatis (start manual dari panel)
--autostart-mission                begitu stack siap, langsung toggle START AUTONOMOUS
--keep-stack                       jangan hentikan stack saat GUI ditutup
--log-dir DIR                      folder log proses (default /tmp/rov_dashboard_logs)
--capture-preview                  tambah tile capture full-res
--mavlink auto|tcp:127.0.0.1:5762  sumber telemetri (default auto)
--size WxH, --shot FILE, --shot-after N, --no-ros, --no-mav
```

## Peringatan penting

1. **Jangan pakai `--embed reparent`** kecuali Anda tahu risikonya: reparent jendela
   X11 pernah membuat jendela dashboard menutupi seluruh layar dan menghilangkan
   terminal. Mode default tidak menyentuh jendela simulator sama sekali — panel
   simulasi dikosongkan (mode `mirror` dihapus 23 Sep 2026).
2. Tutup GUI lewat jendelanya (atau `q`/Ctrl-C di terminal peluncur) supaya stack
   dihentikan dengan rapi.
3. Peta tautan MAVLink (SERIAL0/TCP 5760 hanya menerima **satu** klien, dan SITL ArduSub
   berhenti di `Waiting for connection ....` sampai klien itu datang):
   - **5760** (SERIAL0) — dashboard (sekaligus membuka blokir boot SITL)
   - **5762** (SERIAL2) — controller lawnmower (`mavlink_url:=tcp:127.0.0.1:5762`)
   - **14550** (SERIAL1/UDP) — QGC (dashboard bisa memakai ini sebagai cadangan)
   Runner Tahap B (tanpa GUI) tetap memakai default controller di 5760.

## Berkas

| Berkas | Isi |
|---|---|
| `rov_dashboard.py` | jendela utama, tata letak panel, timer |
| `stackui.py` | panel STACK, tombol START AUTONOMOUS, autostart berurutan |
| `procman.py` | manajer proses (group SIGINT/SIGKILL, log, cleanup sisa proses) |
| `widgets.py` | widget QPainter kustom (panel, tile kamera, plot) |
| `theme.py` | palet gelap netral + aksen amber |
| `rosiface.py` | ROS 2: citra VIO/capture, odometry, ground truth |
| `maviface.py` | MAVLink via pymavlink (telemetri + netralkan RC) |
| `xembed.py` | pencarian jendela simulator, cek WM, detach |
| `pid_view.py` | jendela realtime **respon + galat** Auto-PID (terbuka otomatis saat mode tuning; juga untuk robot asli) |
| `run_dashboard.sh` | peluncur (sumber ROS 2 + profil DDS 16 MB) |
