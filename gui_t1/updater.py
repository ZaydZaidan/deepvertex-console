"""Version check + auto-update for DeepVertex Marine Console.

Flow
----
1. `fetch_latest()` — GET the GitHub Releases API (needs internet + UA).
2. `cek_update(versi_lokal)` — returns release info only when NEWER.
3. The console opens a designed popup (Update now / Not now / release page).
4. "Update now" downloads in a BACKGROUND THREAD with a real progress bar
   (Cancel works); then applies:
   * Windows: silent installer (Setup asset) — portable-EXE swap as fallback;
   * Linux  : source tarball, rsync over the install dir keeping `.venv`.
   The app quits and the apply script relaunches it.

All network helpers use short timeouts and fail silently when offline.
`--preview-update` shows the dialog with demo data (small real download) so
the UI can be reviewed without waiting for an actual release.
"""
import json
import os
import platform
import subprocess
import sys
import tarfile
import time
import urllib.request

import paths

API_LATEST = ("https://api.github.com/repos/ZaydZaidan/deepvertex-console/"
              "releases/latest")
UA = "DeepVertexMarineConsole-Updater"
ASET_WIN = "DeepVertexMarineConsole.exe"
ASET_LINUX_PREFIX = "deepvertex-console-"


def parse_version(tag):
    """'v1.2.3' / '1.2.3-beta' -> (1, 2, 3). Non-numeric parts ignored."""
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
    """Pick the release asset URL for this platform.

    Windows prefers the Setup installer (applied silently); the portable EXE
    is kept as a fallback for updaters from older versions.
    """
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
    """Latest release from GitHub:
    {'tag','name','notes','assets': {name: url}, 'page'}. Raises on failure."""
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
    """Return release info dict when a NEWER release exists, else None."""
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


