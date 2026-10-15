"""Pemeriksa versi + auto-update DeepVertex Marine Console.

Alur:
1. `fetch_latest()` — GET GitHub Releases API (butuh internet + User-Agent).
2. `is_newer(tag, current)` — bandingkan versi (tuple int).
3. Dialog popup di konsol menawarkan update (pilihan user: Update / Nanti).
4. `download()` + `apply_*()` — unduh aset rilis lalu ganti aplikasi:
   * Windows: EXE baru diunduh, skrip .bat menukar EXE setelah app keluar.
   * Linux  : tarball sumber diunduh, skrip .sh menyalin source ke folder
     instalasi (mempertahankan .venv + data user) lalu menjalankan ulang.

Semua fungsi jaringan memakai timeout pendek dan GAGAL DIAM (offline = tidak
mengganggu; hanya dicatat ke log konsol).
"""
import json
import os
import platform
import subprocess
import sys
import tarfile
import urllib.request

import paths

API_LATEST = ("https://api.github.com/repos/ZaydZaidan/deepvertex-console/"
              "releases/latest")
UA = "DeepVertexMarineConsole-Updater"
ASET_WIN = "DeepVertexMarineConsole.exe"
ASET_LINUX_PREFIX = "deepvertex-console-"


def parse_version(tag):
    """'v1.2.3' / '1.2.3-beta' -> (1, 2, 3). Bagian non-numerik diabaikan."""
    t = str(tag or "").strip().lstrip("vV")
    out = []
    for bagian in t.split("."):
        angka = ""
        for ch in bagian:
            if ch.isdigit():
                angka += ch
            else:
                break
        out.append(int(angka) if angka else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def is_newer(tag_baru, versi_lokal):
    return parse_version(tag_baru) > parse_version(versi_lokal)


def aset_url_untuk_platform(assets):
    """Pilih URL aset rilis utk platform ini dari daftar {name: url}.

    Windows: utamakan installer SETUP (update diterapkan lewat installer
    senyap); fallback ke EXE portable — kompatibilitas dengan updater versi
    lama yang hanya mengenal nama EXE."""
    if os.name == "nt":
        for nama, url in sorted(assets.items()):
            if nama.startswith("DeepVertexMarineConsole-Setup") and \
                    nama.endswith(".exe"):
                return url
        return assets.get(ASET_WIN)
    for nama, url in sorted(assets.items()):
        if nama.startswith(ASET_LINUX_PREFIX) and nama.endswith("-linux.tar.gz"):
            return url
    return None


def fetch_latest(timeout=6):
    """Ambil rilis terbaru dari GitHub. Return dict atau None (offline/gagal):
    {'tag','name','notes','assets': {nama: url}, 'page'}"""
    req = urllib.request.Request(API_LATEST, headers={
        "User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.load(r)
    aset = {}
    for a in d.get("assets") or []:
        if a.get("name") and a.get("browser_download_url"):
            aset[a["name"]] = a["browser_download_url"]
    return {
        "tag": d.get("tag_name") or "",
        "name": d.get("name") or "",
        "notes": d.get("body") or "",
        "assets": aset,
        "page": d.get("html_url") or "",
    }


def cek_update(versi_lokal, timeout=6):
    """Return dict info bila ADA versi lebih baru, selain itu None."""
    try:
        d = fetch_latest(timeout=timeout)
    except Exception:                                      # noqa: BLE001
        return None
    if not d or not is_newer(d["tag"], versi_lokal):
        return None
    d["url_aset"] = aset_url_untuk_platform(d["assets"])
    if not d["url_aset"]:
        return None
    return d


def download(url, dest, progress_cb=None, timeout=30):
    """Unduh url -> dest (streaming). progress_cb(frac 0..1) opsional."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r, \
            open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        dibaca = 0
        while True:
            blok = r.read(256 * 1024)
            if not blok:
                break
            f.write(blok)
            dibaca += len(blok)
            if progress_cb and total:
                try:
                    progress_cb(min(1.0, dibaca / float(total)))
                except Exception:                          # noqa: BLE001
                    pass
    return dest


# --------------------------------------------------------------- penerapan
def _dir_update():
    d = os.path.join(paths.data_dir(), "update")
    os.makedirs(d, exist_ok=True)
    return d


def apply_update_windows(url_aset, exe_lokal, progress_cb=None):
    """Unduh aset update + spawn skrip .bat penerap (setelah app keluar).

    Dua mode:
    * URL = installer Setup  → dipasang SENYAP (lisensi otomatis diterima di
      mode senyap; folder instalasi ter-ingat oleh installer), lalu aplikasi
      dibuka ulang. Ini mode utama v1.0.6+.
    * URL = EXE portable (updater lama) → tukar file langsung (kompatibilitas).
    Return path skrip .bat (sudah dijalankan lepas).
    """
    d = _dir_update()
    nama = os.path.basename(str(url_aset or "")).lower()
    setup = "setup" in nama
    baru = os.path.join(d, "setup-baru.exe" if setup else "baru.exe")
    download(url_aset, baru, progress_cb)
    bat = os.path.join(d, "terapkan_update.bat")
    kepala = (
        "@echo off\r\n"
        ":tunggu\r\n"
        "tasklist /fi \"imagename eq DeepVertexMarineConsole.exe\" | find /i "
        "\"DeepVertexMarineConsole.exe\" >nul && (\r\n"
        "  ping -n 2 127.0.0.1 >nul\r\n"
        "  goto tunggu\r\n"
        ")\r\n")
    if setup:
        isi = (kepala +
               "rem DeepVertex auto-update: pasang installer senyap, buka ulang.\r\n"
               "start /wait \"\" \"%~dp0setup-baru.exe\" /SILENT /SUPPRESSMSGBOXES "
               "/NORESTART /CLOSEAPPLICATIONS /NOCANCEL\r\n"
               "start \"\" \"%s\"\r\n"
               "del /q \"%~dp0setup-baru.exe\" >nul 2>nul\r\n"
               "del /q \"%~f0\" >nul 2>nul\r\n" % exe_lokal)
    else:
        isi = (kepala +
               "rem DeepVertex auto-update: tukar EXE portable, buka ulang.\r\n"
               ":salin\r\n"
               "copy /y \"%~dp0baru.exe\" \"%s\" >nul 2>nul\r\n"
               "if errorlevel 1 (\r\n"
               "  ping -n 2 127.0.0.1 >nul\r\n"
               "  goto salin\r\n"
               ")\r\n"
               "start \"\" \"%s\"\r\n"
               "del /q \"%~dp0baru.exe\" >nul 2>nul\r\n"
               "del /q \"%~f0\" >nul 2>nul\r\n" % (exe_lokal, exe_lokal))
    with open(bat, "w", encoding="utf-8") as f:
        f.write(isi)
    subprocess.Popen(["cmd", "/c", bat], close_fds=True,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return bat


def apply_update_linux(tar_url, dir_instalasi, pid=None, progress_cb=None):
    """Unduh tarball sumber + spawn skrip .sh penyalin (setelah app keluar).

    Skrip: tunggu PID lama keluar, ekstrak tarball, salin source (kecuali
    .venv/data), lalu jalankan ulang launcher. Return path skrip .sh."""
    d = _dir_update()
    tar_path = os.path.join(d, "baru.tar.gz")
    download(tar_url, tar_path, progress_cb)
    # ekstrak sekarang (aman), simpan sumber baru di d/baru
    src_baru = os.path.join(d, "baru")
    if os.path.isdir(src_baru):
        subprocess.run(["rm", "-rf", src_baru], check=False)
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(d)                                   # -> d/deepvertex-*...
    akar = [os.path.join(d, n) for n in os.listdir(d)
            if n.startswith(ASET_LINUX_PREFIX) and os.path.isdir(os.path.join(d, n))]
    if not akar:
        raise RuntimeError("isi tarball tidak dikenali")
    if os.path.isdir(src_baru):
        subprocess.run(["rm", "-rf", src_baru], check=False)
    os.rename(sorted(akar)[-1], src_baru)
    sh = os.path.join(d, "terapkan_update.sh")
    with open(sh, "w", encoding="utf-8") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            "# DeepVertex auto-update: tunggu proses lama keluar, salin source,\n"
            "# jalankan ulang (mempertahankan .venv dan data user).\n"
            "set -u\n"
            "PID_APP=\"${1:-}\"\n"
            "DIR=\"%s\"\n"
            "SRC=\"%s\"\n"
            "[ -n \"$PID_APP\" ] && while kill -0 \"$PID_APP\" 2>/dev/null; do "
            "sleep 0.5; done\n"
            "sleep 1\n"
            "rsync -a --delete --exclude .venv --exclude saved --exclude "
            "'*.log' \"$SRC/\" \"$DIR/\" 2>/dev/null || "
            "cp -a \"$SRC/.\" \"$DIR/\"\n"
            "nohup \"$DIR/gui_t1/run_console_real.sh\" "
            "--raw-agent \"${DV_RAW_AGENT:-http://192.168.2.2:8765}\" "
            ">/dev/null 2>&1 &\n"
            "rm -rf \"$SRC\" \"%s\" 2>/dev/null\n"
            "rm -f \"$0\"\n" % (dir_instalasi, src_baru, tar_path))
    os.chmod(sh, 0o755)
    subprocess.Popen(["bash", sh, str(pid or os.getpid())], start_new_session=True)
    return sh


def versi_saat_ini(modul_konsol=None):
    """Versi aplikasi (dari konstanta modul console bila tersedia)."""
    if modul_konsol is not None:
        v = getattr(modul_konsol, "__version__", None)
        if v:
            return str(v)
    return "0.0.0"


def info_platform():
    return {
        "os": "windows" if os.name == "nt" else platform.system().lower(),
        "python": sys.version.split()[0],
        "exe": bool(getattr(sys, "frozen", False)),
    }


# ---------------------------------------------------------------- UI konsol
def periksa_di_latar(w, versi_lokal=None):
    """Panggil dari konsol setelah show(): cek rilis di thread latar, dan bila
    ADA versi lebih baru tampilkan popup (pilihan user). Offline = diam.

    UI diimpor lokal agar modul ini tetap importable tanpa PySide (uji).
    """
    from PySide6 import QtCore

    class _Cek(QtCore.QThread):
        hasil = QtCore.Signal(object)

        def __init__(self, versi, parent=None):
            super().__init__(parent)
            self._versi = versi

        def run(self):
            try:
                self.hasil.emit(cek_update(self._versi))
            except Exception:                              # noqa: BLE001
                self.hasil.emit(None)

    v = str(versi_lokal or versi_saat_ini(sys.modules.get("rov_console_real")))
    t = _Cek(v, w)
    t.hasil.connect(lambda info: (info is not None and w.isVisible() and
                                  _tanya_lalu_terapkan(w, info, v)))
    t.start()
    w._upd_thread = t                    # simpan referensi (anti GC)
    return t


def _diskusikan_update(w, info, v_lokal):
    """Popup 'ada versi baru' (pilihan user). True = user pilih update."""
    from PySide6 import QtCore, QtGui, QtWidgets
    d = QtWidgets.QDialog(w)
    d.setWindowTitle("Update tersedia — DeepVertex Marine Console")
    v = QtWidgets.QVBoxLayout(d)
    judul = QtWidgets.QLabel("Versi baru tersedia: <b>%s</b>  (terpasang: %s)"
                             % (info.get("tag", "?"), v_lokal))
    judul.setWordWrap(True)
    v.addWidget(judul)
    catatan = QtWidgets.QPlainTextEdit()
    catatan.setReadOnly(True)
    catatan.setMaximumBlockCount(400)
    catatan.setPlainText((info.get("notes") or "").strip()[:6000])
    catatan.setMinimumSize(560, 280)
    v.addWidget(catatan, 1)
    baris = QtWidgets.QHBoxLayout()
    b_web = QtWidgets.QPushButton("Buka halaman rilis…")
    b_web.setObjectName("BtnTiny")
    b_web.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
        QtCore.QUrl(info.get("page") or "")))
    baris.addWidget(b_web)
    baris.addStretch(1)
    b_nanti = QtWidgets.QPushButton("Nanti")
    b_nanti.clicked.connect(d.reject)
    baris.addWidget(b_nanti)
    b_upd = QtWidgets.QPushButton("Update sekarang")
    b_upd.setObjectName("BtnPrimary")
    b_upd.clicked.connect(d.accept)
    baris.addWidget(b_upd)
    v.addLayout(baris)
    return bool(d.exec())


def _tanya_lalu_terapkan(w, info, v_lokal):
    from PySide6 import QtWidgets
    if not _diskusikan_update(w, info, v_lokal):
        return
    arah = _dir_update()
    prog = QtWidgets.QProgressDialog("Mengunduh pembaruan…", "", 0, 100, w)
    prog.setWindowTitle("DeepVertex — update")
    prog.setMinimumWidth(460)
    prog.setCancelButton(None)
    prog.setWindowModality(QtWidgets.Qt.WindowModal)

    def _pb(frac):
        prog.setValue(int(frac * 100))
        QtWidgets.QApplication.processEvents()

    try:
        if os.name == "nt":
            if getattr(sys, "frozen", False):
                exe = sys.executable
            else:
                exe = os.path.join(paths.APP, "installation", "DeepVertexMarineConsole.exe")
            skrip = apply_update_windows(info["url_aset"], exe, _pb)
        else:
            skrip = apply_update_linux(info["url_aset"],
                                       os.path.dirname(paths.APP),
                                       os.getpid(), _pb)
    except Exception as exc:                               # noqa: BLE001
        prog.close()
        QtWidgets.QMessageBox.warning(
            w, "DeepVertex — update",
            "Gagal menerapkan pembaruan:\n%s" % exc)
        return
    prog.setValue(100)
    prog.close()
    QtWidgets.QMessageBox.information(
        w, "DeepVertex — update",
        "Pembaruan siap diterapkan.\n\nAplikasi akan DITUTUP, diganti versi "
        "baru, lalu terbuka kembali otomatis.")
    QtWidgets.QApplication.instance().quit()
