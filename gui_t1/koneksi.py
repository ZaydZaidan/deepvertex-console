"""koneksi.py — pengaturan koneksi konsol: BlueOS / ethernet tether / custom.

Tujuan (permintaan pemilik 15 Okt): dari setelan ethernet sampai konsol benar
benar bisa terhubung — dan tetap bisa dipakai TANPA BlueOS (endpoint custom).

Isi:
* preset: "blueos" (standar 192.168.2.x), "sitl" (loopback), "custom".
* simpan/muat `koneksi.json` di folder data user (paths.py); otomatis dipakai
  sebagai default saat konsol start (argumen CLI tetap menang).
* perintah ethernet siap-kopi per OS (nmcli/ip di Linux, netsh di Windows) —
  fungsi murni supaya bisa diuji tanpa jaringan [48].
* `args_terapan()` — menimpa opsi --mavlink/--port/--raw-agent pada argv
  (dipakai tombol "Simpan & Jalankan Ulang").
"""
import json
import os
import socket
import urllib.request

BENDA = "koneksi.json"

PRESET = {
    "blueos": {
        "nama": "BlueOS (standard)",
        "host": "192.168.2.2",
        "mavlink": "udpin:0.0.0.0:14550",
        "front_port": 5600, "left_port": 5601, "right_port": 5602,
        "raw_agent": "http://192.168.2.2:8765",
        "ssh_user": "pi", "ssh_pass": "", "agent_path": "~/onboard/raw_agent.py",
    },
    "sitl": {
        "nama": "Simulation / SITL (local)",
        "host": "127.0.0.1",
        "mavlink": "udpin:0.0.0.0:14550",
        "front_port": 5600, "left_port": 5601, "right_port": 5602,
        "raw_agent": "http://127.0.0.1:8765",
        "ssh_user": "pi", "ssh_pass": "", "agent_path": "~/onboard/raw_agent.py",
    },
}

IP_SARAN = "192.168.2.50    (laptop) — do not use .2 (that is BlueOS)"
SUBNET_DEFAULT = "192.168.2.50/24"


def preset(nama):
    """Salinan konfig preset (selalu dict baru)."""
    d = dict(PRESET.get(nama or "blueos", PRESET["blueos"]))
    d["preset"] = nama if nama in PRESET else "blueos"
    return d


def _path():
    try:
        import paths
        return paths.aset_json(BENDA)
    except Exception:                                      # noqa: BLE001
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), BENDA)


def load():
    """Konfigurasi tersimpan (atau None bila belum ada/rusak)."""
    try:
        with open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:                                      # noqa: BLE001
        return None


def save(cfg):
    try:
        p = _path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(dict(cfg), f, indent=2)
        return True
    except Exception:                                      # noqa: BLE001
        return False


# ------------------------------------------------------------- perintah OS
def perintah_ethernet(os_name, iface, ip=SUBNET_DEFAULT, dhcp=True):
    """Perintah siap-tempel untuk mengatur IP interface tether.

    Return {"cepat": [..], "permanen": [..], "catatan": str}
    - "cepat": sesi ini saja (hilang setelah reboot / cabut).
    - "permanen": tersimpan di NetworkManager (Linux) / permanen (Windows).
    - dhcp=True: memakai DHCP (banyak unit BlueOS menyalakan DHCP).
    """
    if os_name == "nt":
        na = '"%s"' % iface
        if dhcp:
            cepat = ["netsh interface ip set address name=%s dhcp" % na]
            permanen = list(cepat)
        else:
            cepat = ["netsh interface ip set address name=%s static %s 255.255.255.0"
                     % (na, ip.split("/")[0])]
            permanen = list(cepat)
        return {"cepat": cepat, "permanen": permanen,
                "catatan": "Run as Administrator (the Apply button uses UAC)."}
    # Linux (NetworkManager / iproute2)
    if dhcp:
        cepat = ["sudo nmcli device set %s managed yes" % iface,
                 "sudo nmcli con up %s 2>/dev/null || true  # use any existing profile" % iface]
        permanen = ["sudo nmcli con mod '%s' ipv4.method auto && sudo nmcli con up '%s'"
                    % (iface, iface)]
    else:
        cepat = ["sudo ip addr add %s dev %s 2>/dev/null || true" % (ip, iface),
                 "sudo ip link set %s up" % iface]
        permanen = ["sudo nmcli con mod '%s' ipv4.method manual ipv4.addresses %s ipv4.gateway ''"
                    % (iface, ip),
                    "sudo nmcli con up '%s'" % iface]
    return {"cepat": cepat, "permanen": permanen,
            "catatan": "Needs sudo (the terminal will ask). Check: ip -br addr show %s" % iface}


