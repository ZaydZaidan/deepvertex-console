"""rawcap.py — klien + panel kontrol perekam RAW onboard (HTTP, agen: onboard/raw_agent.py).

Alur: start/stop rekam RAW di Pi → daftar chunk → transfer ke laptop (unduh +
verifikasi sha256) → hapus yang sudah terverifikasi. Semua panggilan HTTP async
(ThreadPoolExecutor); hasil dikirim lewat sinyal Qt.
"""
import concurrent.futures
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from PySide6 import QtCore, QtWidgets

import theme


class RawClient(QtCore.QObject):
    sig_result = QtCore.Signal(str, object)      # tag, dict hasil
    sig_progress = QtCore.Signal(str, float)     # nama file, frac 0..1

    def __init__(self, base="http://127.0.0.1:8765", token=None, parent=None):
        super().__init__(parent)
        self.base = base.rstrip("/")
        self.token = token
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    # ------------------------------------------------------------------ HTTP
    def _req(self, method, path, body=None, timeout=15, stream_to=None):
        url = self.base + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["X-Token"] = self.token
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        if stream_to:
            with urllib.request.urlopen(req, timeout=timeout) as r, open(stream_to, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    blk = r.read(262144)
                    if not blk:
                        break
                    f.write(blk)
                    got += len(blk)
                    if total:
                        self.sig_progress.emit(os.path.basename(stream_to), got / float(total))
            return {"ok": True, "path": stream_to, "size": got}
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def _async(self, tag, method, path, **kw):
        fut = self.pool.submit(self._req, method, path, **kw)

        def _done(f):
            try:
                self.sig_result.emit(tag, f.result())
            except Exception as exc:                       # noqa: BLE001
                self.sig_result.emit(tag, {"ok": False, "error": str(exc)})
        fut.add_done_callback(_done)

    # ------------------------------------------------------------------- API
    def status(self):
        self._async("status", "GET", "/status")

    def start(self, cam, chunk_mb=None):
        body = {"cam": cam}
        if chunk_mb:
            body["chunk_mb"] = int(chunk_mb)
        self._async("start", "POST", "/capture/start", body=body)

    def stop(self, cam):
        self._async("stop", "POST", "/capture/stop", body={"cam": cam})

    def files(self):
        self._async("files", "GET", "/files")

    def delete(self, names):
        self._async("delete", "POST", "/delete", body={"names": list(names)})

    def transfer(self, rel, dest_dir):
        """Unduh + verifikasi sha256 terhadap agen. Hasil: tag 'transfer'."""
        def kerja():
            dest = os.path.join(dest_dir, os.path.basename(rel))
            r = self._req("GET", "/download/" + urllib.parse.quote(rel),
                          stream_to=dest, timeout=1800)
            h = hashlib.sha256()
            with open(dest, "rb") as f:
                for blk in iter(lambda: f.read(1048576), b""):
                    h.update(blk)
            lokal = h.hexdigest()
            j = self._req("GET", "/sha256/" + urllib.parse.quote(rel))
            return {"ok": True, "rel": rel, "path": dest, "size": r["size"],
                    "sha_lokal": lokal, "sha_agen": j.get("sha256"),
                    "cocok": lokal == j.get("sha256")}
        fut = self.pool.submit(kerja)

        def _done(f):
            try:
                self.sig_result.emit("transfer", f.result())
            except Exception as exc:                       # noqa: BLE001
                self.sig_result.emit("transfer", {"ok": False, "rel": rel,
                                                  "error": str(exc)})
        fut.add_done_callback(_done)


class RawPanel(QtWidgets.QWidget):
    """Panel kontrol RAW (ditempatkan di dalam W.Panel oleh pemanggil)."""

    def __init__(self, agent="http://127.0.0.1:8765", outdir=".", parent=None):
        super().__init__(parent)
        self.outdir = outdir
        self.client = RawClient(agent, parent=self)
        self.files_info = {}                                # rel -> size
        self.verified = {}                                  # rel -> path lokal

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.lbl = QtWidgets.QLabel("RAW · not connected to the agent "
                                    "(uncompressed capture)")
        self.lbl.setObjectName("MicroLabel")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        self.lbl_disk = QtWidgets.QLabel("—")
        self.lbl_disk.setObjectName("Faint")
        lay.addWidget(self.lbl_disk)

        # Baris 1: pilih kamera RAW. Baris 2: STOP + ukuran chunk.
        # (audit 13 Okt: satu baris panjang = minW 602 px > viewport rel 442 = TERKLIP;
        #  dipecah dua baris supaya panel muat utuh di rel kanan.)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        self.btn = {}
        for key, txt, tip, cam in (
                ("L", "RAW L", "start RAW recording — stereo LEFT (B0495C)", "left"),
                ("R", "RAW R", "start RAW recording — stereo RIGHT (B0495C)", "right"),
                ("both", "L+R", "start RAW recording — both stereo cameras", "both"),
                ("front", "RAW F", "start RAW recording — FRONT camera "
                 "(uncompressed, ≤640×480 over USB2)", "front")):
            b = QtWidgets.QPushButton(txt)
            b.setObjectName("BtnTiny")
            b.setToolTip(tip)
            b.clicked.connect(lambda _, k=key: self.client.start(k, self.sp_chunk.value()))
            row.addWidget(b)
            self.btn[key] = b
        row.addStretch(1)
        lay.addLayout(row)

        row1b = QtWidgets.QHBoxLayout()
        row1b.setSpacing(6)
        b = QtWidgets.QPushButton("STOP")
        b.setObjectName("BtnTiny")
        b.setToolTip("stop ALL RAW recording (every camera)")
        b.clicked.connect(lambda: self.client.stop("both"))
        row1b.addWidget(b)
        b = QtWidgets.QPushButton("STOP F")
        b.setObjectName("BtnTiny")
        b.setToolTip("stop RAW recording on the FRONT camera")
        b.clicked.connect(lambda: self.client.stop("front"))
        row1b.addWidget(b)
        self.sp_chunk = QtWidgets.QSpinBox()
        self.sp_chunk.setRange(64, 8192)
        self.sp_chunk.setValue(1024)
        self.sp_chunk.setSuffix(" MB/chunk")
        self.sp_chunk.setFixedWidth(theme.px(118))
        self.sp_chunk.setToolTip("chunk size for the RAW files written on the Pi")
        row1b.addWidget(self.sp_chunk)
        row1b.addStretch(1)
        lay.addLayout(row1b)

        self.list = QtWidgets.QListWidget()
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.setMinimumHeight(110)
        lay.addWidget(self.list, 1)

        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(6)
        b = QtWidgets.QPushButton("REFRESH")
        b.setObjectName("BtnTiny")
        b.clicked.connect(self.client.files)
        row2.addWidget(b)
        b = QtWidgets.QPushButton("TRANSFER ►")
        b.setObjectName("BtnTiny")
        b.setToolTip("download the selected chunks into this session's raw/ folder "
                     "+ sha256 verify")
        b.clicked.connect(self._transfer)
        row2.addWidget(b)
        b = QtWidgets.QPushButton("DELETE ✓")
        b.setObjectName("BtnTiny")
        b.setToolTip("delete on the Pi ALL files already verified on the laptop")
        b.clicked.connect(self._hapus)
        row2.addWidget(b)
        row2.addStretch(1)
        lay.addLayout(row2)
        self.prog = QtWidgets.QLabel("")
        self.prog.setObjectName("Faint")
        self.prog.setWordWrap(True)
        lay.addWidget(self.prog)

        # wiring
        self.client.sig_result.connect(self._hasil)
        self.client.sig_progress.connect(
            lambda nama, frac: self.prog.setText(f"{nama}: {frac * 100:.0f}%"))
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.client.status)
        self._timer.start(10000)
        self.client.status()
        self.client.files()          # muat daftar chunk langsung (isi panel sejak awal)

    # ------------------------------------------------------------------ aksi
    def _transfer(self):
        items = self.list.selectedItems()
        if not items:
            self.prog.setText("select a file in the list first")
            return
        for it in items:
            self.client.transfer(it.data(QtCore.Qt.ItemDataRole.UserRole), self.outdir)

    def _hapus(self):
        if not self.verified:
            self.prog.setText("no verified files to delete yet")
            return
        names = list(self.verified.keys())
        self.prog.setText(f"deleting {len(names)} verified files on the Pi …")
        self.client.delete(names)

    # ------------------------------------------------------------------ hasil
    def _hasil(self, tag, d):
        if not isinstance(d, dict):
            d = {"ok": False, "error": str(d)}
        if tag == "status":
            if d.get("ok"):
                caps = d.get("captures") or {}
                aktif = ", ".join(k for k, v in caps.items() if isinstance(v, dict) and v.get("aktif"))
                self.lbl.setText(f"RAW agent OK ({'test' if d.get('test') else 'device'}) — "
                                 f"active capture: {aktif or 'none'}")
                self.lbl_disk.setText(f"Pi free: {d.get('disk_free_mb', '?')} MB "
                                      f"(min {d.get('min_free_mb', '?')} MB) — Pi dir "
                                      f"{d.get('dir', '?')}  →  transfer pulls into this "
                                      f"session's raw/")
            else:
                self.lbl.setText("RAW agent unreachable")
                self.lbl.setToolTip(str(d.get("error", "")))
        elif tag == "files":
            if d.get("ok"):
                self.files_info = {f["path"]: f["size"] for f in d.get("files", [])}
                self.list.clear()
                for rel, size in sorted(self.files_info.items()):
                    it = QtWidgets.QListWidgetItem(f"{rel}   ({size / 1e6:.1f} MB)")
                    it.setData(QtCore.Qt.ItemDataRole.UserRole, rel)
                    if rel in self.verified:
                        it.setText(it.text() + "  ✓ verified")
                    self.list.addItem(it)
            else:
                self.prog.setText(f"listing files failed: {d.get('error', '')[:90]}")
        elif tag in ("start", "stop"):
            self.prog.setText(f"{tag}: {'ok' if d.get('ok') else d.get('error', 'failed')}")
            self.client.status()
            self.client.files()
        elif tag == "transfer":
            if d.get("ok") and d.get("cocok"):
                self.verified[d["rel"]] = d["path"]
                self.prog.setText(f"✓ {os.path.basename(d['rel'])} terverifikasi → {d['path']}")
                self.client.files()
            elif d.get("ok"):
                self.prog.setText(f"✗ {d.get('rel', '')}: sha MISMATCH — do not delete on the Pi!")
            else:
                self.prog.setText(f"✗ transfer failed: {d.get('error', '')[:90]}")
        elif tag == "delete":
            if d.get("ok"):
                self.prog.setText("delete finished — " + str(d.get("hasil", ""))[:120])
                self.verified.clear()
                self.client.files()
            else:
                self.prog.setText(f"delete failed: {d.get('error', '')[:90]}")
