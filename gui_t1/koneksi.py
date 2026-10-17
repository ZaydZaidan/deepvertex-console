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
    },
    "sitl": {
        "nama": "Simulation / SITL (local)",
        "host": "127.0.0.1",
        "mavlink": "udpin:0.0.0.0:14550",
        "front_port": 5600, "left_port": 5601, "right_port": 5602,
        "raw_agent": "http://127.0.0.1:8765",
    },
}

IP_SARAN = "192.168.2.1 (laptop; official BlueRobotics tether setup)"
SUBNET_DEFAULT = "192.168.2.1/24"
NET_IP = "192.168.2.1"
NET_MASK = "255.255.255.0"


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


def network_steps(os_name, iface, ip=NET_IP, mask=NET_MASK):
    """Langkah set-up network RESMI BlueRobotics (static IP) per OS — laptop saja.

    Fungsi murni (diuji [55]); TIDAK menyentuh robot sama sekali. Resep:
    https://bluerobotics.com/learn/bluerov2-software-setup/#network-setup
    * Linux   : nmcli (NetworkManager) — jalan langsung, tanpa reboot.
    * Windows : netsh (lewat UAC); Control Panel = fallback manual.
    * macOS   : networksetup (lewat osascript admin).
    """
    if os_name == "nt":
        nm = '"%s"' % iface
        return {
            "apply": ["netsh interface ip set address name=%s static %s %s"
                      % (nm, ip, mask)],
            "verify": ["netsh", "interface", "ip", "show", "address",
                       "name=%s" % iface],
            "manual": ("Windows: Control Panel (NOT the Settings app) > "
                       "Network and Sharing Center > Change adapter settings > "
                       "right-click the FXTI adapter (Realtek USB FE Family "
                       "Controller) > Properties > Internet Protocol Version 4 "
                       "(TCP/IPv4) > Properties > Use the following IP "
                       "address: %s / %s") % (ip, mask),
        }
    if os_name == "darwin":
        return {
            "apply": ['networksetup -setmanual "%s" %s %s'
                      % (iface, ip, mask)],
            "verify": ["networksetup", "-getinfo", iface],
            "manual": ("macOS: System Settings > Network > the FXTI adapter "
                       "(USB 10/100 LAN) > Details... > TCP/IP > Configure "
                       "IPv4: Manually > %s / %s") % (ip, mask),
        }
    con = "DeepVertex Robot Tether"
    return {
        "apply": [
            "nmcli -t -f NAME,DEVICE con show   # find the profile bound to "
            + iface,
            "nmcli con add type ethernet ifname %s con-name '%s' 2>/dev/null "
            "|| true" % (iface, con),
            "nmcli con mod '%s' ipv4.method manual ipv4.addresses %s/24 "
            "ipv4.gateway '' ipv4.never-default yes && nmcli con up '%s'"
            % (con, ip, con)],
        "verify": ["ip", "-4", "-br", "addr", "show", iface],
        "manual": ("Linux: Settings > Network > “USB Ethernet” > gear "
                   "icon > IPv4 tab > Manual > Address %s, Netmask %s, "
                   "Gateway 0.0.0.0 > Apply") % (ip, mask),
    }