def args_terapan(cfg, argv):
    """Timpa --mavlink/--front-port/--left-port/--right-port/--raw-agent pada
    daftar argumen (argv[1:]); opsi lain dibiarkan."""
    ganti = {
        "--mavlink": str(cfg.get("mavlink", "udpin:0.0.0.0:14550")),
        "--front-port": str(int(cfg.get("front_port", 5600))),
        "--left-port": str(int(cfg.get("left_port", 5601))),
        "--right-port": str(int(cfg.get("right_port", 5602))),
        "--raw-agent": str(cfg.get("raw_agent", "http://192.168.2.2:8765")),
    }
    keluar, i, argv = [], 1, list(argv)
    while i < len(argv):
        a = argv[i]
        if a in ganti:
            if "=" in a:                       # bentuk --opt=value
                keluar.append("%s=%s" % (a.split("=", 1)[0], ganti[a.split("=", 1)[0]]))
                i += 1
                continue
            keluar.append(a)
            keluar.append(ganti[a])
            i += 2 if i + 1 < len(argv) and not argv[i + 1].startswith("--") else 1
            continue
        if a.startswith("--") and "=" in a and a.split("=", 1)[0] in ganti:
            keluar.append("%s=%s" % (a.split("=", 1)[0], ganti[a.split("=", 1)[0]]))
            i += 1
            continue
        keluar.append(a)
        i += 1
    for k, v in ganti.items():                 # tambahkan yang belum ada
        if k not in keluar and not any(x.startswith(k + "=") for x in keluar):
            keluar += [k, v]
    return keluar


# ------------------------------------------------------------------ ujicoba
def uji(host, port_video=5600, raw_url="", timeout=1.5):
    """Quick connection test; returns {"ssh","web","mavlink","raw"} messages.

    * ssh : TCP 22 (BlueOS/companion open?)
    * web : HTTP on port 80/8080 (BlueOS web UI alive?)
    * mavlink : informational — the console LISTENS on its MAVLink endpoint;
      we compute which local IP the robot would see, so it can be configured
      in BlueOS (video ports behave the same: UDP sent TO this laptop).
    * raw : GET <raw_url>/status (onboard RAW agent running?)
    """
    hasil = {}
    try:
        s = socket.create_connection((host, 22), timeout=timeout)
        s.close()
        hasil["ssh"] = "OK — %s:22 open (BlueOS/SSH)" % host
    except Exception as exc:                               # noqa: BLE001
        hasil["ssh"] = "not open — %s:22 (%s)" % (host, exc)
    for porta in (80, 8080):
        try:
            req = urllib.request.Request("http://%s:%d/" % (host, porta),
                                         headers={"User-Agent": "dv-connection"})
            with urllib.request.urlopen(req, timeout=timeout + 0.5) as r:
                hasil["web"] = "OK — BlueOS web answers on :%d (HTTP %d)" % (
                    porta, r.status)
            break
        except urllib.error.HTTPError as exc:              # answered (302/401…)
            hasil["web"] = "OK — BlueOS web answers on :%d (HTTP %d)" % (
                porta, exc.code)
            break
        except Exception as exc:                           # noqa: BLE001
            hasil.setdefault("web", None)
            hasil["web"] = "no web answer on :80/:8080 (%s)" % exc
    # MAVLink: console listens (UDP in); show the local IP the robot must use.
    ip_lokal = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, 14550))                           # no packets sent
        ip_lokal = s.getsockname()[0]
        s.close()
    except Exception:                                      # noqa: BLE001
        pass
    hasil["mavlink"] = ("console listens on 0.0.0.0:14550 — in BlueOS add a "
                        "MAVLink UDP client to %s:14550 (video: UDP to this "
                        "laptop, ports 5600 front / 5601 L / 5602 R)"
                        % (ip_lokal or "<this laptop IP>"))
    if raw_url:
        try:
            req = urllib.request.Request(raw_url.rstrip("/") + "/status",
                                         headers={"User-Agent": "dv-connection"})
            with urllib.request.urlopen(req, timeout=timeout + 1.0) as r:
                hasil["raw"] = "OK — RAW agent responded (HTTP %d)" % r.status
        except Exception as exc:                           # noqa: BLE001
            hasil["raw"] = ("no RAW agent — %s (%s). Start it on the robot: "
                            "python3 onboard/raw_agent.py --port 8765"
                            % (raw_url, exc))
    return hasil