def download(url, dest, progress_cb=None, timeout=30, batal=None):
    """Stream `url` -> `dest`. progress_cb(bytes_done, total_bytes) is called
    per chunk (total may be 0). `batal()` -> True aborts the download."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r, \
            open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        dibaca = 0
        if progress_cb:
            try:
                progress_cb(0, total)
            except Exception:                              # noqa: BLE001
                pass
        while True:
            if batal is not None and batal():
                raise RuntimeError("download cancelled")
            blok = r.read(128 * 1024)
            if not blok:
                break
            f.write(blok)
            dibaca += len(blok)
            if progress_cb:
                try:
                    progress_cb(dibaca, total)
                except Exception:                          # noqa: BLE001
                    pass
    return dest


# ------------------------------------------------------------- housekeeping
def _dir_update():
    d = os.path.join(paths.data_dir(), "update")
    os.makedirs(d, exist_ok=True)
    return d


def _tulis_bat(dest, setup, exe_lokal, target=""):
    """Write + launch the Windows apply script for an ALREADY-DOWNLOADED file.

    v2 (audit 15 Okt): everything is logged to `update\\apply_log.txt` (steps,
    installer exit code, file-lock retries + cap) so a silent failure is never
    a mystery again. IMPORTANT: the text is built with CONCATENATION ONLY —
    the old `% (...)`-format crashed on batch tokens like `%~dp0`
    (ValueError) BEFORE the bat was ever written: that was the real cause of
    "download hits 100% then nothing happens" in v1.0.7. The installer is
    invoked DIRECTLY (the bat waits for it) and the spawn uses explicit
    DEVNULL handles (windowed PyInstaller builds can fail to spawn children
    with invalid inherited handles).
    """
    d = _dir_update()
    bat = os.path.join(d, "apply_update.bat")
    log = os.path.join(d, "apply_log.txt")
    payload = os.path.basename(dest)
    kepala = (
        "@echo off\r\n"
        "set LOG=" + '"' + log + '"' + "\r\n"
        "echo [%DATE% %TIME%] apply start target=" + (target or "-")
        + " (dir %~dp0) >> %LOG%\r\n"
        "if not exist \"%~dp0" + payload + "\" (echo [%DATE% %TIME%] "
        "PAYLOAD MISSING >> %LOG%)\r\n"
        ":wait\r\n"
        "tasklist /fi \"imagename eq DeepVertexMarineConsole.exe\" | find /i "
        "\"DeepVertexMarineConsole.exe\" >nul && (\r\n"
        "  ping -n 2 127.0.0.1 >nul\r\n"
        "  goto wait\r\n"
        ")\r\n"
        "echo [%DATE% %TIME%] app has exited >> %LOG%\r\n")
    if setup:
        isi = (kepala +
               "echo [%DATE% %TIME%] running installer silently >> %LOG%\r\n"
               "\"%~dp0" + payload + "\" /SILENT /SUPPRESSMSGBOXES /NORESTART "
               "/CLOSEAPPLICATIONS /NOCANCEL\r\n"
               "echo [%DATE% %TIME%] installer exit code %ERRORLEVEL% >> %LOG%\r\n"
               "set N=0\r\n"
               ":sweep\r\n"
               "if not exist \"%~dp0" + payload + "\" goto relaunch\r\n"
               "set /a N+=1\r\n"
               "if %N% GEQ 15 goto relaunch\r\n"
               "ping -n 3 127.0.0.1 >nul\r\n"
               "del /q \"%~dp0" + payload + "\" >nul 2>nul\r\n"
               "goto sweep\r\n")
    else:
        isi = (kepala +
               "echo [%DATE% %TIME%] swapping portable EXE >> %LOG%\r\n"
               ":copy\r\n"
               "copy /y \"%~dp0" + payload + "\" \"" + exe_lokal
               + "\" >nul 2>nul\r\n"
               "if errorlevel 1 (\r\n"
               "  ping -n 2 127.0.0.1 >nul\r\n"
               "  goto copy\r\n"
               ")\r\n"
               "echo [%DATE% %TIME%] swap ok >> %LOG%\r\n"
               "del /q \"%~dp0" + payload + "\" >nul 2>nul\r\n")
    isi += (
        ":relaunch\r\n"
        "echo [%DATE% %TIME%] relaunching >> %LOG%\r\n"
        "start \"\" \"" + exe_lokal + "\"\r\n"
        "echo [%DATE% %TIME%] done (bat self-delete) >> %LOG%\r\n"
        "del /q \"%~f0\" >nul 2>nul\r\n")
    with open(bat, "w", encoding="utf-8") as f:
        f.write(isi)
    # catat target update utk banner hasil di start berikutnya
    try:
        with open(os.path.join(d, "pending.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"target": target, "ts": time.time(), "log": log}, f)
    except OSError:
        pass
    subprocess.Popen(["cmd", "/c", bat], close_fds=True,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL,
                     env=_env_bersih())
    return bat


def _env_bersih():
    """os.environ TANPA variabel PyInstaller (higiene spawn anak).

    Uji empiris 15 Okt: build ini tetap boot walau variabel _PYI_* disetel
    (jadi BUKAN faktor penentu crash relaunch — penyebab aslinya PID-recycle
    folder _MEI, ditangani jeda relaunch di koneksi.py). Tetap dipakai:
    murah + mencegah perilaku bootloader versi lain yang bisa berbeda."""
    env = dict(os.environ)
    for k in ("_MEIPASS2", "_PYI_APPLICATION_HOME_DIR", "_PYI_ARCHIVE_FILE",
              "_PYI_PARENT_PROCESS_LEVEL", "_PYI_SPLASH_IPC"):
        env.pop(k, None)
    return env


def cek_hasil_update(versi_sekarang):
    """Message for the startup banner about the previous update attempt.

    * pending target == running version -> update COMPLETED (clear marker).
    * pending target newer + older than ~15 min -> looks FAILED (keep marker;
      the message points at update\\apply_log.txt).
    * pending fresh (< 15 min) -> install may still be running: silent.
    """
    d = os.path.join(paths.data_dir(), "update")
    p = os.path.join(d, "pending.json")
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:                                      # noqa: BLE001
        return None
    target = str(st.get("target") or "")
    umur = time.time() - float(st.get("ts") or 0)
    if not target:
        return None
    try:
        if parse_version(target) <= parse_version(versi_sekarang):
            try:
                os.remove(p)
            except OSError:
                pass
            return ("Update complete — now running v%s (updated to "
                    "v%s)." % (versi_sekarang, target))
        if umur > 15 * 60:
            return ("Update to v%s did NOT apply (still v%s). Details: %s "
                    "— you can also run the installer from that folder "
                    "manually." % (target, versi_sekarang,
                                   os.path.join(d, "apply_log.txt")))
    except Exception:                                      # noqa: BLE001
        pass
    return None


def _tulis_sh(tar_path, dir_instalasi, pid=None, target=""):
    """Extract an ALREADY-DOWNLOADED source tarball + write/launch the apply
    script (Linux): waits for the app to exit, rsyncs the sources over the
    install dir keeping `.venv`, then relaunches."""
    d = _dir_update()
    src_baru = os.path.join(d, "baru")
    if os.path.isdir(src_baru):
        subprocess.run(["rm", "-rf", src_baru], check=False)
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(d)
    akar = [os.path.join(d, n) for n in os.listdir(d)
            if n.startswith(ASET_LINUX_PREFIX)
            and os.path.isdir(os.path.join(d, n))]
    if not akar:
        raise RuntimeError("unrecognized tarball layout")
    if os.path.isdir(src_baru):
        subprocess.run(["rm", "-rf", src_baru], check=False)
    os.rename(sorted(akar)[-1], src_baru)
    sh = os.path.join(d, "apply_update.sh")
    with open(sh, "w", encoding="utf-8") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            "# DeepVertex auto-update: wait for the old process, copy sources,\n"
            "# relaunch (keeps .venv and user data).\n"
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
    try:
        with open(os.path.join(_dir_update(), "pending.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"target": target, "ts": time.time(),
                       "log": os.path.join(_dir_update(), "apply_update.sh")},
                      f)
    except OSError:
        pass
    subprocess.Popen(["bash", sh, str(pid or os.getpid())], start_new_session=True,
                     env=_env_bersih())
    return sh


def versi_saat_ini(modul_konsol=None):
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


# ------------------------------------------------------------------------ UI
def _diskusikan_update(w, info, v_lokal):
    """Designed update dialog. True = user chose "Update now"."""
    from PySide6 import QtCore, QtGui, QtWidgets
    try:
        import theme
        C_HEAD = theme.warna("head")
        C_LINE = theme.warna("line")
        C_TEXT = theme.warna("ink")
        C_DIM = theme.warna("ink_dim")
        C_ACC = theme.warna("accent")
    except Exception:                                      # noqa: BLE001
        C_HEAD, C_LINE, C_TEXT = "#10202F", "#2A3B4D", "#D7E3EF"
        C_DIM, C_ACC = "#8AA0B4", "#37C8DE"

    d = QtWidgets.QDialog(w)
    d.setWindowTitle("Software update - DeepVertex Marine Console")
    d.setMinimumWidth(640)
    v = QtWidgets.QVBoxLayout(d)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)

    # header band: icon + headline + version chips
    head = QtWidgets.QFrame()
    head.setStyleSheet("QFrame { background: %s; border-bottom: 1px solid %s; }"
                       % (C_HEAD, C_LINE))
    hl = QtWidgets.QHBoxLayout(head)
    hl.setContentsMargins(18, 16, 18, 16)
    hl.setSpacing(14)
    ico = QtWidgets.QLabel()
    try:
        pm = QtGui.QPixmap(os.path.join(paths.APP, "ikon",
                                        "deepvertex-app-icon.png"))
        if not pm.isNull():
            ico.setPixmap(pm.scaled(54, 54, QtCore.Qt.KeepAspectRatio,
                                    QtCore.Qt.SmoothTransformation))
    except Exception:                                      # noqa: BLE001
        pass
    hl.addWidget(ico)
    kiri = QtWidgets.QVBoxLayout()
    kiri.setSpacing(2)
    t1 = QtWidgets.QLabel("A new version is available")
    t1.setStyleSheet("font-size: 17px; font-weight: 700;")
    t2 = QtWidgets.QLabel("DeepVertex Marine Technology Console")
    t2.setStyleSheet("color: %s; font-size: 11px;" % C_DIM)
    kiri.addWidget(t1)
    kiri.addWidget(t2)
    hl.addLayout(kiri)
    hl.addStretch(1)
    chips = QtWidgets.QHBoxLayout()
    chips.setSpacing(8)
    c_now = QtWidgets.QLabel(str(v_lokal))
    c_now.setStyleSheet("color: %s; font-size: 13px;" % C_DIM)
    arah = QtWidgets.QLabel("\u2192")
    arah.setStyleSheet("color: %s; font-size: 15px;" % C_DIM)
    c_new = QtWidgets.QLabel(str(info.get("tag", "?")))
    c_new.setStyleSheet(
        "font-size: 16px; font-weight: 800; color: %s; background: %s; "
        "border: 1px solid %s; border-radius: 6px; padding: 3px 12px;"
        % (C_ACC, C_HEAD, C_ACC))
    chips.addWidget(c_now)
    chips.addWidget(arah)
    chips.addWidget(c_new)
    hl.addLayout(chips)
    v.addWidget(head)

    # release notes (plain, readable)
    catatan = QtWidgets.QPlainTextEdit()
    catatan.setReadOnly(True)
    catatan.setMaximumBlockCount(400)
    catatan.setPlainText((info.get("notes") or "").strip() or
                         "No release notes provided.")
    catatan.setStyleSheet(
        "QPlainTextEdit { background: transparent; color: %s; border: 0; "
        "padding: 12px 16px; font-size: 12px; }" % C_TEXT)
    catatan.setMinimumHeight(260)
    v.addWidget(catatan, 1)

    # footer
    bawah = QtWidgets.QHBoxLayout()
    bawah.setContentsMargins(18, 12, 18, 14)
    bawah.setSpacing(8)
    b_web = QtWidgets.QPushButton("Open release page...")
    b_web.setObjectName("BtnTiny")
    b_web.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
        QtCore.QUrl(info.get("page") or "")))
    bawah.addWidget(b_web)
    bawah.addStretch(1)
    b_nanti = QtWidgets.QPushButton("Not now")
    b_nanti.clicked.connect(d.reject)
    bawah.addWidget(b_nanti)
    b_upd = QtWidgets.QPushButton("Update now")
    b_upd.setObjectName("BtnPrimary")
    b_upd.clicked.connect(d.accept)
    bawah.addWidget(b_upd)
    v.addLayout(bawah)
    return bool(d.exec())


def _unduh_dengan_progres(w, url, dest):
    """Download `url` in a background thread with a working progress dialog.

    Returns (ok, error_message). Cancel = (False, None)."""
    from PySide6 import QtCore, QtWidgets

    prog = QtWidgets.QProgressDialog(w)
    prog.setWindowTitle("DeepVertex - update")
    prog.setLabelText("Downloading update...  connecting")
    prog.setCancelButtonText("Cancel")
    prog.setRange(0, 100)
    prog.setValue(0)
    prog.setMinimumDuration(0)
    prog.setAutoClose(False)
    prog.setAutoReset(False)
    prog.setMinimumWidth(470)
    prog.setWindowModality(QtCore.Qt.WindowModal)
    prog.show()
    QtWidgets.QApplication.processEvents()

    class _Unduh(QtCore.QThread):
        maju = QtCore.Signal(int, int)

        def __init__(self):
            super().__init__()
            self.batal = False
            self.err = None

        def run(self):
            try:
                download(self.url, dest,
                         lambda a, t: self.maju.emit(a, t),
                         batal=lambda: self.batal)
            except RuntimeError:
                self.err = None                     # cancelled by user
            except Exception as exc:                # noqa: BLE001
                self.err = str(exc)

    th = _Unduh()
    th.url = url

    def _maju(a, t):
        if t > 0:
            prog.setRange(0, 100)
            prog.setValue(min(100, int(a * 100.0 / t)))
            prog.setLabelText("Downloading update...  %.1f / %.1f MB"
                              % (a / 1e6, t / 1e6))
        else:
            prog.setRange(0, 0)                 # busy indicator
            prog.setLabelText("Downloading update...  %.1f MB" % (a / 1e6))

    th.maju.connect(_maju)
    prog.canceled.connect(lambda: setattr(th, "batal", True))
    th.start()
    while th.isRunning():
        QtWidgets.QApplication.processEvents()
        th.wait(40)
    prog.close()
    if getattr(th, "batal", False) and th.err is None:
        return False, None
    if th.err:
        return False, th.err
    return True, None


def _tanya_lalu_terapkan(w, info, v_lokal):
    from PySide6 import QtWidgets
    if not _diskusikan_update(w, info, v_lokal):
        return
    nama = os.path.basename(str(info.get("url_aset") or "")).lower()
    setup = "setup" in nama
    dest = os.path.join(_dir_update(),
                        "setup-baru.exe" if setup else "baru.exe")

    ok, err = _unduh_dengan_progres(w, info["url_aset"], dest)
    if err:
        QtWidgets.QMessageBox.warning(
            w, "DeepVertex - update",
            "Failed to download the update:\n%s" % err)
        return
    if not ok:
        QtWidgets.QMessageBox.information(w, "DeepVertex - update",
                                          "Update cancelled.")
        return
    try:
        if os.name == "nt":
            if getattr(sys, "frozen", False):
                exe = sys.executable
            else:
                exe = os.path.join(paths.APP, "installation",
                                   "DeepVertexMarineConsole.exe")
            _tulis_bat(dest, setup, exe,
                       target=str(info.get("tag", "")).lstrip("vV"))
        else:
            _tulis_sh(dest, os.path.dirname(paths.APP), os.getpid(),
                      target=str(info.get("tag", "")).lstrip("vV"))
    except Exception as exc:                               # noqa: BLE001
        QtWidgets.QMessageBox.warning(
            w, "DeepVertex - update",
            "Failed to apply the update:\n%s" % exc)
        return
    QtWidgets.QMessageBox.information(
        w, "DeepVertex - update",
        "Download complete — %s (%.0f MB).\n\nThe console will now CLOSE. "
        "The installer runs automatically and the app reopens by itself on "
        "the new version (about a minute).\n\nIf it does not reopen, run "
        "setup-baru.exe from:\n%s\n\n(Progress and any error are logged to "
        "apply_log.txt in that folder.)"
        % (info.get("tag", "?"), os.path.getsize(dest) / 1e6, _dir_update()))
    QtWidgets.QApplication.instance().quit()


def pratinjau(w, versi_lokal):
    """`--preview-update`: show the new dialog + a real (small) download so
    the progress bar can be reviewed. Nothing is installed."""
    from PySide6 import QtWidgets
    d = None
    try:
        d = fetch_latest(timeout=6)
    except Exception:                                      # noqa: BLE001
        d = None
    info = {
        "tag": "v9.9.9-preview",
        "notes": ("PREVIEW - this is what the update popup looks like when a "
                  "new version is published.\n\n"
                  "- \"Update now\" downloads in the background; the progress "
                  "bar and Cancel both work.\n"
                  "- Real updates show the release notes of the new version "
                  "in this area.\n"
                  "- Preview mode installs nothing."),
        "page": (d or {}).get("page")
        or "https://github.com/ZaydZaidan/deepvertex-console/releases",
        "url_aset": None,
    }
    if d:
        for nama, url in sorted((d.get("assets") or {}).items()):
            if nama.endswith("-linux.tar.gz"):
                info["url_aset"] = url
                break
        if not info["url_aset"]:
            info["url_aset"] = aset_url_untuk_platform(d.get("assets") or {})
    if not _diskusikan_update(w, info, versi_lokal):
        return
    if not info["url_aset"]:
        QtWidgets.QMessageBox.information(
            w, "DeepVertex - update preview",
            "Preview dialog shown. (Offline - no download demo.)")
        return
    dest = os.path.join(_dir_update(), "preview-download.tar.gz")
    ok, err = _unduh_dengan_progres(w, info["url_aset"], dest)
    if err:
        QtWidgets.QMessageBox.warning(
            w, "DeepVertex - update preview", "Download failed:\n%s" % err)
        return
    if not ok:
        QtWidgets.QMessageBox.information(w, "DeepVertex - update preview",
                                          "Cancelled.")
        return
    try:
        os.remove(dest)
    except OSError:
        pass
    QtWidgets.QMessageBox.information(
        w, "DeepVertex - update preview",
        "Preview finished.\n\nThis is exactly how a real update downloads "
        "(progress + cancel). Nothing was installed in preview mode.")


def periksa_di_latar(w, versi_lokal=None):
    """Call after show(): check releases in a background thread and, when a
    newer version exists, open the update dialog. Offline = silent."""
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
    w._upd_thread = t                    # keep a reference (anti GC)
    return t