def parse_verify(os_name, teks, ip=NET_IP):
    """True bila output baca-alamat per-OS menunjukkan IP target terpasang."""
    t = str(teks or "")
    if not t:
        return False
    if os_name == "nt":
        for baris in t.splitlines():
            if "IP Address" in baris and ip in baris:
                return True
        return False
    return ip in t


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
def skrip_fix_firewall():
    """Isi skrip .ps1 pemulihan firewall (fungsi murni, diuji [54]).

    Masalah nyata (15 Okt): prompt Windows Defender yang pernah ditolak
    meninggalkan aturan BLOCK untuk exe kita — aturan BLOCK mengalahkan
    aturan allow apa pun, sehingga telemetri tak pernah masuk. Skrip ini
    (dijalankan lewat UAC) menghapus blok itu + memastikan allow UDP 14550.
    """
    return (
        "Get-NetFirewallRule | Where-Object { $_.DisplayName -like "
        "\"*deepvertex*\" -and $_.Action -eq \"Block\" } | "
        "Remove-NetFirewallRule -ErrorAction SilentlyContinue\r\n"
        "netsh advfirewall firewall add rule name=\"DeepVertex MAVLink "
        "UDP In\" dir=in action=allow protocol=UDP localport=14550 | "
        "Out-Null\r\n")


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
    form.addRow("Robot host (BlueOS)", f_host)
    form.addRow("MAVLink endpoint", f_mav)
    form.addRow("Video port — front", f_fr)
    form.addRow("Video port — stereo L", f_l)
    form.addRow("Video port — stereo R", f_r)
    form.addRow("RAW agent URL", f_raw)
    v.addLayout(form)
    v.addWidget(QtWidgets.QLabel(
        "Officially supported flow: set the laptop’s tether adapter to "
        "192.168.2.1 (section 2 — nothing on the robot is changed), then use "
        "the BlueOS web UI (192.168.2.2) for anything robot-side. RAW capture "
        "is optional and needs the onboard agent (install script in the repo)."))

    def isi(c):
        f_host.setText(str(c.get("host", "")))
        f_mav.setText(str(c.get("mavlink", "")))
        f_fr.setValue(int(c.get("front_port", 5600)))
        f_l.setValue(int(c.get("left_port", 5601)))
        f_r.setValue(int(c.get("right_port", 5602)))
        f_raw.setText(str(c.get("raw_agent", "")))

    aktif = [cfg0.get("preset", "blueos") if cfg0.get("preset") in rb
             else "custom"]

    def tarik():
        return {"preset": aktif[0], "host": f_host.text().strip(),
                "mavlink": f_mav.text().strip(),
                "front_port": f_fr.value(), "left_port": f_l.value(),
                "right_port": f_r.value(), "raw_agent": f_raw.text().strip()}

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

    # ---- 2) network setup (laptop saja — resep resmi BlueRobotics)
    v.addWidget(QtWidgets.QLabel(
        "<b>2. Robot network (laptop side)</b> — connect the tether cable, "
        "then give the laptop adapter the official address "
        "<b>192.168.2.1</b>. The robot itself is never modified."))
    eg = QtWidgets.QGridLayout()
    c_if = QtWidgets.QComboBox()
    c_if.setMinimumWidth(320)
    info_if = QtWidgets.QLabel("")
    info_if.setWordWrap(True)
    eg.addWidget(QtWidgets.QLabel("Interface"), 0, 0)
    eg.addWidget(c_if, 0, 1, 1, 4)
    eg.addWidget(info_if, 1, 1, 1, 4)
    ch_dhcp = QtWidgets.QCheckBox(
        "Use DHCP instead (rare — only if the robot runs a DHCP server)")
    ch_dhcp.setChecked(False)
    eg.addWidget(ch_dhcp, 2, 1, 1, 4)
    eg.addWidget(QtWidgets.QLabel("Static IP"), 3, 0)
    f_ip = QtWidgets.QLineEdit(SUBNET_DEFAULT)
    eg.addWidget(f_ip, 3, 1)
    b_net = QtWidgets.QPushButton("Set up network (official recipe)")
    b_net.setObjectName("BtnPrimary")
    b_man = QtWidgets.QPushButton("Manual steps…")
    eg.addWidget(b_net, 3, 2, 1, 2)
    eg.addWidget(b_man, 3, 4)
    lbl_net = QtWidgets.QLabel("")
    lbl_net.setObjectName("Faint")
    lbl_net.setWordWrap(True)
    eg.addWidget(lbl_net, 4, 1, 1, 4)
    b_copy = QtWidgets.QPushButton("Copy commands")
    b_copy.setObjectName("BtnTiny")
    b_vrf = QtWidgets.QPushButton("Verify")
    b_vrf.setObjectName("BtnTiny")
    baris_v = QtWidgets.QHBoxLayout()
    baris_v.addWidget(b_copy)
    baris_v.addWidget(b_vrf)
    baris_v.addStretch(1)
    eg.addWidget(QtWidgets.QLabel("Fallback"), 5, 0)
    eg.addLayout(baris_v, 5, 1, 1, 4)
    perintah_txt = QtWidgets.QPlainTextEdit()
    perintah_txt.setReadOnly(True)
    perintah_txt.setMaximumHeight(84)
    eg.addWidget(perintah_txt, 6, 0, 1, 5)
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
                                                "\n".join(p["cara"] if
                                                           "cara" in p else
                                                           p["cepat"])))

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
        console._catat("connection: network command copied")

    import subprocess

    def _platform_key():
        if os.name == "nt":
            return "nt"
        return "darwin" if sys.platform == "darwin" else "posix"

    def _tulis_ps1(nama, isi):
        try:
            import paths
            d = os.path.join(paths.data_dir(), "update")
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, nama)
            with open(p, "w", encoding="utf-8") as f:
                f.write(isi)
            return p
        except Exception:                                  # noqa: BLE001
            return None

    def _apply_linux(iface, ip):
        """nmcli: mod profil yang ada (atau buat baru), up — aktif langsung."""
        try:
            r = subprocess.run(["nmcli", "-t", "-f", "NAME,DEVICE", "con",
                                "show"], capture_output=True, text=True,
                               timeout=15)
            con = ""
            for baris in (r.stdout or "").splitlines():
                bagian = baris.split(":")
                if len(bagian) >= 2 and bagian[-1] == iface:
                    con = ":".join(bagian[:-1])
                    break
            if not con:
                con = "DeepVertex Robot Tether"
                subprocess.run(["nmcli", "con", "add", "type", "ethernet",
                                "ifname", iface, "con-name", con],
                               capture_output=True, text=True, timeout=20)
            r1 = subprocess.run(
                ["nmcli", "con", "mod", con, "ipv4.method", "manual",
                 "ipv4.addresses", "%s/24" % ip, "ipv4.gateway", "",
                 "ipv4.never-default", "yes"],
                capture_output=True, text=True, timeout=20)
            r2 = subprocess.run(["nmcli", "con", "up", con],
                                capture_output=True, text=True, timeout=25)
            ok = (r1.returncode == 0 and r2.returncode == 0)
            if ok:
                return True, ("OK — profile “%s” now static %s/24 and ACTIVE "
                              "(no reboot needed)" % (con, ip))
            pesan = ((r1.stderr or "") + " " + (r2.stderr or "")).strip()
            return False, ("nmcli failed: %s — use Manual steps… / Copy "
                           "commands" % (pesan[:110] or "permission?"))
        except Exception as exc:                           # noqa: BLE001
            return False, "nmcli error: %s" % exc

    def _set_network():
        iface = _iface_sekarang()
        if not iface:
            lbl_net.setText("Select the tether adapter first.")
            return
        ip = (f_ip.text().strip().split("/")[0] or NET_IP)
        plat = _platform_key()
        lbl_net.setStyleSheet("")
        if plat == "nt":
            st = network_steps("nt", iface, ip)
            p = _tulis_ps1("set_network.ps1",
                           "\r\n".join(["& " + st["apply"][0]]))
            if p:
                QtCore.QProcess.startDetached(
                    "powershell",
                    ["-NoProfile", "-Command",
                     "Start-Process powershell -Verb RunAs -ArgumentList "
                     "'-NoProfile','-ExecutionPolicy','Bypass','-File','%s'" % p])
                lbl_net.setText("applying %s (click YES on the UAC prompt) — "
                                "verifying in a few seconds…" % ip)
                QtCore.QTimer.singleShot(6000, _verify_network)
                QtCore.QTimer.singleShot(13000, _verify_network)
            else:
                lbl_net.setText("Could not write the helper script — use "
                                "Manual steps…")
            console._catat("connection: network set (Windows, UAC) — %s" % iface)
        elif plat == "darwin":
            st = network_steps("darwin", iface, ip)
            skrip = st["apply"][0].replace('"', '\\"')
            subprocess.Popen(["osascript", "-e",
                              'do shell script "%s" with administrator '
                              'privileges' % skrip])
            lbl_net.setText("applying %s (enter your password if asked) — "
                            "verifying in a few seconds…" % ip)
            QtCore.QTimer.singleShot(5000, _verify_network)
            QtCore.QTimer.singleShot(11000, _verify_network)
            console._catat("connection: network set (macOS) — %s" % iface)
        else:
            ok, msg = _apply_linux(iface, ip)
            lbl_net.setText(msg)
            lbl_net.setStyleSheet("color:#5CC98F;" if ok else "color:#E8804A;")
            console._catat("connection: network set (Linux) — " + msg)
            _verify_network()

    def _verify_network():
        iface = _iface_sekarang()
        ip = (f_ip.text().strip().split("/")[0] or NET_IP)
        plat = _platform_key()
        ada = False
        try:
            cmd = network_steps(plat, iface, ip)["verify"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            ada = parse_verify(plat, r.stdout, ip)
        except Exception:                                  # noqa: BLE001
            ada = False
        host = (f_host.text().strip() or "192.168.2.2")
        robot = False
        try:
            s = socket.create_connection((host, 80), timeout=1.5)
            s.close()
            robot = True
        except Exception:                                  # noqa: BLE001
            pass
        if ada and robot:
            txt = ("OK — adapter has %s/24 and the robot answers — network "
                   "ready. Settings saved; no restart needed." % ip)
            lbl_net.setStyleSheet("color:#5CC98F;")
            save(tarik())
            console._catat("connection: network verify OK (%s) — saved, "
                           "no restart" % ip)
        elif ada:
            txt = ("OK — adapter has %s/24 (robot not answering yet — check "
                   "ROV power / the tether cable). Settings saved; no restart "
                   "needed." % ip)
            lbl_net.setStyleSheet("color:#E8C15C;")
            save(tarik())
            console._catat("connection: network verify (adapter only) — saved")
        else:
            txt = ("Not applied yet — adapter does not show %s. Wait a few "
                   "seconds and press Verify, or use Manual steps…" % ip)
            lbl_net.setStyleSheet("color:#E8804A;")
        lbl_net.setText(txt)

    def _manual_steps():
        m = network_steps(_platform_key(), _iface_sekarang() or "<adapter>",
                          f_ip.text().strip().split("/")[0] or NET_IP)
        QtWidgets.QMessageBox.information(
            d, "Manual network steps (official BlueRobotics)",
            m["manual"] + "\n\nFull guide: "
            "https://bluerobotics.com/learn/bluerov2-software-setup/"
            "#network-setup\n\nThis only configures THIS laptop — nothing "
            "on the robot is changed.")

    b_copy.clicked.connect(_copy)
    b_net.clicked.connect(_set_network)
    b_vrf.clicked.connect(_verify_network)
    b_man.clicked.connect(_manual_steps)

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

    b_uji = QtWidgets.QPushButton("Test now")
    b_uji.clicked.connect(_uji)
    v.addWidget(b_uji)

    # ---- 4) simpan (restart hanya bila endpoint berubah)
    v.addWidget(QtWidgets.QLabel(
        "Network changes work immediately — just Save. Endpoint/port changes "
        "apply after Save & Restart."))
    bawah = QtWidgets.QHBoxLayout()
    b_tutup = QtWidgets.QPushButton("Close")
    b_tutup.clicked.connect(d.reject)
    b_simpan = QtWidgets.QPushButton("Save && Restart console")
    b_simpan2 = QtWidgets.QPushButton("Save (no restart)")
    b_simpan2.setObjectName("BtnPrimary")
    bawah.addStretch(1)
    bawah.addWidget(b_tutup)
    bawah.addWidget(b_simpan)
    bawah.addWidget(b_simpan2)
    v.addLayout(bawah)

    def _simpan_saja():
        if save(tarik()):
            console._catat("connection: settings saved (no restart)")
            d.accept()
        else:
            QtWidgets.QMessageBox.warning(d, "Connection",
                                          "Failed to save koneksi.json")

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
            # v1.0.16: relaunch via restart.bat (pola terbukti dari updater).
            # Bug 15 Okt: cmd /c "<mega-string dengan tanda kutip>&" dari
            # QProcess.startDetached ter-mangle Qt → cmd salah-parse →
            # "The network path was not found". Bat = satu argumen pendek.
            # Jeda ±4 dtk: biar pembersihan _MEI instance lama selesai dulu.
            for k in ("_MEIPASS2", "_PYI_APPLICATION_HOME_DIR",
                      "_PYI_ARCHIVE_FILE", "_PYI_PARENT_PROCESS_LEVEL",
                      "_PYI_SPLASH_IPC"):
                os.environ.pop(k, None)
            if os.name == "nt":
                try:
                    import paths
                    d = os.path.join(paths.data_dir(), "update")
                    os.makedirs(d, exist_ok=True)
                    bat = os.path.join(d, "restart.bat")
                    arg = " ".join('"%s"' % a if " " in a else a
                                   for a in args2)
                    with open(bat, "w", encoding="utf-8") as f:
                        f.write("@echo off\r\n"
                                "ping -n 5 127.0.0.1 >nul\r\n"
                                "start \"\" \"%s\" %s\r\n"
                                "del /q \"%%~f0\" >nul 2>nul\r\n"
                                % (prog, arg))
                    QtCore.QProcess.startDetached("cmd.exe", ["/c", bat])
                except Exception:                          # noqa: BLE001
                    QtCore.QProcess.startDetached(prog, args2)
            else:
                QtCore.QProcess.startDetached(prog, args2)
            console.close()

        # PENTING (bug 15 Okt): dialog modal HARUS ditutup SEBELUM console
        # ditutup — kalau tidak, loop modal menahan proses lama hidup dan
        # muncul DUA instance konsol yang berebut port/sesi.
        d.accept()
        QtCore.QTimer.singleShot(150, _lahirkan)

    b_simpan.clicked.connect(_simpan_restart)
    b_simpan2.clicked.connect(_simpan_saja)
    _isi_iface()
    _uji()
    d.exec()
    return d
