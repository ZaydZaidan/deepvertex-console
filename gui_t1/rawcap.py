"""rawcap.py — klien + panel kontrol perekam RAW onboard (HTTP, agen: onboard/raw_agent.py).

Alur: start/stop rekam RAW di Pi → daftar chunk → transfer ke laptop (unduh +
verifikasi sha256) → hapus yang sudah terverifikasi. Semua panggilan HTTP async
(ThreadPoolExecutor); hasil dikirim lewat sinyal Qt.
"""
import concurrent.futures
import hashlib
import json
import os
import time
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

    def snap(self, cam):
        """Satu frame RAW di robot (muncul di /files → transfer)."""
        self._async("snap", "POST", "/snap", body={"cam": cam})

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
        self.last_status = {}                               # status terakhir agen
        self._prev_free = None                              # (t, free_mb) Pi
        self._rate_mbpm = 0.0                               # laju RAW (EMA)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.lbl = QtWidgets.QLabel("RAW · not connected to the agent "
                                    "(uncompressed capture)")
        self.lbl.setObjectName("MicroLabel")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        self.lbl_disk = QtWidgets.QLabel("—")
        self.lbl_disk.setWordWrap(True)   # jangan melebarkan rel
        self.lbl_disk.setObjectName("Faint")
        lay.addWidget(self.lbl_disk)

        # Start/stop rekam RAW = panel CAPTURE (REC + mode RAW) — TIDAK ada
        # tombol rekam di sini supaya tidak ada informasi ganda (permintaan
        # pemilik 17 Okt). Yang tersisa = ukuran chunk (dipakai jalur REC itu).
        row1b = QtWidgets.QHBoxLayout()
        row1b.setSpacing(6)
        self.sp_chunk = QtWidgets.QSpinBox()
        self.sp_chunk.setRange(64, 8192)
        self.sp_chunk.setValue(1024)
        self.sp_chunk.setSuffix(" MB/chunk")
        self.sp_chunk.setFixedWidth(theme.px(118))
        self.sp_chunk.setToolTip("chunk size for the RAW files written on the Pi — "
                                 "recording start/stop lives in CAPTURE "
                                 "(REC button + RAW mode)")
        row1b.addWidget(self.sp_chunk)
        row1b.addStretch(1)
        lay.addLayout(row1b)

        self.list = QtWidgets.QListWidget()
        # Nama chunk panjang JANGAN melebarkan panel (audit: min 825 px > rel 442 ->
        # terpotong di kanan). Elide tengah + tanpa scrollbar horizontal + min kecil.
        self.list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(QtCore.Qt.ElideMiddle)
        self.list.setMinimumWidth(0)
        self.list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.list.setMinimumHeight(110)
        lay.addWidget(self.list, 1)

        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(6)
        b = QtWidgets.QPushButton("REFRESH")
        b.setObjectName("BtnTiny")
        b.clicked.connect(self.client.files)
        row2.addWidget(b)
        self.b_all = QtWidgets.QPushButton("SELECT ALL")
        self.b_all.setObjectName("BtnTiny")
        self.b_all.setToolTip("tick every file in the list")
        self.b_all.clicked.connect(self._pilih_semua)
        row2.addWidget(self.b_all)
        self.b_tr = QtWidgets.QPushButton("TRANSFER ►")
        self.b_tr.setObjectName("BtnTiny")
        self.b_tr.setToolTip("download the TICKED chunks into this session's raw/ "
                             "folder + sha256 verify")
        self.b_tr.clicked.connect(self._transfer)
        self.b_tr.setEnabled(False)
        row2.addWidget(self.b_tr)
        self.b_del = QtWidgets.QPushButton("DELETE")
        self.b_del.setObjectName("BtnTiny")
        self.b_del.setToolTip("delete the TICKED files on the Pi (asks first)")
        self.b_del.setEnabled(False)
        self.b_del.clicked.connect(self._hapus)
        b.clicked.connect(self._hapus)
        row2.addWidget(self.b_del)
        row2.addStretch(1)
        lay.addLayout(row2)
        self.prog = QtWidgets.QLabel("")
        self.prog.setObjectName("Faint")
        self.prog.setWordWrap(True)
        lay.addWidget(self.prog)

        # wiring
        self.list.itemChanged.connect(self._daftar_berubah)
        self.client.sig_result.connect(self._hasil)
        self.client.sig_progress.connect(
            lambda nama, frac: self.prog.setText(f"{nama}: {frac * 100:.0f}%"))
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.client.status)
        self._timer.start(10000)
        self.client.status()
        self.client.files()          # muat daftar chunk langsung (isi panel sejak awal)

    # ------------------------------------------------------------------ aksi
    def _checked_rels(self):
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == QtCore.Qt.CheckState.Checked:
                rel = it.data(QtCore.Qt.ItemDataRole.UserRole)
                if rel:
                    out.append(rel)
        return out

    def _daftar_berubah(self, *_a):
        n = len(self._checked_rels())
        self.b_tr.setText("TRANSFER ► (%d)" % n if n else "TRANSFER ►")
        self.b_del.setText("DELETE (%d)" % n if n else "DELETE")
        self.b_tr.setEnabled(n > 0)
        self.b_del.setEnabled(n > 0)

    def _pilih_semua(self):
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(QtCore.Qt.CheckState.Checked)
        self._daftar_berubah()

    def _transfer(self):
        rels = self._checked_rels()
        if not rels:
            self.prog.setText("tick a file in the list first (or SELECT ALL)")
            return
        for rel in rels:
            self.client.transfer(rel, self.outdir)
        self.prog.setText("transferring %d file(s) → raw/ …" % len(rels))

    def _hapus(self):
        rels = self._checked_rels()
        if not rels:
            self.prog.setText("tick a file in the list first")
            return
        total = sum(self.files_info.get(r, 0) for r in rels)
        pratinjau = "\n".join(rels[:8]) + ("\n…" if len(rels) > 8 else "")
        jawab = QtWidgets.QMessageBox.question(
            self, "Delete on the robot",
            "Delete %d file(s) (%.1f MB) on the Pi?\n\n%s"
            % (len(rels), total / 1e6, pratinjau),
            QtWidgets.QMessageBox.StandardButton.Yes |
            QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No)
        if jawab != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self.prog.setText("deleting %d file(s) on the Pi …" % len(rels))
        self.client.delete(rels)

    # ------------------------------------------------------------------ hasil
    def _hasil(self, tag, d):
        if not isinstance(d, dict):
            d = {"ok": False, "error": str(d)}
        if tag == "status":
            if d.get("ok"):
                self.last_status = d
                caps = d.get("captures") or {}
                aktif = ", ".join(k for k, v in caps.items() if isinstance(v, dict) and v.get("aktif"))
                self.lbl.setText(f"RAW agent OK ({'test' if d.get('test') else 'device'}) — "
                                 f"active capture: {aktif or 'none'}")
                # estimasi sisa waktu RAW: laju dari turunnya free-space Pi
                fr = d.get("disk_free_mb")
                mn = float(d.get("min_free_mb") or 0)
                now = time.time()
                if aktif and fr is not None and self._prev_free is not None:
                    dt = now - self._prev_free[0]
                    dd = self._prev_free[1] - float(fr)
                    if dt > 0.5 and dd > 0:
                        inst = dd / (dt / 60.0)
                        self._rate_mbpm = (0.7 * self._rate_mbpm + 0.3 * inst
                                           if self._rate_mbpm else inst)
                if not aktif:
                    self._rate_mbpm = 0.0
                if fr is not None:
                    self._prev_free = (now, float(fr))
                est = None
                if aktif and self._rate_mbpm >= 1.0 and fr is not None:
                    sisa = float(fr) - mn
                    est = max(0.0, sisa) / self._rate_mbpm
                ekor = (f" · ~{int(est)} min RAW left (@ {self._rate_mbpm:.0f} MB/min)"
                        if est is not None else
                        (" · measuring RAW rate…" if aktif else
                         " · start RAW to see time left"))
                # disk/free Pi = panel CAPTURE (hindari informasi ganda);
                # di sini hanya estimasi LAJU RAW terukur saat merekam.
                self.lbl_disk.setText(ekor.strip(" ·") if aktif else "")
            else:
                self.lbl.setText("RAW agent unreachable")
                self.lbl.setToolTip(str(d.get("error", "")))
        elif tag == "files":
            if d.get("ok"):
                self.files_info = {f["path"]: f["size"] for f in d.get("files", [])}
                self.list.clear()
                self.list.blockSignals(True)
                for rel, size in sorted(self.files_info.items()):
                    it = QtWidgets.QListWidgetItem(f"{rel}   ({size / 1e6:.1f} MB)")
                    it.setData(QtCore.Qt.ItemDataRole.UserRole, rel)
                    it.setFlags(it.flags() |
                                QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                    it.setCheckState(QtCore.Qt.CheckState.Unchecked)
                    if rel in self.verified:
                        it.setText(it.text() + "  ✓ verified")
                    self.list.addItem(it)
                self.list.blockSignals(False)
                self._daftar_berubah()
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
