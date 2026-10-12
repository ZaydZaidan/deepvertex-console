# DeepVertex Raw Capture — BlueOS Extension (scaffold Langkah C)

Membungkus `raw_agent.py` (perekam RAW onboard) sebagai extension BlueOS, supaya
pemasangan di Pi rapi (dapat dimatikan/dihapus tanpa jejak).

> **Status: scaffold.** Format metadata/manifest diverifikasi terhadap dokumen
> "Extensions" BlueOS resmi SAAT TANGAN (Langkah C / begitu Pi 4 ada). Nomor
> device kamera & mounting path juga dikonfirmasi di Pi.

## Isi

| Berkas | Fungsi |
|---|---|
| `raw_agent.py` | perekam — **salin dulu**: `cp ../raw_agent.py .` (satu sumber kebenaran tetap di `../`) |
| `Dockerfile` | image minimal: python3 + GStreamer (v4l2src, multifilesink) |
| `docker-compose.yml` | service + akses jaringan host + device kamera + volume data |

## Cara pasang di Pi (Langkah C — perdana)

1. Salin FOLDER ini ke BlueOS (File Browser ke `/root/extensions/deepvertex-raw/`,
   atau `scp` lewat tether).
2. Pastikan `raw_agent.py` sudah disalin ke folder ini (`cp ../raw_agent.py .`), lalu build & jalankan dari Terminal BlueOS:
   `cd /root/extensions/deepvertex-raw && docker compose up -d --build`
   (atau daftarkan lewat tombol "+" di Extensions Manager bila format manifest sudah
   disesuaikan dengan versi BlueOS yang terpasang).
3. Cek: `curl http://127.0.0.1:8765/status` di Pi.
4. Dari laptop: `./run_console_real.sh --raw-agent http://192.168.2.2:8765`
   (192.168.2.2 = default IP BlueOS).

## Yang WAJIB diverifikasi di Pi (jangan diasumsikan)

- Nomor device stereo: `ls /dev/video*` → perbarui `--device-left/--device-right`
  (di docker-compose atau CMD).
- Path penyimpanan: volume `/data` → pastikan cukup ruang & cepat (SD vs SSD).
- Format caps YUYV & resolusi B0495: `v4l2-ctl --list-formats-ext` (uji deteksi).
- `io-mode` v4l2src (dmabuf vs mmap) & beban CPU saat 2 kamera simultan.
- Auth token: set `--token` (jangan biarkan terbuka di jaringan).
