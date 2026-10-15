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
        "nama": "BlueOS (standar)",
        "host": "192.168.2.2",
        "mavlink": "udpin:0.0.0.0:14550",
        "front_port": 5600, "left_port": 5601, "right_port": 5602,
        "raw_agent": "http://192.168.2.2:8765",
    },
    "sitl": {
        "nama": "Simulasi / SITL (lokal)",
        "host": "127.0.0.1",
        "mavlink": "udpin:0.0.0.0:14550",
        "front_port": 5600, "left_port": 5601, "right_port": 5602,
        "raw_agent": "http://127.0.0.1:8765",
    },
}

IP_SARAN = "192.168.2.50    (laptop) — jangan pakai .2 (itu BlueOS)"
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
                "catatan": "Jalankan sebagai Administrator (tombol Terapkan memakai UAC)."}
    # Linux (NetworkManager / iproute2)
    if dhcp:
        cepat = ["sudo nmcli device set %s managed yes" % iface,
                 "sudo nmcli con up %s 2>/dev/null || true  # pakai profil apa pun" % iface]
        permanen = ["sudo nmcli con mod '%s' ipv4.method auto && sudo nmcli con up '%s'"
                    % (iface, iface)]
    else:
        cepat = ["sudo ip addr add %s dev %s 2>/dev/null || true" % (ip, iface),
                 "sudo ip link set %s up" % iface]
        permanen = ["sudo nmcli con mod '%s' ipv4.method manual ipv4.addresses %s ipv4.gateway ''"
                    % (iface, ip),
                    "sudo nmcli con up '%s'" % iface]
    return {"cepat": cepat, "permanen": permanen,
            "catatan": "Butuh sudo (diminta oleh terminal). Cek: ip -br addr show %s" % iface}


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
    """Uji sambungan cepat; return dict {'ping','video','raw'} berisi pesan."""
    hasil = {}
    # ping = resolusi + TCP ke port video
    try:
        s = socket.create_connection((host, int(port_video)), timeout=timeout)
        s.close()
        hasil["video"] = "OK — %s:%d menerima TCP" % (host, port_video)
    except Exception as exc:                               # noqa: BLE001
        hasil["video"] = "GAGAL — %s:%d (%s)" % (host, port_video, exc)
    try:
        s = socket.create_connection((host, 22), timeout=timeout)
        s.close()
        hasil["ssh"] = "OK — %s:22 (BlueOS/SSH terbuka)" % host
    except Exception as exc:                               # noqa: BLE001
        hasil["ssh"] = "tidak terbuka — %s:22 (%s)" % (host, exc)
    if raw_url:
        try:
            req = urllib.request.Request(raw_url.rstrip("/") + "/status",
                                         headers={"User-Agent": "dv-koneksi"})
            with urllib.request.urlopen(req, timeout=timeout + 1.0) as r:
                hasil["raw"] = "OK — agen RAW menjawab (HTTP %d)" % r.status
        except Exception as exc:                           # noqa: BLE001
            hasil["raw"] = "GAGAL — %s (%s)" % (raw_url, exc)
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
    form.addRow("Robot host (BlueOS)", f_host)
    form.addRow("MAVLink endpoint", f_mav)
    form.addRow("Video port — front", f_fr)
    form.addRow("Video port — stereo L", f_l)
    form.addRow("Video port — stereo R", f_r)
    form.addRow("RAW agent URL", f_raw)
    v.addLayout(form)

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
        console._catat("koneksi: perintah ethernet disalin")

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
            console._catat("koneksi: menerapkan IP (UAC) — %s" % kk)
        else:
            _copy()
            console._catat("koneksi: perintah disalin — jalankan di terminal "
                           "(perlu sudo)")

    b_copy.clicked.connect(_copy)
    b_apply.clicked.connect(_apply_admin)

    # ---- 3) uji koneksi
    v.addWidget(QtWidgets.QLabel("<b>3. Test connection</b>"))
    hasil_lbl = {}
    for k, tag in (("ssh", "SSH/BlueOS"), ("video", "Video port"),
                   ("raw", "RAW agent")):
        lb = QtWidgets.QLabel("—")
        lb.setObjectName("Faint")
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
        hasil_lbl["ssh"].setText("menguji %s …" % host)
        QtWidgets.QApplication.processEvents()
        h = uji(host, c["front_port"], c["raw_agent"])
        for k in ("ssh", "video", "raw"):
            hasil_lbl[k].setText(h.get(k, "—"))
            hasil_lbl[k].setStyleSheet(
                "color:#5CC98F;" if h.get(k, "").startswith("OK")
                else "color:#E8804A;")

    b_uji = QtWidgets.QPushButton("Test now")
    b_uji.clicked.connect(_uji)
    v.addWidget(b_uji)

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
                                          "Gagal menyimpan koneksi.json")
            return
        argv_baru = args_terapan(c, sys.argv)
        if getattr(sys, "frozen", False):
            QtCore.QProcess.startDetached(sys.executable, argv_baru)
        else:
            QtCore.QProcess.startDetached(sys.executable,
                                          [sys.argv[0]] + argv_baru)
        console._catat("koneksi: disimpan — konsol dijalankan ulang")
        console.close()

    b_simpan.clicked.connect(_simpan_restart)
    _isi_iface()
    _uji()
    d.exec()
    return d
