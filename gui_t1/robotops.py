"""robotops.py — SSH helpers to the robot (RAW agent: start + check).

Used by the Connection dialog for the one-click flow: when the RAW agent is
not answering, the console can SSH to the robot and start it, then re-test
automatically. Pure command builders are unit-tested [50]; the SSH part
needs `paramiko` (bundled in the installers; the module degrades gracefully
when paramiko is missing).
"""
import json
import time
import urllib.request

try:
    import paramiko
except Exception:                                          # noqa: BLE001
    paramiko = None  # noqa: N816


def agent_status(host, port=8765, timeout=1.5):
    """True when the RAW agent answers /status."""
    try:
        req = urllib.request.Request(
            "http://%s:%d/status" % (host, port),
            headers={"User-Agent": "dv-robotops"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:                                      # noqa: BLE001
        return False


def perintah_start(path_agent, port=8765):
    """Shell command that (re)starts the agent detached on the robot."""
    return ("bash -lc 'pkill -f raw_agent.py 2>/dev/null; "
            "nohup python3 %s --port %d > /tmp/dv-raw-agent.log 2>&1 & "
            "echo STARTED'" % (path_agent, port))


def perintah_cari(path_agent):
    """Print the agent path (argument if it exists, else search common spots)."""
    return ("bash -lc 'if [ -f %s ]; then echo %s; else "
            "find $HOME /opt /srv -maxdepth 4 -name raw_agent.py "
            "-print -quit 2>/dev/null; fi'" % (path_agent, path_agent))


def ssh_exec(host, user, password, perintah, timeout=8):
    """Run one command over SSH. Returns (rc, stdout, stderr)."""
    if paramiko is None:
        return 127, "", "paramiko module not available in this build"
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(host, username=user, password=password, timeout=timeout,
                  banner_timeout=timeout, auth_timeout=timeout,
                  look_for_keys=False, allow_agent=False)
        _i, o, e = c.exec_command(perintah, timeout=timeout)
        rc = o.channel.recv_exit_status()
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        return rc, out, err
    except Exception as exc:                               # noqa: BLE001
        return 255, "", str(exc)
    finally:
        try:
            c.close()
        except Exception:                                  # noqa: BLE001
            pass


def start_agent(host, user, password, path_agent, port=8765):
    """One-click: SSH to the robot -> locate the agent -> start it -> wait
    for /status. Returns (ok, message)."""
    if paramiko is None:
        return False, ("paramiko not available in this build — start the "
                       "agent manually on the robot")
    rc, out, err = ssh_exec(host, user, password, perintah_cari(path_agent))
    if rc != 0:
        return False, "SSH to %s failed: %s" % (host, (err or "rc=%d" % rc).strip())
    jalur = ""
    for baris in reversed((out or "").strip().splitlines()):
        b = baris.strip()
        if b.endswith("raw_agent.py") or b.endswith(" / raw_agent.py"):
            jalur = b.split()[-1] if " " in b else b
            break
    if not jalur:
        return False, ("raw_agent.py not found on the robot (looked at %s and "
                       "common folders) — set the correct path in the dialog"
                       % path_agent)
    if agent_status(host, port, timeout=1.5):
        return True, "agent already running at :%d" % port
    rc, out, err = ssh_exec(host, user, password,
                            perintah_start(jalur, port))
    if rc != 0 and "STARTED" not in (out or ""):
        return False, "failed to start: %s" % (err or "rc=%d" % rc).strip()
    for _ in range(6):
        time.sleep(1.0)
        if agent_status(host, port, timeout=1.5):
            return True, "agent started ✓ (%s)" % jalur
    return False, ("command sent but /status did not answer — check "
                   "/tmp/dv-raw-agent.log on the robot")


# ------------------------------------------------- BlueOS API (one-click connect)
# Dipelajari dari robot asli (15 Okt, BlueOS ardupilot-manager):
#   GET  http://<host>/ardupilot-manager/v1.0/endpoints/   -> daftar endpoint
#   POST http://<host>/ardupilot-manager/v1.0/endpoints/   -> body = [ {objek} ]
#   objek: name, owner="User", connection_type ("udpin"/"udpout"/"tcpin"/...),
#          place (ip), argument (port), persistent, enabled
def blueos_api(host):
    return "http://%s/ardupilot-manager/v1.0/endpoints/" % host


def endpoint_ada(daftar, ip, port=14550):
    """True bila sudah ada endpoint udpout aktif ke ip:port (fungsi murni)."""
    for e in daftar or []:
        try:
            if (str(e.get("connection_type", "")).lower() == "udpout"
                    and str(e.get("place", "")) == str(ip)
                    and int(e.get("argument", 0)) == int(port)
                    and e.get("enabled", True)):
                return True
        except Exception:                                  # noqa: BLE001
            continue
    return False


def blueos_daftar(host, timeout=5):
    """Daftar endpoint BlueOS (list of dict) atau raises."""
    req = urllib.request.Request(blueos_api(host),
                                 headers={"User-Agent": "dv-robotops"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def blueos_pastikan_endpoint(host, ip_lokal, port=14550, nama=None,
                             timeout=8):
    """Pastikan robot mengirim MAVLink ke ip_lokal:port (one-click).

    Returns (ok, pesan). Idempoten: bila endpoint yang cocok sudah ada,
    tidak menambah apa pun."""
    nama = nama or ("DeepVertex Console (%s)" % ip_lokal)
    try:
        daftar = blueos_daftar(host, timeout=timeout)
    except Exception as exc:                               # noqa: BLE001
        return False, "BlueOS API tidak menjawab di %s (%s)" % (host, exc)
    if endpoint_ada(daftar, ip_lokal, port):
        return True, "endpoint MAVLink sudah ada (udpout -> %s:%d)" % (
            ip_lokal, port)
    badan = json.dumps([{
        "name": nama, "owner": "User", "connection_type": "udpout",
        "place": str(ip_lokal), "argument": int(port),
        "persistent": True, "enabled": True}]).encode()
    req = urllib.request.Request(blueos_api(host), data=badan, method="POST",
                                 headers={"User-Agent": "dv-robotops",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            kode = r.status
    except Exception as exc:                               # noqa: BLE001
        return False, "gagal menambah endpoint: %s" % exc
    if 200 <= kode < 300:
        return True, "endpoint MAVLink dibuat (udpout -> %s:%d) ✓" % (
            ip_lokal, port)
    return False, "endpoint ditolak (HTTP %d)" % kode


def ip_ke_robot(host, port=14550):
    """IP lokal yang akan dilihat robot (trik connect UDP tanpa kirim paket)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((host, port))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:                                      # noqa: BLE001
        return None


def tunggu_link(bridge, maks_detik=6.0):
    """Tunggu state link konsol hidup; True bila telemetri masuk."""
    t0 = time.time()
    while time.time() - t0 < maks_detik:
        try:
            if bridge is not None and bridge.state.get("link"):
                return True
        except Exception:                                  # noqa: BLE001
            pass
        time.sleep(0.4)
    return False
