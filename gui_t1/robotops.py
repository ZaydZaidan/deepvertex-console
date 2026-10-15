"""robotops.py — SSH helpers to the robot (RAW agent: start + check).

Used by the Connection dialog for the one-click flow: when the RAW agent is
not answering, the console can SSH to the robot and start it, then re-test
automatically. Pure command builders are unit-tested [50]; the SSH part
needs `paramiko` (bundled in the installers; the module degrades gracefully
when paramiko is missing).
"""
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