# ------------------------------------------------------------------ dialog
def buat_dialog(console):
    """Dialog pengaturan koneksi (menu: Connection…). Dibuat on-demand."""
    import sys

    from PySide6 import QtCore, QtGui, QtWidgets
    try:
        from PySide6 import QtNetwork
    except Exception:                                      # noqa: BLE001
        QtNetwork = None

    cfg0 = load() or preset("blueos")

    d = QtWidgets.QDialog(console)
    d.setWindowTitle("Connection — DeepVertex Marine Console")
    d.setMinimumWidth(640)
    v = QtWidgets.QVBoxLayout(d)

    # ---- 1) pilihan preset
    v.addWidget(QtWidgets.QLabel(
        "<b>1. Connection preset</b> — BlueOS default, or your own robot."))
    baris = QtWidgets.QHBoxLayout()
    grp = QtWidgets.QButtonGroup(d)
    rb = {}
    for k, label in (("blueos", "BlueOS (192.168.2.x)"),
                     ("sitl", "Simulation / SITL (local)"),
                     ("custom", "Custom / other robot")):
        b = QtWidgets.QRadioButton(label)
        grp.addButton(b)
        rb[k] = b
        baris.addWidget(b)
    baris.addStretch(1)
    v.addLayout(baris)

    form = QtWidgets.QFormLayout()
    f_host = QtWidgets.QLineEdit()
    f_mav = QtWidgets.QLineEdit()
    f_fr, f_l, f_r = (QtWidgets.QSpinBox(), QtWidgets.QSpinBox(),
                      QtWidgets.QSpinBox())
    for sp in (f_fr, f_l, f_r):
        sp.setRange(1, 65535)
    f_raw = QtWidgets.QLineEdit()
    f_user = QtWidgets.QLineEdit()
    f_passw = QtWidgets.QLineEdit()
    f_passw.setEchoMode(QtWidgets.QLineEdit.Password)
    f_path = QtWidgets.QLineEdit()
    form.addRow("Robot host (BlueOS)", f_host)
    form.addRow("MAVLink endpoint", f_mav)
    form.addRow("Video port — front", f_fr)
    form.addRow("Video port — stereo L", f_l)
    form.addRow("Video port — stereo R", f_r)
    form.addRow("RAW agent URL", f_raw)
    form.addRow("Robot SSH user", f_user)
    form.addRow("Robot SSH password", f_passw)
    form.addRow("RAW agent path (on robot)", f_path)
    v.addLayout(form)
    v.addWidget(QtWidgets.QLabel(
        "SSH fields power the one-click “Start RAW agent”. For auto-start at "
        "every boot, run once on the robot: install/robot/"
        "install_agent_service.sh (kept in the repo)."))

    def isi(c):
        f_host.setText(str(c.get("host", "")))
        f_mav.setText(str(c.get("mavlink", "")))
        f_fr.setValue(int(c.get("front_port", 5600)))
        f_l.setValue(int(c.get("left_port", 5601)))
        f_r.setValue(int(c.get("right_port", 5602)))
        f_raw.setText(str(c.get("raw_agent", "")))
        f_user.setText(str(c.get("ssh_user", "pi")))
        f_passw.setText(str(c.get("ssh_pass", "")))
        f_path.setText(str(c.get("agent_path", "~/onboard/raw_agent.py")))

    aktif = [cfg0.get("preset", "blueos") if cfg0.get("preset") in rb
             else "custom"]

    def tarik():
        return {"preset": aktif[0], "host": f_host.text().strip(),
                "mavlink": f_mav.text().strip(),
                "front_port": f_fr.value(), "left_port": f_l.value(),
                "right_port": f_r.value(), "raw_agent": f_raw.text().strip(),
                "ssh_user": f_user.text().strip(), "ssh_pass": f_passw.text(),
                "agent_path": f_path.text().strip()}

    def pilih(k, pakai_cfg=False):
        aktif[0] = k
        if k in rb:
            rb[k].setChecked(True)
        if k in PRESET:
            isi(preset(k))
        elif pakai_cfg:
            isi(cfg0)

    for k, b in rb.items():
        b.toggled.connect(lambda on, k=k: on and pilih(k))
    pilih(aktif[0], pakai_cfg=True)

    # ---- 2) jaringan ethernet
    v.addWidget(QtWidgets.QLabel(
        "<b>2. Ethernet / tether network</b> — connect the tether cable, then "
        "use the robot subnet (BlueOS: 192.168.2.x)."))
    eg = QtWidgets.QGridLayout()
    c_if = QtWidgets.QComboBox()
    c_if.setMinimumWidth(320)
    info_if = QtWidgets.QLabel("")
    info_if.setWordWrap(True)
    eg.addWidget(QtWidgets.QLabel("Interface"), 0, 0)
    eg.addWidget(c_if, 0, 1, 1, 3)
    eg.addWidget(info_if, 1, 1, 1, 3)
    ch_dhcp = QtWidgets.QCheckBox("Use DHCP (recommended for most BlueOS rigs)")
    ch_dhcp.setChecked(True)
    eg.addWidget(ch_dhcp, 2, 1, 1, 3)
    eg.addWidget(QtWidgets.QLabel("Static IP"), 3, 0)
    f_ip = QtWidgets.QLineEdit(SUBNET_DEFAULT)
    eg.addWidget(f_ip, 3, 1)
    b_copy = QtWidgets.QPushButton("Copy command")
    b_apply = QtWidgets.QPushButton("Apply (admin)…")
    eg.addWidget(b_copy, 3, 2)
    eg.addWidget(b_apply, 3, 3)
    perintah_txt = QtWidgets.QPlainTextEdit()
    perintah_txt.setReadOnly(True)
    perintah_txt.setMaximumHeight(84)
    eg.addWidget(perintah_txt, 4, 0, 1, 4)
    v.addLayout(eg)

    def _iface_sekarang():
        nama = c_if.currentData() or ""
        return str(nama)

    def _segarkan_perintah(*_a):
        if not _iface_sekarang():
            perintah_txt.setPlainText("(no interface found)")
            return
        p = perintah_ethernet(os.name, _iface_sekarang(),
                              f_ip.text().strip() or SUBNET_DEFAULT,
                              dhcp=ch_dhcp.isChecked())
        perintah_txt.setPlainText("# %s\n%s" % (p["catatan"],
                                                "\n".join(p["cepat"])))

    ch_dhcp.toggled.connect(_segarkan_perintah)
    f_ip.textChanged.connect(_segarkan_perintah)
    c_if.currentIndexChanged.connect(_segarkan_perintah)

    def _isi_iface():
        c_if.clear()
        if QtNetwork is None:
            return

        def _enum(cls, scoped, plain):
            ns = getattr(cls, scoped, None)
            v = getattr(ns, plain, None) if ns is not None else None
            return v if v is not None else getattr(cls, plain, None)

        QNI = QtNetwork.QNetworkInterface
        IPv4 = _enum(QtNetwork.QAbstractSocket, "NetworkLayerProtocol",
                     "IPv4Protocol")
        F_LB = _enum(QNI, "InterfaceFlag", "IsLoopBack")
        F_UP = _enum(QNI, "InterfaceFlag", "IsUp")
        T_ETH = _enum(QNI, "InterfaceType", "Ethernet")
        T_WIFI = _enum(QNI, "InterfaceType", "Wifi")
        for ifa in QNI.allInterfaces():
            fl = ifa.flags()
            if F_LB is not None and (fl & F_LB):
                continue
            addrs = []
            for ae in ifa.addressEntries():
                if ae.ip().protocol() == IPv4:
                    addrs.append("%s/%d" % (ae.ip().toString(),
                                            ae.prefixLength()))
            tipe = {T_ETH: "eth", T_WIFI: "wifi"}.get(ifa.type(), "net")
            up = "up" if (F_UP is not None and (fl & F_UP)) else "down"
            c_if.addItem("%s [%s %s] %s" % (ifa.name(), tipe, up,
                                            ", ".join(addrs) or "no IPv4"),
                         ifa.name())
        _segarkan_perintah()

    def _copy():
        QtWidgets.QApplication.clipboard().setText(perintah_txt.toPlainText())
        console._catat("connection: ethernet command copied")

    def _apply_admin():
        if not _iface_sekarang():
            return
        p = perintah_ethernet(os.name, _iface_sekarang(),
                              f_ip.text().strip() or SUBNET_DEFAULT,
                              dhcp=ch_dhcp.isChecked())
        if os.name == "nt":
            kk = "__%s" % p["cepat"][0]
            script = ("& %s" % p["cepat"][0])          # netsh ...
            QtCore.QProcess.startDetached(
                "powershell",
                ["-NoProfile", "-Command",
                 "Start-Process powershell -Verb RunAs -ArgumentList "
                 "'-NoProfile','-Command','%s'" % script])
            console._catat("connection: applying IP (UAC) — %s" % kk)
        else:
            _copy()
            console._catat("connection: command copied — run it in a terminal "
                           "(needs sudo)")

    b_copy.clicked.connect(_copy)
    b_apply.clicked.connect(_apply_admin)

    # ---- 3) uji koneksi
    v.addWidget(QtWidgets.QLabel("<b>3. Test connection</b>"))
    hasil_lbl = {}
    for k, tag in (("ssh", "SSH / BlueOS"), ("web", "BlueOS web"),
                   ("mavlink", "MAVLink"), ("raw", "RAW agent")):
        lb = QtWidgets.QLabel("—")
        lb.setObjectName("Faint")
        lb.setWordWrap(True)
        baris2 = QtWidgets.QHBoxLayout()
        t = QtWidgets.QLabel(tag)
        t.setFixedWidth(96)
        baris2.addWidget(t)
        baris2.addWidget(lb, 1)
        v.addLayout(baris2)
        hasil_lbl[k] = lb

    def _uji():
        c = tarik()
        host = c["host"] or "192.168.2.2"
        hasil_lbl["ssh"].setText("testing %s …" % host)
        QtWidgets.QApplication.processEvents()
        h = uji(host, c["front_port"], c["raw_agent"])
        for k in ("ssh", "web", "mavlink", "raw"):
            msg = h.get(k, "—")
            hasil_lbl[k].setText(msg)
            if msg.startswith("OK"):
                hasil_lbl[k].setStyleSheet("color:#5CC98F;")
            elif k == "mavlink":
                hasil_lbl[k].setStyleSheet("")       # informational
            else:
                hasil_lbl[k].setStyleSheet("color:#E8804A;")
        # one-click: RAW gagal + kredensial terisi → nyalakan agent di robot
        if not h.get("raw", "").startswith("OK") and \
                f_user.text().strip() and f_passw.text():
            lbl_ssh.setText("RAW agent is not up — starting it on the robot …")
            QtWidgets.QApplication.processEvents()
            if _start_agent(silent=True):
                hasil_lbl["raw"].setText(
                    "OK — RAW agent started automatically ✓")
                hasil_lbl["raw"].setStyleSheet("color:#5CC98F;")

    b_uji = QtWidgets.QPushButton("Test now")
    b_uji.clicked.connect(_uji)
    v.addWidget(b_uji)

    baris_ssh = QtWidgets.QHBoxLayout()
    b_start = QtWidgets.QPushButton("Start RAW agent on the robot")
    b_start.setObjectName("BtnTiny")
    lbl_ssh = QtWidgets.QLabel("")
    lbl_ssh.setObjectName("Faint")
    lbl_ssh.setWordWrap(True)
    baris_ssh.addWidget(b_start)
    baris_ssh.addWidget(lbl_ssh, 1)
    v.addLayout(baris_ssh)

    def _start_agent(silent=False):
        c = tarik()
        host = c["host"] or "192.168.2.2"
        u, p = c["ssh_user"], c["ssh_pass"]
        if not u or not p:
            if not silent:
                lbl_ssh.setText("fill the robot SSH user + password first")
            return False
        if not silent:
            lbl_ssh.setText("starting the agent on %s …" % host)
            QtWidgets.QApplication.processEvents()
        try:
            import robotops
            ok, msg = robotops.start_agent(host, u, p, c["agent_path"])
        except Exception as exc:                           # noqa: BLE001
            ok, msg = False, "robotops error: %s" % exc
        lbl_ssh.setText(msg)
        return ok

    def _start_dan_uji():
        if _start_agent():
            _uji()

    b_start.clicked.connect(_start_dan_uji)

    # ---- 4) simpan & jalankan ulang
    v.addWidget(QtWidgets.QLabel(
        "Changing endpoints restarts the console with the new connection."))
    bawah = QtWidgets.QHBoxLayout()
    b_tutup = QtWidgets.QPushButton("Close")
    b_tutup.clicked.connect(d.reject)
    b_simpan = QtWidgets.QPushButton("Save && Restart console")
    b_simpan.setObjectName("BtnPrimary")
    bawah.addStretch(1)
    bawah.addWidget(b_tutup)
    bawah.addWidget(b_simpan)
    v.addLayout(bawah)

    def _simpan_restart():
        c = tarik()
        if not save(c):
            QtWidgets.QMessageBox.warning(d, "Connection",
                                          "Failed to save koneksi.json")
            return
        argv_baru = args_terapan(c, sys.argv)
        if getattr(sys, "frozen", False):
            prog, args2 = sys.executable, argv_baru
        else:
            prog, args2 = sys.executable, [sys.argv[0]] + argv_baru
        console._catat("connection: saved — restarting the console")

        def _lahirkan():
            # v1.0.10: relaunch DITUNDA ±4 dtk. Bug 15 Okt: folder ekstraksi
            # onefile = _MEI<hex-PID>; PID Windows cepat didaur ulang, jadi
            # instance baru dapat PID bekas instance lama → ekstrak ke folder
            # yang SEDANG dihapus oleh bootloader lama → crash
            # "numpy\_core tidak ditemukan". Jeda = biar pembersihan selesai.
            # (Pop env PyInstaller = higiene tambahan; hasil uji: bukan faktor
            #  penentu di build ini, tapi aman.)
            for k in ("_MEIPASS2", "_PYI_APPLICATION_HOME_DIR",
                      "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL",
                      "_PYI_SPLASH_IPC"):
                os.environ.pop(k, None)
            if os.name == "nt":
                arg = " ".join('"%s"' % a if " " in a else a for a in args2)
                baris = ('ping -n 5 127.0.0.1 >nul & start "" "%s" %s'
                         % (prog, arg))
                QtCore.QProcess.startDetached("cmd", ["/c", baris])
            else:
                QtCore.QProcess.startDetached(prog, args2)
            console.close()

        # PENTING (bug 15 Okt): dialog modal HARUS ditutup SEBELUM console
        # ditutup — kalau tidak, loop modal menahan proses lama hidup dan
        # muncul DUA instance konsol yang berebut port/sesi.
        d.accept()
        QtCore.QTimer.singleShot(150, _lahirkan)

    b_simpan.clicked.connect(_simpan_restart)
    _isi_iface()
    _uji()
    d.exec()
    return d
