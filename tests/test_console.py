#!/usr/bin/env python3
"""Uji konsol offscreen (v2): 3 stream dummy + MAVLink palsu (PARAM/ACK) +
agen RAW (--test) + teleop simulasi. Semua fitur inti diuji tanpa hardware.

Prasyarat: agen RAW jalan di :8791 (lihat tests/test_raw.py docstring).
Jalankan dari folder gui_t1:  .venv/bin/python ../tests/test_console.py
"""
import glob
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
GUI = os.path.join(HERE, "..", "gui_t1")
sys.path.insert(0, GUI)

from PySide6 import QtCore, QtGui, QtWidgets    # noqa: E402

SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test")
os.makedirs(SCRATCH, exist_ok=True)
STATS = os.path.join(SCRATCH, "fv_stats.json")
# Data user uji -> scratch (deterministik, tidak menyentuh ~/.config / AppData)
os.environ.setdefault("DV_NO_UPDATE", "1")
os.environ.setdefault("DV_DATA_DIR", os.path.join(SCRATCH, "data"))


def sumber(port):
    return subprocess.Popen(
        ["gst-launch-1.0", "-q", "videotestsrc", "is-live=true", "!", "video/x-raw,width=640,height=360,framerate=25/1", "!",
         "x264enc", "tune=zerolatency", "speed-preset=ultrafast", "key-int-max=25", "bitrate=600", "!",
         "rtph264pay", "config-interval=1", "pt=96", "!", "udpsink", "host=127.0.0.1", "port=%d" % port],
        stderr=subprocess.DEVNULL)


def baca_stats():
    try:
        with open(STATS, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                      # noqa: BLE001
        return {}


def titik_rel_app(w):
    """Titik (x, y) di pita TENGAH panel rel-kiri yang PASTI terlihat —
    zona 'tambah ke ujung rel' (dipakai uji drag; deterministik)."""
    geo = w.col_left.geometry()
    cands = [k for k in w._active_spec.get("left", []) if k in w.P]
    pilih = None
    for k in cands:
        p = w.P[k]
        if p.isWindow():
            continue
        tl = p.mapTo(w, QtCore.QPoint(0, 0))
        if tl.y() < geo.top() + 4 or tl.y() + p.height() > geo.bottom() - 4:
            continue
        if p.height() < 60:
            continue
        pilih = (k, tl, p)
        break
    if pilih is None:                      # cadangan: panel pertama apa pun
        p = w.P[cands[0]]
        pilih = (cands[0], p.mapTo(w, QtCore.QPoint(0, 0)), p)
    _k, tl, p = pilih
    return (tl.x() + 18, tl.y() + int(p.height() * 0.5))


def main():
    app = QtWidgets.QApplication(sys.argv)
    try:
        os.remove(STATS)
    except OSError:
        pass
    _dd = os.environ.get("DV_DATA_DIR")
    if _dd:                                # data uji selalu mulai bersih
        import shutil
        shutil.rmtree(_dd, ignore_errors=True)
    srcs = [sumber(p) for p in (5620, 5621, 5622)]
    fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_vehicle.py"),
                             "--endpoint", "udpout:127.0.0.1:14560", "--secs", "90",
                             "--stats", STATS])
    time.sleep(1.5)

    import rov_console_real as rc
    import vehicleops as VO

    args = SimpleNamespace(
        nama_sesi=None, no_prompt=True,
        mavlink="udpin:0.0.0.0:14560",
        front_port=5620, left_port=5621, right_port=5622,
        port_front=5620, port_stereo_L=5621, port_stereo_R=5622,
        raw_agent="http://127.0.0.1:8791",
        outdir=os.path.join(SCRATCH, "cons"),
        preset="pilot-focus", no_pad=False)
    w = rc.RovConsole(args)
    w.show()

    def jalan(sec):
        t0 = time.time()
        while time.time() - t0 < sec:
            app.processEvents()
            time.sleep(0.02)

    def tunggu(kondisi, detik):
        t0 = time.time()
        while time.time() - t0 < detik:
            app.processEvents()
            if kondisi():
                return True
            time.sleep(0.02)
        return False

    lulus = True

    ok_frames = tunggu(lambda: all(w.players[n]._qimg is not None
                                   for n in ("front", "stereo L", "stereo R")), 15)
    print("[1] frame tiga stream:", ok_frames)
    lulus &= ok_frames

    ok_mav = tunggu(lambda: (w.last.get("mode") not in (None, "--")) and bool(w.last.get("volt")), 15)
    print("[2] telemetri: mode=%s volt=%s depth=%s head=%s" % (
        w.last.get("mode"), w.last.get("volt"), w.last.get("depth"), w.last.get("heading")))
    lulus &= bool(ok_mav)

    w.btn_rec["front"].setChecked(True)
    jalan(4.0)
    w.btn_rec["front"].setChecked(False)
    ok_stop = tunggu(lambda: not w.players["front"].is_recording(), 10)
    files = sorted(glob.glob(os.path.join(w.sesi, "compressed", "front_*")))
    size = os.path.getsize(files[-1]) if files else 0
    ok_rec = bool(files) and size > 50000
    print("[3] rekam front: stop_ok=%s size=%.0f B" % (ok_stop, size))
    lulus &= ok_rec

    w._snap("stereo L")
    snaps = glob.glob(os.path.join(w.sesi, "compressed", "snap_stereo_L_*.png"))
    print("[4] snapshot stereo L:", bool(snaps))
    lulus &= bool(snaps)

    try:
        for nm in ("setup", "capture", "mapping", "pilot-focus"):
            w._apply_preset(nm)
        jalan(0.5)
        print("[5] preset berganti tanpa error: True")
    except Exception as exc:                               # noqa: BLE001
        print("[5] preset error:", exc)
        lulus = False

    w.bridge.queue_param_list()
    ok_par = tunggu(lambda: len(w.bridge.params) >= 12, 10)
    print("[6] parameter terunduh: %d (butuh>=12) ok=%s" % (len(w.bridge.params), ok_par))
    lulus &= ok_par

    acks = []
    w.bridge.sig_ack.connect(lambda c, r, t: acks.append((c, r, t)))
    w.bridge.queue_command(VO.CMD_PREFLIGHT_CALIBRATION, p1=1, tag="gyro-cal")
    ok_cal = tunggu(lambda: any(c == 241 for c, _r, _t in acks), 8)
    w.bridge.queue_command(VO.CMD_DO_MOTOR_TEST, p1=1, p2=0, p3=15, p4=2, p5=1, p6=2,
                           tag="motor-1")
    ok_mot = tunggu(lambda: any(c == 209 for c, _r, _t in acks), 8)
    print("[7] ACK kalibrasi(241)=%s ACK motor(209)=%s" % (ok_cal, ok_mot))
    lulus &= bool(ok_cal and ok_mot)

    w.bridge.queue_param_set("FS_GCS_TIMEOUT", 6)
    ok_set = tunggu(lambda: any(n == "FS_GCS_TIMEOUT" for n, _v in baca_stats().get("param_set", [])), 8)
    print("[8] PARAM_SET sampai ke kendaraan:", ok_set)
    lulus &= bool(ok_set)

    w.gamepad.sim_axes = {1: 0.8, 0: 0.0, 4: 0.0, 3: 0.0}
    w.gamepad.sim_buttons = {4: True}
    for _ in range(6):
        w.gamepad._tick()
        jalan(0.1)
    ok_man = tunggu(lambda: baca_stats().get("manual", 0) > 0, 8)
    print("[9] MANUAL_CONTROL terkirim (teleop simulasi):", ok_man)
    lulus &= bool(ok_man)

    ok_nvf = tunggu(lambda: (w.bridge.state.get("nvf") or {}).get("CamTilt") is not None, 8)
    ro = w.ro_camtilt.val.text()
    print("[12] nilai sub: nvf=%s CamTilt readout=%r" % (ok_nvf, ro))
    lulus &= bool(ok_nvf and ro not in ("", "\u2014"))

    tlog = os.path.join(w.sesi, "mavlink.tlog")
    ok_tlog = tunggu(lambda: os.path.exists(tlog) and os.path.getsize(tlog) > 400, 8)
    print("[13] tlog telemetri: %s (%d B)" % (ok_tlog,
          os.path.getsize(tlog) if os.path.exists(tlog) else 0))
    lulus &= bool(ok_tlog)

    w._gain_step(0.05)
    ok_gain = tunggu(lambda: any(n == "JS_GAIN_DEFAULT" for n, _v in
                                 baca_stats().get("param_set", [])), 8)
    print("[14] gain JS_GAIN_DEFAULT sampai kendaraan:", ok_gain)
    lulus &= bool(ok_gain)

    n0 = baca_stats().get("rc", 0)
    w.sl_tilt.setValue(1800)
    jalan(4.0)
    ok_keep = tunggu(lambda: baca_stats().get("rc", 0) >= n0 + 2, 6)
    print("[15] RC override tilt dipertahankan (keep-alive):", ok_keep)
    lulus &= bool(ok_keep)

    # [18] UI tilt responsif berulang (regresi crash paint 13 Okt)
    for v in (1900, 1100, 1850, 1150, 1700):
        w.sl_tilt.setValue(v)
        jalan(0.05)
    w.sl_tilt.setValue(1600)
    jalan(0.1)
    w.sp_tilt.setValue(30)          # ketik di kotak persen -> slider harus ikut
    jalan(0.1)
    ok_tilt = (w.sl_tilt.value() == 1340) and (int(w._tilt) == 1340)
    print("[18] UI tilt responsif (slider & spin):", ok_tilt,
          "(slider=%d spin=%d)" % (w.sl_tilt.value(), w.sp_tilt.value()))
    lulus &= bool(ok_tilt)

    # [19] stress ganti-ganti preset cepat (bug "window nyangkut" 13 Okt)
    try:
        for _i in range(3):
            for nm2 in ("pilot-focus", "capture", "mapping", "setup", "capture",
                        "pilot-focus"):
                w._apply_preset(nm2)
                jalan(0.06)
        w._apply_preset("pilot-focus")
        jalan(0.3)
        ok_str = True
        for key, pnl in w.P.items():
            if key in w.vis:
                if not pnl.isVisible():
                    print("   panel preset-aktif TAK tampil:", key)
                    ok_str = False
            else:
                if pnl.isVisible():
                    print("   panel NYANGKUT (bukan preset tapi tampil):", key)
                    ok_str = False
        print("[19] stress ganti preset tanpa nyangkut:", ok_str)
        lulus &= bool(ok_str)
    except Exception as exc:                               # noqa: BLE001
        print("[19] stress ganti preset ERROR:", exc)
        lulus = False

    # [20] failsafe defaults (BlueROV2 + 4S 10000 mAh) sampai ke kendaraan
    w._apply_failsafe_defaults()
    ok_fs = tunggu(lambda: any(n == "FS_LEAK_ENABLE" for n, _v in
                               baca_stats().get("param_set", [])), 8)
    print("[20] failsafe defaults terkirim:", ok_fs)
    lulus &= bool(ok_fs)

    # [21] alert suhu client-side memicu strip alert
    w.sp_temp_warn.setValue(10)
    w.sp_temp_crit.setValue(12)
    w._safety_check(dict(w.bridge.state, temp=20.0, link=True))
    ok_t21 = (w._alert_kini is not None and "temp" in str(w._alert_kini[1]).lower())
    w.sp_temp_warn.setValue(38)
    w.sp_temp_crit.setValue(45)
    w._safety_check(dict(w.bridge.state, temp=21.0, link=True))
    ok_t21 = ok_t21 and (w._alert_kini is None)
    print("[21] alert suhu client:", ok_t21)
    lulus &= bool(ok_t21)

    # [22] preset pad bernama: simpan -> file -> terapkan
    ok_p22 = w.gamepad.simpan_preset("Custom A", {"scale_yaw": 0.9})
    try:
        with open(w.gamepad.presets_path, encoding="utf-8") as f:
            data = json.load(f)
        ok_p22 = ok_p22 and ("Custom A" in data.get("presets", {})) and \
            data.get("aktif") == "Custom A"
        w.gamepad.terapkan_cfg(data["presets"]["Custom A"])
        ok_p22 = ok_p22 and abs(float(w.gamepad.cfg["scale_yaw"]) - 0.9) < 1e-6
    except Exception as exc:                               # noqa: BLE001
        print("   preset pad error:", exc)
        ok_p22 = False
    try:
        os.remove(w.gamepad.presets_path)
    except OSError:
        pass
    print("[22] preset pad bernama simpan/muat:", ok_p22)
    lulus &= bool(ok_p22)

    # [23] halaman gamepad mapping: dialog HARUS bisa dikonstruksi (bug 13 Okt:
    #     addWidget(b, 0, int) -> TypeError, tombol PAD MAPPING terlihat "ngebug")
    try:
        import padmap as _pm
        d23 = _pm.PadMapDialog(w.gamepad, w)
        d23.show()
        ok23 = (d23.lbl["axis_fwd"].text() not in ("", "—")) and \
            (d23.lbl["deadman_buttons"].text() != "") and \
            ("btn_record" in w.gamepad.cfg) and ("btn_photo" in w.gamepad.cfg) and \
            (d23.lbl["btn_record"].text() != "") and (d23.lbl["btn_photo"].text() != "") and \
            (d23.height() <= 710) and \
            (d23.rect().bottom() - d23.b_close.geometry().bottom() >= 0)
        d23.close()
        print("[23] dialog pad mapping konstruksi:", ok23)
        lulus &= bool(ok23)
    except Exception as exc:                               # noqa: BLE001
        print("[23] dialog pad mapping GAGAL:", repr(exc))
        lulus = False

    # [24] tombol fisik REC/PHOTO di pad → jalur yang sama dengan tombol GUI
    try:
        ok24 = hasattr(w.gamepad, "sig_aksi")
        w._aksi_pad("photo")
        w._aksi_pad("record")
        w._aksi_pad("record")          # balikkan (stop) agar tidak menggantung
        ok24 = ok24 and ("pad:" in " ".join(w.log.toPlainText().splitlines()[-5:]))
        print("[24] aksi pad record/photo:", ok24)
        lulus &= bool(ok24)
    except Exception as exc:                               # noqa: BLE001
        print("[24] aksi pad GAGAL:", repr(exc))
        lulus = False

    # [25] alur file: folder compressed/ + raw/ ada; snap masuk compressed/;
    #      mode RAW mengarah ke agen onboard (bukan recorder lokal)
    try:
        ok25 = os.path.isdir(os.path.join(w.sesi, "compressed")) and \
            os.path.isdir(os.path.join(w.sesi, "raw"))
        komp = os.path.join(w.sesi, "compressed")
        sebelum = set(os.listdir(komp))
        w._snap("front")
        jalan(0.4)
        ok25 = ok25 and (len(set(os.listdir(komp)) - sebelum) >= 1)
        w.cb_mode["stereo L"].setCurrentText("RAW")
        w.btn_rec["stereo L"].setChecked(True)
        jalan(1.0)
        import urllib.request as _u
        st25 = json.load(_u.urlopen("http://127.0.0.1:8791/status", timeout=3))
        ok25 = ok25 and bool(((st25.get("captures") or {}).get("left") or {}).get("aktif"))
        w.btn_rec["stereo L"].setChecked(False)
        jalan(0.6)
        w.cb_mode["stereo L"].setCurrentText("COMPRESSED")
        print("[25] alur file compressed/raw + routing RAW:", ok25)
        lulus &= bool(ok25)
    except Exception as exc:                               # noqa: BLE001
        print("[25] alur file GAGAL:", repr(exc))
        lulus = False

    # [26] save/load parameter & failsafe → folder APLIKASI saved/ (roundtrip)
    try:
        ok26 = os.path.isdir(VO.PASTA_SIMPAN)
        fp = VO.FailsafePanel(w.bridge)
        pa = os.path.join(VO.PASTA_SIMPAN, "_uji_failsafe.json")
        fp.simpan(path=pa)
        ok26 = ok26 and os.path.exists(pa)
        fp.muat_file(path=pa)
        ok26 = ok26 and os.path.exists(pa)
        try:
            os.remove(pa)
        except OSError:
            pass
        pb = w.P["params"].findChild(VO.ParamBrowser)
        ok26 = ok26 and (pb is not None)
        if pb is not None:
            pj = os.path.join(VO.PASTA_SIMPAN, "_uji_params.json")
            n_save = pb.simpan(path=pj)
            n_ps = len(baca_stats().get("param_set", []))
            n_load = pb.muat(path=pj)
            tunggu(lambda: len(baca_stats().get("param_set", [])) > n_ps, 6)
            ok26 = ok26 and (n_save >= 10) and (n_load == n_save)
            try:
                os.remove(pj)
            except OSError:
                pass
        print("[26] save/load parameter -> saved/:", ok26)
        lulus &= bool(ok26)
    except Exception as exc:                               # noqa: BLE001
        print("[26] save/load GAGAL:", repr(exc))
        lulus = False

    # [27] mode MAPPING memblokir output ke ROV (permintaan pemilik 13 Okt):
    #      D-PAD atas TIDAK boleh menggerakkan tilt selama halaman pad terbuka.
    try:
        import padmap as _padmap
        g = w.gamepad
        g.sim_axes = {7: -1.0}            # D-PAD atas (axis_tilt default [7])
        time.sleep(0.08)
        g._tick()
        g._tick()
        tilt_gerak = g.tilt
        d27 = _padmap.PadMapDialog(g, w)
        ok27 = (g.output_blocked is True)
        t_blok0 = g.tilt
        time.sleep(0.08)
        g._tick()                          # D-PAD atas ditahan — harus DIABAIKAN
        ok27 = ok27 and (g.tilt == t_blok0) and (g.last_out == (0, 0, 500, 0))
        d27.close()
        ok27 = ok27 and (g.output_blocked is False)
        g.sim_axes = None
        print("[27] mapping memblokir output ke ROV:", ok27)
        lulus &= bool(ok27)
    except Exception as exc:                               # noqa: BLE001
        print("[27] blokir mapping GAGAL:", repr(exc))
        lulus = False

    # [28] nama sesi bisa diinput/diganti — folder + label ikut berubah
    try:
        w._ganti_nama_sesi("Test Dive Alpha")
        nm28 = os.path.basename(w.sesi)
        ok28 = ("test_dive_alpha" in nm28) and os.path.isdir(w.sesi)
        ok28 = ok28 and ("test_dive_alpha" in w.lbl_sesi.text().lower())
        ok28 = ok28 and os.path.isdir(os.path.join(w.sesi, "compressed"))
        w._ganti_nama_sesi("Test Dive Alpha")          # ganti ke nama sama: aman
        ok28 = ok28 and os.path.isdir(w.sesi)
        w._ganti_nama_sesi("")                          # kosong -> "dive"
        ok28 = ok28 and ("_dive" in os.path.basename(w.sesi))
        print("[28] nama sesi bisa diinput/diganti:", ok28)
        lulus &= bool(ok28)
    except Exception as exc:                               # noqa: BLE001
        print("[28] nama sesi GAGAL:", repr(exc))
        lulus = False

    # [29] estimasi storage + margin pengaman + blokir REC/SNAP saat penuh
    try:
        # Deterministik: override disk-free via SEAM resmi (uji _update_disk
        # bermuka-muka dengan disk kecil; timer tidak menimpa flag lagi).
        w._disk_free_mb_override = 900.0
        w._update_disk()
        jalan(0.3)
        ok_pre29 = w._disk_blok and w._disk_free_mb == 900.0
        komp = os.path.join(w.sesi, "compressed")
        sebelum = set(os.listdir(komp))
        w._snap("front")
        jalan(1.2)
        logtxt = w.log.toPlainText()
        ok29 = ok_pre29 and (set(os.listdir(komp)) == sebelum)
        ok29 = ok29 and ("SNAP blocked \u2014 topside storage full" in logtxt)
        w.btn_rec["front"].setChecked(True)        # compressed → harus ditolak
        jalan(1.2)
        logtxt = w.log.toPlainText()
        ok29 = ok29 and (not w.btn_rec["front"].isChecked())
        ok29 = ok29 and ("REC blocked \u2014 topside storage full" in logtxt)
        w._safety_check(dict(w.bridge.state, link=True))
        ok29 = ok29 and (w._alert_kini is not None and
                         "STORAGE" in str(w._alert_kini[1]).upper())
        # estimasi: free 2400, laju 60 MB/min → (2400-1500)/60 = 15 min
        est29 = w._est_menit(2400.0, 60.0)
        ok29 = ok29 and (est29 is not None and abs(est29 - 15.0) < 0.6)
        w._disk_free_mb = None
        w._disk_blok = False
        w._disk_free_mb_override = None
        w._safety_check(dict(w.bridge.state, link=True))
        print("[29] estimasi & blokir storage:", ok29)
        lulus &= bool(ok29)
    except Exception as exc:                               # noqa: BLE001
        print("[29] storage GAGAL:", repr(exc))
        lulus = False

    # [30] preflight memeriksa SETIAP sensor & menyebut sensor yang error
    try:
        w.bridge.pause_sys = True        # hentikan aliran SYS_STATUS (anti-race CI)
        pf = w.P["preflight"].panel_preflight
        st = w.bridge.state
        allbits = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3) | (1 << 15) | (1 << 24)
        st["sys_present"] = allbits
        st["sys_healthy"] = allbits
        w.bridge.params.pop("COMPASS_DEV_ID", None)   # param belum terunduh
        pf._perbarui()
        ok30 = (pf.sensor_txt.get("s_gyro") == "ok") and \
            (pf.sensor_txt.get("s_mag") == "ok")      # cukup "ok" — bukan FAULT palsu
        w.bridge.params["COMPASS_DEV_ID"] = 71146     # setelah param terunduh
        pf._perbarui()
        ok30 = ok30 and ("1 compass" in pf.sensor_txt.get("s_mag", ""))
        st["sys_healthy"] = allbits & ~(1 << 2)          # MAG rusak
        pf._perbarui()
        ok30 = ok30 and (pf.sensor_txt.get("s_mag") == "FAULT")
        st["sys_present"] = allbits & ~(1 << 3)          # BARO tidak terpasang
        pf._perbarui()
        ok30 = ok30 and (pf.sensor_txt.get("s_baro") == "NOT FOUND")
        st["sys_present"] = 0
        st["sys_healthy"] = 0
        pf._perbarui()
        # semua bit 0 = belum ada data -> chip jujur "--", bukan menuduh
        ok30 = ok30 and (pf.sensor_txt.get("s_gyro") == "—")
        st.pop("sys_present", None)
        st.pop("sys_healthy", None)
        w.bridge.pause_sys = False
        print("[30] preflight per-sensor:", ok30)
        lulus &= bool(ok30)
    except Exception as exc:                               # noqa: BLE001
        print("[30] preflight sensor GAGAL:", repr(exc))
        lulus = False

    # [36] lay-snap: drag panel ke rail KIRI (Chrome-style) + anti-nyangkut
    try:
        w._tata_forget()                        # mulai bersih (tanpa custom)
        w._apply_preset("pilot-focus"); jalan(0.1)
        before = [k for k in w._active_spec.get("left", [])]
        w.snap.simulate_begin("notif")
        kind36 = w.snap.simulate_move_to(*titik_rel_app(w))
        ok36 = (kind36 == ("rail", "left"))
        w.snap.simulate_drop(*titik_rel_app(w)); jalan(0.2)
        after = list(w._active_spec.get("left", []))
        ok36 = ok36 and after[:len(before)] == before and \
            w._asal_of("notif") == ["left", len(after) - 1] and \
            (not w.P["notif"].isWindow())
        # ganti-ganti preset cepat: panel tidak boleh "melayang nyangkut"
        for _ in range(3):
            w._apply_preset("mapping"); jalan(0.05)
            w._apply_preset("pilot-focus"); jalan(0.05)
        orphans = [k for k, p in w.P.items()
                   if (not p.isWindow()) and p.isVisible() and
                   p.parentWidget() is None]
        ok36 = ok36 and not orphans
        w._apply_custom(); jalan(0.1)           # file layout_custom masih ada
        ok36 = ok36 and (w._asal_of("notif") == ["left", len(w._active_spec
                           .get("left", [])) - 1])
        print("[36] drag-snap rail kiri + anti-nyangkut:", ok36)
        lulus &= bool(ok36)
    except Exception as exc:                               # noqa: BLE001
        print("[36] drag-snap GAGAL:", repr(exc))
        lulus = False
    finally:
        w.snap.simulate_cancel()

    # [37] lay-snap: sisip di kolom tengah (di atas/di bawah panel kamera)
    try:
        w._apply_custom(); jalan(0.1)
        peta = w.P["peta"]
        tl = peta.mapTo(w, QtCore.QPoint(0, 0))
        # 0.2 + clamp: zona 'above/below' = PITA atas/bawah panel (tengah
        # panel = 'end'); kamera kini FIT rasio → titik harus di pita & terlihat.
        pt = QtCore.QPoint(tl.x() + peta.width() // 2,
                           min(tl.y() + int(peta.height() * 0.2),
                               w.height() - 60))
        w.snap.simulate_begin("raw")
        kind37 = w.snap.simulate_move_to(pt.x(), pt.y())
        ok37 = kind37[0] in ("below", "above") and kind37[1] == "peta"
        w.snap.simulate_drop(pt.x(), pt.y()); jalan(0.2)
        mid_keys = [it[0] if isinstance(it, tuple) else it
                    for it in w._active_spec.get("mid", [])]
        if kind37[0] == "below":
            ok37 = ok37 and (mid_keys.index("raw") == mid_keys.index("peta") + 1)
        else:
            ok37 = ok37 and (mid_keys.index("peta") == mid_keys.index("raw") + 1)
        w.snap.simulate_begin("raw")
        tf = w.P["cam_front"]; tl2 = tf.mapTo(w, QtCore.QPoint(0, 0))
        pt2 = QtCore.QPoint(tl2.x() + tf.width() // 2,
                            min(tl2.y() + int(tf.height() * 0.2),
                                w.height() - 60))
        kind37b = w.snap.simulate_move_to(pt2.x(), pt2.y())
        ok37 = ok37 and kind37b[0] in ("below", "above") and \
            kind37b[1] == "cam_front"
        w.snap.simulate_cancel()
        print("[37] drag-snap sisip tengah (atas/bawah kamera):", ok37)
        if not ok37:
            print("[37] DETAIL kind37=%r kind37b=%r mid=%r asal_raw=%r"
                  % (kind37, kind37b, mid_keys, w._asal_of("raw")))
        lulus &= bool(ok37)
    except Exception as exc:                               # noqa: BLE001
        print("[37] sisip tengah GAGAL:", repr(exc))
        lulus = False
    finally:
        w.snap.simulate_cancel()

    # [38] lay-snap: pop-out floating window & tarik kembali ke rel
    try:
        w.snap.simulate_begin("teleop")
        w.snap.simulate_move_to(w.width() + 300, 0)     # keluar jendela
        w.snap.simulate_drop(w.width() + 300, 0); jalan(0.2)
        ok38 = w.P["teleop"].isWindow() and w.P["teleop"].isVisible()
        w.snap.simulate_begin("teleop")
        w.snap.simulate_drop(*titik_rel_app(w)); jalan(0.2)       # tarik ke rel kiri
        ok38 = ok38 and (not w.P["teleop"].isWindow())
        _asal38 = w._asal_of("teleop") or [None]
        ok38 = ok38 and (_asal38[0] == "left")
        if not ok38:
            print("[38] DETAIL iswin=%r vis=%r asal=%r left=%r"
                  % (w.P["teleop"].isWindow(), w.P["teleop"].isVisible(),
                     w._asal_of("teleop"), w._active_spec.get("left", [])))
        print("[38] pop-out floating + re-dock:", ok38)
        lulus &= bool(ok38)
    except Exception as exc:                               # noqa: BLE001
        print("[38] floating GAGAL:", repr(exc))
        lulus = False
    finally:
        w.snap.simulate_cancel()

    # [39] persistensi layout CUSTOM (saved/layout_custom.json) + reload
    try:
        w.snap.simulate_begin("teleop")
        w.snap.simulate_move_to(*titik_rel_app(w))
        w.snap.simulate_drop(*titik_rel_app(w)); jalan(0.2)
        path = w._tata_json()
        ok39 = os.path.isfile(path)
        data = json.load(open(path, encoding="utf-8"))
        ok39 = ok39 and ("teleop" in [k for k in
                     (data.get("spec") or {}).get("left", [])])
        w._apply_custom(); jalan(0.1)
        ok39 = ok39 and ("teleop" in
                         w._active_spec.get("left", []))
        w._menu_tata_forget()
        ok39 = ok39 and (not os.path.isfile(path))
        print("[39] custom layout tersimpan & dimuat ulang:", ok39)
        lulus &= bool(ok39)
    except Exception as exc:                               # noqa: BLE001
        print("[39] custom GAGAL:", repr(exc))
        lulus = False
    finally:
        w.snap.simulate_cancel()

    # [40] ESC batal drag: susunan tidak berubah; float pulang ke "asal"
    try:
        w._tata_forget()
        w._apply_preset("setup"); jalan(0.1)
        spec0 = json.dumps(w._active_spec, sort_keys=True)
        w.snap.simulate_begin("raw")
        w.snap.simulate_move_to(*titik_rel_app(w))
        w.snap.simulate_cancel(); jalan(0.1)
        ok40 = (json.dumps(w._active_spec, sort_keys=True) == spec0) and \
            (not w.P["raw"].isWindow())
        # posisi FLOAT pulang ke slot asal ("asal") saat preset diganti
        w.snap.simulate_begin("raw")
        w.snap.simulate_drop(w.width() + 300, 0); jalan(0.2)
        ok40 = ok40 and w.P["raw"].isWindow()
        w.snap.simulate_begin("raw")
        w.snap.simulate_drop(*titik_rel_app(w)); jalan(0.2)       # dock kanan dulu? kiri
        ok40 = ok40 and (not w.P["raw"].isWindow())
        w.snap.simulate_begin("raw")
        w.snap.simulate_move_to(w.width() + 300, 0)
        w.snap.simulate_drop(w.width() + 300, 0); jalan(0.2)   # float lagi
        w._apply_preset("setup"); jalan(0.2)
        ok40 = ok40 and (not w.P["raw"].isWindow())     # pulang ke asal
        w._apply_preset("pilot-focus"); jalan(0.1)
        w._tata_forget()
        print("[40] ESC cancel + float pulang ke asal:", ok40)
        lulus &= bool(ok40)
    except Exception as exc:                               # noqa: BLE001
        print("[40] cancel/asal GAGAL:", repr(exc))
        lulus = False
    finally:
        w.snap.simulate_cancel()
        w._tata_forget()

    # [41] DEPTH (baro/ground-pressure) dialog: START -> 241 dengan param3=1
    try:
        d41 = VO.SimpleCalDialog(w.bridge, "depth", w)
        d41._mulai()
        ok41 = tunggu(lambda: any(c[0] == 241 and c[3] == 1
                                  for c in baca_stats().get("cmd", [])), 8)
        d41.close()
        print("[41] kalibrasi DEPTH (241 p3=1):", ok41)
        lulus &= bool(ok41)
    except Exception as exc:                               # noqa: BLE001
        print("[41] depth cal GAGAL:", repr(exc))
        lulus = False

    # [42] accel 6-pos wizard START: kirim enter-command 241 (param5=1) dulu
    try:
        d42 = VO.AccelCalDialog(w.bridge, w)
        d42._mulai()
        ok42 = tunggu(lambda: any(c[0] == 241 and c[4] == 1
                                  for c in baca_stats().get("cmd", [])) and
                      any(c[0] == VO.CMD_ACCELCAL_VEHICLE_POS
                          for c in baca_stats().get("cmd", [])), 8)
        d42.close()
        print("[42] accel wizard: 241 p5=1 + ACCELCAL_VEHICLE_POS:", ok42)
        lulus &= bool(ok42)
    except Exception as exc:                               # noqa: BLE001
        print("[42] accel start GAGAL:", repr(exc))
        lulus = False

    # [43] estimasi LiPo dari tegangan (4S 15.47 V ~ 60%, full/empty clamp)
    try:
        c4, p4 = rc._lipo_pct(15.47)
        c4f, p4f = rc._lipo_pct(16.86)
        c2, p2 = rc._lipo_pct(7.9)
        c2e, p2e = rc._lipo_pct(7.0)
        ok43 = (c4 == 4 and 25 <= p4 <= 45) and (c4f == 4 and p4f == 100) and \
            (c2 == 2 and 40 <= p2 <= 60) and (c2e == 2 and p2e <= 10)
        print("[43] estimasi LiPo (4S 15.47 = %d%%, 16.86 = %d%%, 2S 7.9 = %d%%):"
              % (p4, p4f, p2), ok43)
        lulus &= bool(ok43)
    except Exception as exc:                               # noqa: BLE001
        print("[43] lipo est GAGAL:", repr(exc))
        lulus = False

    # [44] saran LiPo guard (berhenti di 14.6 V / est 32%, critical 14.0 V)
    try:
        fsav = dict(rc.RovConsole._FS_REKOMENDASI)
        ok44 = (fsav.get("BATT_LOW_VOLT") == 14.6 and
                fsav.get("BATT_CRT_VOLT") == 14.0 and
                fsav.get("BATT_LOW_MAH") == 2500 and
                "14.6 V" in w.lbl_batt_stop.text() and
                "14.0 V" in w.lbl_batt_stop.text())
        print("[44] LiPo guard: defaults + label stop-muatan:", ok44)
        lulus &= bool(ok44)
    except Exception as exc:                               # noqa: BLE001
        print("[44] lipo guard GAGAL:", repr(exc))
        lulus = False

    # [45] geometri jendela dimuatkan ke area kerja (layar kecil / DPI besar):
    # 1920x1040 -> 1620x1000 tengah; 1536x816 (125% DPI) & 1280x720 -> isi layar
    # penuh + tandai "longgar"; 2560x1440 -> 1620x1000 tengah.
    try:
        g1 = rc.hitung_geometri_jendela(1920, 1040, 1432, 915)
        ok45 = (g1 == (1620, 1000, 150, 20, False))
        g2 = rc.hitung_geometri_jendela(1536, 816, 1432, 915)
        ok45 = ok45 and (g2 == (1536, 816, 0, 0, True))
        g3 = rc.hitung_geometri_jendela(1280, 720, 1600, 1000)
        ok45 = ok45 and (g3 == (1280, 720, 0, 0, True))
        g4 = rc.hitung_geometri_jendela(2560, 1440, 1432, 915)
        ok45 = ok45 and (g4 == (1620, 1000, 470, 220, False))
        print("[45] geometri muat-layar (1920/1536/1280/2560):", ok45)
        lulus &= bool(ok45)
    except Exception as exc:                               # noqa: BLE001
        print("[45] muat-layar GAGAL:", repr(exc))
        lulus = False

    # [46] drag panel ke ATAS tetangganya (laporan pemilik 15 Okt: dulu hanya
    # bisa "turun"); + regresi off-by-one urutan (pindah ke bawah = sisip
    # TEPAT di bawah target, bukan di ujung daftar).
    try:
        import laysnap as _LS
        s2, _ = _LS.move_into_spec({"left": ["a", "b", "c", "d"], "right": []},
                                   {}, "a", "left", 3)     # a -> bawah c
        ok46 = (s2["left"] == ["b", "c", "a", "d"])
        s3, _ = _LS.move_into_spec({"left": ["a", "b", "c", "d"], "right": []},
                                   {}, "d", "left", 1)     # d -> atas b
        ok46 = ok46 and (s3["left"] == ["a", "d", "b", "c"])
        w._tata_forget()
        w._apply_preset("pilot-focus"); jalan(0.15)
        left0 = [k for k in w._active_spec.get("left", [])]
        atas = left0[0]
        pindah = left0[1] if len(left0) > 1 else None
        if pindah is not None:
            pa = w.P[atas]
            tla = pa.mapTo(w, QtCore.QPoint(0, 0))
            pt = QtCore.QPoint(tla.x() + 18, tla.y() + int(pa.height() * 0.15))
            w.snap.simulate_begin(pindah)
            k46 = w.snap.simulate_move_to(pt.x(), pt.y())
            ok46 = ok46 and (k46 == ("above", atas))
            w.snap.simulate_drop(pt.x(), pt.y()); jalan(0.2)
            left1 = [k for k in w._active_spec.get("left", [])]
            ok46 = ok46 and (left1.index(pindah) + 1 == left1.index(atas))
        print("[46] drag ke ATAS tetangga rel (zona above + urutan turun):", ok46)
        lulus &= bool(ok46)
    except Exception as exc:                               # noqa: BLE001
        print("[46] drag ke atas GAGAL:", repr(exc))
        lulus = False
    finally:
        w.snap.simulate_cancel()
        w._tata_forget()

    # [47] update-checker: parse/banding versi, pilih aset per-platform,
    # offline aman; paths.py — folder data user mengikuti DV_DATA_DIR.
    try:
        import paths as _P
        import updater as _U
        ok47 = (_U.parse_version("v1.2.3") == (1, 2, 3) and
                _U.parse_version("1.0.10") > _U.parse_version("1.0.9") and
                _U.parse_version("garbage") == (0, 0, 0))
        ok47 = ok47 and _U.is_newer("v1.0.6", "1.0.5") and \
            (not _U.is_newer("v1.0.5", "1.0.5")) and \
            (not _U.is_newer("v1.0.4", "1.0.5"))
        aset = {"DeepVertexMarineConsole.exe": "https://x/exe",
                "DeepVertexMarineConsole-Setup-1.0.6.exe": "https://x/setup",
                "deepvertex-console-v1.0.6-linux.tar.gz": "https://x/tar"}
        ok47 = ok47 and (_U.aset_url_untuk_platform(aset) in aset.values())
        _simpan = _U.API_LATEST
        try:
            _U.API_LATEST = "http://127.0.0.1:9/tidak-ada"   # port tertutup
            ok47 = ok47 and (_U.cek_update("1.0.5", timeout=1) is None)
        finally:
            _U.API_LATEST = _simpan
        ok47 = ok47 and (_P.saved_dir() ==
                         os.path.join(os.environ["DV_DATA_DIR"], "saved"))
        print("[47] update-checker + paths data user:", ok47)
        lulus &= bool(ok47)
    except Exception as exc:                               # noqa: BLE001
        print("[47] update-checker GAGAL:", repr(exc))
        lulus = False

    # [48] koneksi: preset BlueOS/SITL, args_terapan, perintah ethernet per-OS,
    # simpan/muat koneksi.json.
    try:
        import koneksi as _K
        ok48 = (_K.preset("blueos")["mavlink"] == "udpin:0.0.0.0:14550" and
                _K.preset("blueos")["raw_agent"] == "http://192.168.2.2:8765" and
                _K.preset("sitl")["host"] == "127.0.0.1")
        argv0 = ["app.py", "--mavlink", "udpin:0.0.0.0:9999", "--no-pad",
                 "--raw-agent=http://lama:1"]
        a2 = _K.args_terapan({"mavlink": "udpin:0.0.0.0:14550",
                              "front_port": 5600, "left_port": 5601,
                              "right_port": 5602,
                              "raw_agent": "http://192.168.2.2:8765"}, argv0)
        ok48 = ok48 and ("udpin:0.0.0.0:14550" in a2) and \
            ("udpin:0.0.0.0:9999" not in a2) and \
            (not any(x.startswith("--raw-agent=") and "lama" in x
                     for x in a2)) and ("--no-pad" in a2) and \
            (a2.count("--mavlink") == 1)
        p_win = _K.perintah_ethernet("nt", "Ethernet", dhcp=True)
        p_lin = _K.perintah_ethernet("posix", "enp3s0", dhcp=False)
        ok48 = ok48 and any("netsh" in x for x in p_win["cepat"]) and \
            any("ip addr add" in x for x in p_lin["cepat"])
        c_save = {"preset": "custom", "host": "10.0.0.5",
                  "mavlink": "udpin:0.0.0.0:14550", "front_port": 5600,
                  "left_port": 5601, "right_port": 5602,
                  "raw_agent": "http://10.0.0.5:8765"}
        ok48 = ok48 and _K.save(c_save) and \
            ((_K.load() or {}).get("host") == "10.0.0.5")
        print("[48] koneksi (preset/args/ethernet/simpan):", ok48)
        lulus &= bool(ok48)
    except Exception as exc:                               # noqa: BLE001
        print("[48] koneksi GAGAL:", repr(exc))
        lulus = False

    # [49] gamepad Windows: terjemahan SDL -> kode evdev (joystick palsu).
    try:
        import gamepad as _GP
        class _FakeJ:
            def __init__(self):
                self.ax = {0: 0.5, 1: -0.25, 3: 1.0, 4: 0.0, 5: 1.0}
                self.btn = {0: 1, 4: 1}
                self.hat = (0, -1)
            def get_name(self):
                return "Fake Pad"
            def get_axis(self, i):
                return self.ax.get(i, 0.0)
            def get_button(self, i):
                return self.btn.get(i, 0)
            def get_numhats(self):
                return 1
            def get_hat(self, i):
                return self.hat
            def quit(self):
                pass
        g49 = _GP.Gamepad(None)
        g49.device = _GP._SdlPad(_FakeJ())
        g49._konek = True
        g49._sdl_baca()
        ec = _GP.ecodes
        ok49 = (abs(g49.axes.get(ec.ABS_X, 0) - int(0.5 * 32767)) <= 2 and
                abs(g49.axes.get(ec.ABS_RY, 0) - 32767) <= 2 and
                g49.axes.get(ec.ABS_HAT0Y, 0) == -32767 and
                g49.buttons.get(ec.BTN_SOUTH, 0) == 1 and
                g49.buttons.get(ec.BTN_TL, 0) == 1 and
                g49.buttons.get(ec.BTN_TL2, 0) == 0 and      # axis4=0 -> belum tekan
                g49.buttons.get(ec.BTN_TR2, 0) == 1)         # axis5=1.0 -> tekan
        nx0 = g49._norm_axis(0)
        nx1 = g49._norm_axis(1)
        ok49 = ok49 and abs(nx0 - 0.5) < 0.01 and abs(nx1 + 0.25) < 0.01
        if not ok49:
            print("[49] DETAIL axes=%r buttons=%r nx=%.3f,%.3f"
                  % ({k: g49.axes.get(k) for k in (0, 1, 3, 5, 16, 17)},
                     {k: g49.buttons.get(k) for k in (304, 305, 312, 313, 310)},
                     nx0, nx1))
        print("[49] gamepad SDL->evdev (translate + norm):", ok49)
        lulus &= bool(ok49)
    except Exception as exc:                               # noqa: BLE001
        print("[49] gamepad SDL GAGAL:", repr(exc))
        lulus = False

    # [50] robotops: perintah start/cari agent + simpan/muat koneksi
    # (desain "jalur resmi": tanpa field SSH / tombol one-click).
    try:
        import koneksi as _K50
        import robotops as _RO
        cmd50 = _RO.perintah_start("/home/pi/onboard/raw_agent.py", 8765)
        ok50 = ("raw_agent.py" in cmd50 and "--port 8765" in cmd50 and
                "nohup" in cmd50)
        cari50 = _RO.perintah_cari("~/onboard/raw_agent.py")
        ok50 = ok50 and "raw_agent.py" in cari50 and "find" in cari50
        ok50 = ok50 and (_RO.agent_status("127.0.0.1", 1, timeout=0.3)
                         in (True, False))
        c50 = _K50.preset("blueos")
        ok50 = ok50 and c50.get("host") == "192.168.2.2" \
            and "raw_agent" in c50 and "ssh_user" not in c50
        ok50 = ok50 and _K50.save(dict(c50, host="10.1.1.1",
                                       raw_agent="http://10.1.1.1:8765"))
        ok50 = ok50 and ((_K50.load() or {}).get("host") == "10.1.1.1") and \
            ((_K50.load() or {}).get("raw_agent") == "http://10.1.1.1:8765")
        print("[50] robotops + koneksi simpan/muat:", ok50)
        lulus &= bool(ok50)
    except Exception as exc:                               # noqa: BLE001
        print("[50] robotops GAGAL:", repr(exc))
        lulus = False

    # [51] updater: deteksi setup tertunda (jalan pemulihan "Finish pending
    # update") — file besar di update/setup-baru.exe terdeteksi & dibersihkan.
    try:
        import paths as _P51
        import updater as _U51
        p51 = os.path.join(_P51.data_dir(), "update")
        os.makedirs(p51, exist_ok=True)
        f51 = os.path.join(p51, "setup-baru.exe")
        with open(f51, "wb") as fh:
            fh.truncate(60 * 1024 * 1024)          # sparse, instan
        ok51 = _U51.ada_setup_tertunda() == f51
        os.remove(f51)
        ok51 = ok51 and (_U51.ada_setup_tertunda() is None)
        print("[51] setup tertunda (deteksi):", ok51)
        lulus &= bool(ok51)
    except Exception as exc:                               # noqa: BLE001
        print("[51] setup tertunda GAGAL:", repr(exc))
        lulus = False

    # [52] updater: auto-bersih installer USANG (versi <= terpasang) —
    # kebijakan storage dari pemilik 15 Okt.
    try:
        import paths as _P52
        import updater as _U52
        p52 = os.path.join(_P52.data_dir(), "update")
        os.makedirs(p52, exist_ok=True)
        lama = os.path.join(p52, "setup-0.9.9.exe")
        baru = os.path.join(p52, "setup-99.0.0.exe")
        for f in (lama, baru):
            with open(f, "wb") as fh:
                fh.truncate(60 * 1024 * 1024)          # sparse
        dih = _U52.bersihkan_update_lama("1.0.13")
        ok52 = ("setup-0.9.9.exe" in dih and not os.path.exists(lama)
                and os.path.exists(baru))
        ok52 = ok52 and (_U52.ada_setup_tertunda() == baru)
        os.remove(baru)
        print("[52] auto-bersih installer usang:", ok52)
        lulus &= bool(ok52)
    except Exception as exc:                               # noqa: BLE001
        print("[52] auto-bersih GAGAL:", repr(exc))
        lulus = False

    # [53] robotops BlueOS API: jalur + payload + deteksi endpoint (murni).
    try:
        import robotops as _RO53
        ok53 = _RO53.blueos_api("192.168.2.2").endswith(
            "/ardupilot-manager/v1.0/endpoints/")
        daftar53 = [
            {"connection_type": "udpin", "place": "127.0.0.1",
             "argument": 14001, "enabled": True},
            {"connection_type": "udpout", "place": "192.168.2.1",
             "argument": 14550, "enabled": True},
        ]
        ok53 = ok53 and _RO53.endpoint_ada(daftar53, "192.168.2.1", 14550)
        ok53 = ok53 and not _RO53.endpoint_ada(daftar53, "192.168.2.129", 14550)
        ok53 = ok53 and (_RO53.ip_ke_robot("127.0.0.1") in ("127.0.0.1", None))
        print("[53] robotops BlueOS API (payload+deteksi):", ok53)
        lulus &= bool(ok53)
    except Exception as exc:                               # noqa: BLE001
        print("[53] robotops BlueOS API GAGAL:", repr(exc))
        lulus = False

    # [54] koneksi: skrip pemulihan firewall (isi benar; murni).
    try:
        import koneksi as _K54
        sk = _K54.skrip_fix_firewall()
        ok54 = ("Remove-NetFirewallRule" in sk and "deepvertex" in sk
                and "14550" in sk and "Block" in sk)
        print("[54] firewall fix script (isi):", ok54)
        lulus &= bool(ok54)
    except Exception as exc:                               # noqa: BLE001
        print("[54] firewall fix GAGAL:", repr(exc))
        lulus = False

    # [55] network setup resmi BlueRobotics (fungsi murni): langkah per-OS +
    # parse verifikasi + default subnet 192.168.2.1/24 (laptop-side saja).
    try:
        import koneksi as _K55
        s_nt = _K55.network_steps("nt", "Ethernet 3")
        s_lin = _K55.network_steps("posix", "enx001122")
        s_mac = _K55.network_steps("darwin", "USB 10/100 LAN")
        ok55 = ("netsh" in s_nt["apply"][0]
                and "static 192.168.2.1 255.255.255.0" in s_nt["apply"][0])
        join_lin = " ".join(s_lin["apply"])
        ok55 = ok55 and ("nmcli con mod" in join_lin
                         and "enx001122" in join_lin
                         and "ipv4.never-default" in join_lin)
        ok55 = ok55 and ("networksetup -setmanual" in s_mac["apply"][0]
                         and "192.168.2.1 255.255.255.0" in s_mac["apply"][0])
        ok55 = ok55 and 'Control Panel' in s_nt["manual"] \
            and 'System Settings' in s_mac["manual"]
        ok55 = ok55 and _K55.parse_verify(
            "nt", "IP Address:                           192.168.2.1\n"
                  "Subnet Prefix:                         192.168.2.0/24")
        ok55 = ok55 and not _K55.parse_verify("nt", "IP Address: 10.0.0.5")
        ok55 = ok55 and _K55.parse_verify("posix",
                                          "enx0  UP  192.168.2.1/24  brd -")
        ok55 = ok55 and not _K55.parse_verify("posix", "inet 10.0.0.5/24 brd")
        ok55 = ok55 and _K55.SUBNET_DEFAULT == "192.168.2.1/24"
        print("[55] network setup resmi (langkah per-OS + verifikasi):", ok55)
        lulus &= bool(ok55)
    except Exception as exc:                               # noqa: BLE001
        print("[55] network setup GAGAL:", repr(exc))
        lulus = False

    # [56] wheel guard: roda-scroll di atas combo yang TAK fokus tidak
    # mengubah nilai, tapi menggulir area (scroll "nyambung" ke halaman).
    try:
        import wheelguard as _WG56
        _WG56.pasang(QtWidgets.QApplication.instance())
        area56 = QtWidgets.QScrollArea()
        area56.setFixedSize(240, 160)
        isi56 = QtWidgets.QWidget()
        lay56 = QtWidgets.QVBoxLayout(isi56)
        cb56 = QtWidgets.QComboBox()
        cb56.addItems(["A", "B", "C"])
        lay56.addWidget(cb56)
        sp56 = QtWidgets.QSpinBox()          # tanpa popup: uji klik-dulu bersih
        sp56.setRange(0, 1000)
        sp56.setValue(100)
        lay56.addWidget(sp56)
        for _i in range(30):
            lay56.addWidget(QtWidgets.QLabel("filler %d" % _i))
        area56.setWidget(isi56)
        area56.show()
        jalan(0.3)
        cb56.clearFocus()
        jalan(0.1)
        idx0 = cb56.currentIndex()
        sb56 = area56.verticalScrollBar()
        sb0 = sb56.value()
        ev56 = QtGui.QWheelEvent(
            QtCore.QPointF(10, 10), QtCore.QPointF(10, 10),
            QtCore.QPoint(0, 0), QtCore.QPoint(0, -120),
            QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
            QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        QtWidgets.QApplication.sendEvent(cb56, ev56)
        jalan(0.15)
        ok56a = (cb56.currentIndex() == idx0)
        ok56b = (sb56.value() > sb0)

        def _wheel56():
            return QtGui.QWheelEvent(
                QtCore.QPointF(5, 5), QtCore.QPointF(5, 5),
                QtCore.QPoint(0, 0), QtCore.QPoint(0, -120),
                QtCore.Qt.MouseButton.NoButton,
                QtCore.Qt.KeyboardModifier.NoModifier,
                QtCore.Qt.ScrollPhase.NoScrollPhase, False)
        prz = QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(5, 5), QtCore.QPointF(5, 5),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier)
        QtWidgets.QApplication.sendEvent(sp56, prz)      # klik spinbox dulu
        QtWidgets.QApplication.sendEvent(sp56, _wheel56())
        jalan(0.1)
        ok56c = (sp56.value() != 100)                  # dipencet -> roda boleh
        val_after = sp56.value()
        QtWidgets.QApplication.sendEvent(
            sp56, QtCore.QEvent(QtCore.QEvent.Type.Leave))
        QtWidgets.QApplication.sendEvent(sp56, _wheel56())
        jalan(0.1)
        ok56d = (sp56.value() == val_after)            # keluar -> nonaktif lagi
        ok56 = ok56a and ok56b and ok56c and ok56d
        area56.close()
        print("   [56] nilai-tetap=%s scroll-naik=%s klik-dulu=%s "
              "keluar-nonaktif=%s" % (ok56a, ok56b, ok56c, ok56d))
        print("[56] wheel guard (scroll di combo tak mengubah nilai):", ok56)
        lulus &= bool(ok56)
    except Exception as exc:                               # noqa: BLE001
        print("[56] wheel guard GAGAL:", repr(exc))
        lulus = False

    # [57] kamera ON/OFF (preview) — hemat CPU: player stop/start + tile off.
    try:
        w._toggle_cam("front", False)
        jalan(0.7)
        s1 = (getattr(w, "cam_aktif", {}).get("front") is False)
        s2 = (not w.btn_rec["front"].isEnabled())
        s3 = (not w.btn_snap["front"].isEnabled())
        s3b = w.btn_cam["front"].text() == "OFF"
        w._toggle_cam("front", True)
        jalan(0.6)
        s4 = (w.cam_aktif.get("front") is True)
        s5 = w.btn_rec["front"].isEnabled()
        s6 = w.btn_snap["front"].isEnabled()
        s6b = w.btn_cam["front"].text() == "ON"
        try:
            import mcm as _M57
            ok57m = hasattr(w, "mcm") and \
                (_M57.KAM_STREAM.get("front") == "UDP Stream 0")
        except Exception:                                   # noqa: BLE001
            ok57m = False
        ok57 = s1 and s2 and s3 and s3b and s4 and s5 and s6 and s6b and ok57m
        print("   [57] sub: off=%s rec_off=%s snap_off=%s lbl_off=%s on=%s "
              "rec_on=%s snap_on=%s lbl_on=%s"
              % (s1, s2, s3, s3b, s4, s5, s6, s6b))
        print("[57] kamera ON/OFF (preview hemat CPU):", ok57)
        lulus &= bool(ok57)
    except Exception as exc:                               # noqa: BLE001
        print("[57] kamera ON/OFF GAGAL:", repr(exc))
        lulus = False

    # [58] panel tengah = rasio kamera front (tanpa border hitam/letterbox).
    try:
        jalan(0.9)
        t58 = w.P["cam_front"].tile
        asp58 = getattr(t58, "_aspect", 16.0 / 9.0)
        ph58 = max(1, t58.height() - 18)
        ideal58 = t58.heightForWidth(t58.width())
        ok58 = (t58.width() <= int(ph58 * asp58) + 3) and \
            (t58.height() >= ideal58 - 6)
        print("   [58] tile %dx%d asp=%.3f ideal-h=%d"
              % (t58.width(), t58.height(), asp58, ideal58))
        print("[58] panel tengah fit resolusi (tanpa border):", ok58)
        lulus &= bool(ok58)
    except Exception as exc:                               # noqa: BLE001
        print("[58] panel tengah rasio GAGAL:", repr(exc))
        lulus = False

    # [59] bar persen (battery/storage) + teks % di sampingnya.
    try:
        import widgets as _W59
        ok59 = hasattr(_W59, "BarPersen")
        if ok59:
            b59 = _W59.BarPersen(tinggi=8)
            b59.setValue(120)
            b59.grab()
            ok59 = ok59 and abs(b59.value() - 100.0) < 0.01
            b59.setValue(-5)
            ok59 = ok59 and abs(b59.value() - 0.0) < 0.01
            b59.setValue(57.5)
            ok59 = ok59 and abs(b59.value() - 57.5) < 0.01
            b59.setWarna("#5CC98F")
            b59.grab()
        ok59 = ok59 and all(hasattr(w, n) for n in
                            ("bar_batt", "lbl_batt_pct", "bar_lipo",
                             "lbl_lipo_pct", "bar_disk", "lbl_disk_pct",
                             "bar_disk_pi", "lbl_disk_pi_pct"))
        w._disk_free_mb_override = 12345.0
        w._update_disk()
        ok59 = ok59 and ("%" in w.lbl_disk_pct.text())
        ok59 = ok59 and (0.0 <= w.bar_disk.value() <= 100.0)
        w._disk_free_mb_override = None
        print("   [59] pct-topside=%r" % w.lbl_disk_pct.text())
        print("[59] bar persen battery/storage + teks %:", ok59)
        lulus &= bool(ok59)
    except Exception as exc:                               # noqa: BLE001
        print("[59] bar persen GAGAL:", repr(exc))
        lulus = False

    # [60] tinggi panel kamera STABIL antar siklus sync (anti-ratchet:
    # bug 17 Okt — min-height ikut mengukur slack → panel turun terus).
    try:
        jalan(1.2)
        h0 = w.P["cam_front"].height()
        jalan(2.2)
        h1 = w.P["cam_front"].height()
        ok60 = abs(h1 - h0) <= 3
        print("   [60] panel h0=%d h1=%d" % (h0, h1))
        print("[60] tinggi panel kamera stabil (anti-ratchet):", ok60)
        lulus &= bool(ok60)
    except Exception as exc:                               # noqa: BLE001
        print("[60] stabil GAGAL:", repr(exc))
        lulus = False

    # [61] input anchor manual: tombol COORDS… + jalur set anchor typed.
    try:
        from PySide6 import QtWidgets as _Q61
        tombol61 = [b.text() for b in w.P["peta"].findChildren(_Q61.QPushButton)]
        ok61 = any("COORDS" in t for t in tombol61)
        a0 = dict(w.peta.anchor)
        w._anchor_dipilih(-6.123456, 106.654321)
        ok61 = ok61 and abs(float(w.peta.anchor["lat"]) + 6.123456) < 1e-4 and \
            abs(float(w.peta.anchor["lon"]) - 106.654321) < 1e-4
        w.peta.set_anchor(a0)
        print("   [61] tombol=%r" % tombol61[:9])
        print("[61] input anchor manual (COORDS\u2026):", ok61)
        lulus &= bool(ok61)
    except Exception as exc:                               # noqa: BLE001
        print("[61] anchor manual GAGAL:", repr(exc))
        lulus = False

    # [62] mcm: payload NYALA/MATI stream robot (murni — bentuk pabrik).
    try:
        import mcm as _M62
        ent = {"id": "x", "running": True,
               "video_and_stream": {
                   "name": "UDP Stream 0",
                   "stream_information": {
                       "endpoints": ["udp://192.168.2.1:5600"]},
                   "video_source": {"Local": {
                       "name": "H264 USB Camera: USB Camera",
                       "device_path": "/dev/video2"}}}}
        p62 = _M62.payload_dari_get(ent)
        ok62 = (p62["name"] == "UDP Stream 0"
                and p62["source"] == "/dev/video2"
                and p62["stream_information"]["endpoints"]
                == ["udp://192.168.2.1:5600"])
        p62b = _M62.payload_dari_get({})
        ok62 = ok62 and p62b["source"] == "" and p62b["name"] == ""
        ok62 = ok62 and _M62.KAM_STREAM == {"front": "UDP Stream 0",
                                            "stereo L": "UDP Stream 1",
                                            "stereo R": "UDP Stream 2"}
        print("   [62] source=%r endpoint=%r" % (
            p62["source"], p62["stream_information"]["endpoints"]))
        print("[62] mcm payload NYALA/MATI stream robot:", ok62)
        lulus &= bool(ok62)
    except Exception as exc:                                # noqa: BLE001
        print("[62] mcm payload GAGAL:", repr(exc))
        lulus = False

    # [63] animasi REC/SNAP capture + panel RAW tanpa duplikat & checkbox.
    try:
        ok63 = True
        w._toggle_rec("front", True)
        jalan(0.4)
        ok63 = ok63 and ("background" in w.btn_rec["front"].styleSheet())
        ok63 = ok63 and w._anim_timer.isActive()
        w._toggle_rec("front", False)
        jalan(0.3)
        ok63 = ok63 and (w.btn_rec["front"].styleSheet() == "")
        ok63 = ok63 and (not w._anim_timer.isActive())
        w._snap("front")
        jalan(0.1)
        ok63 = ok63 and ("background" in w.btn_snap["front"].styleSheet())
        jalan(0.5)
        ok63 = ok63 and (w.btn_snap["front"].styleSheet() == "")
        rp = w.rawpanel
        teks_btn = [b.text() for b in rp.findChildren(QtWidgets.QPushButton)]
        ok63 = ok63 and not any(("RAW L" in t or "RAW F" in t or "STOP" in t)
                                for t in teks_btn)
        ok63 = ok63 and any("SELECT ALL" in t for t in teks_btn)
        ok63 = ok63 and hasattr(rp, "sp_chunk")
        print("   [63] tombol RAW: %r" % teks_btn[:8])
        print("[63] animasi REC/SNAP + RAW tanpa duplikat:", ok63)
        lulus &= bool(ok63)
    except Exception as exc:                                # noqa: BLE001
        print("[63] animasi/RAW GAGAL:", repr(exc))
        lulus = False

    # [64] anchor tersimpan antar-sesi + sensor log topside per sesi.
    try:
        ok64 = True
        import paths as _P64
        _f64 = _P64.aset_json("anchor.json")
        _lama64 = None
        try:
            _lama64 = open(_f64, encoding="utf-8").read()
        except OSError:
            pass
        w._anchor_dipilih(-6.234567, 107.765432)
        try:
            _baru64 = json.load(open(_f64, encoding="utf-8"))
            ok64 = ok64 and abs(float(_baru64["lat"]) + 6.234567) < 1e-4 and \
                abs(float(_baru64["lon"]) - 107.765432) < 1e-4
        except Exception as exc:                            # noqa: BLE001
            ok64 = False
            print("   [64] baca anchor.json gagal:", repr(exc))
        if _lama64 is None:
            try:
                os.remove(_f64)
            except OSError:
                pass
        else:
            open(_f64, "w", encoding="utf-8").write(_lama64)
        _sl = os.path.join(w.sesi, "sensors.csv")
        ok64 = ok64 and os.path.exists(_sl)
        if ok64:
            with open(_sl, encoding="utf-8") as _ff:
                baris64 = _ff.read().strip().splitlines()
            ok64 = (len(baris64) >= 3 and "volt_v" in baris64[0]
                    and "roll_deg" in baris64[0])
            print("   [64] sensors.csv: %d baris | header ok=%s"
                  % (len(baris64), "volt_v" in baris64[0]))
        print("[64] anchor tersimpan + sensor log topside:", ok64)
        lulus &= bool(ok64)
    except Exception as exc:                                # noqa: BLE001
        print("[64] anchor/log GAGAL:", repr(exc))
        lulus = False

    # [65] peta: anchor TIDAK melompat (view dipatok ke titik) + zoom-out luas.
    try:
        w._anchor_dipilih(-6.111111, 106.222222)
        ok65 = (w.peta._pusat_bebas is not None
                and abs(w.peta._pusat_bebas[0] + 6.111111) < 1e-4
                and abs(w.peta._pusat_bebas[1] - 106.222222) < 1e-4)
        ok65 = ok65 and (w.peta.ikuti is False)
        w.peta.zoom(-30)
        ok65 = ok65 and (w.peta._zoom_delta == -20)
        w.peta.zoom(30)
        ok65 = ok65 and (w.peta._zoom_delta == 6)
        w.peta.grab()                       # smoke-paint di zoom-out ekstrem
        print("   [65] pusat=%r delta=%d" % (w.peta._pusat_bebas,
                                              w.peta._zoom_delta))
        print("[65] peta: anchor tak melompat + zoom-out luas:", ok65)
        lulus &= bool(ok65)
    except Exception as exc:                                # noqa: BLE001
        print("[65] peta GAGAL:", repr(exc))
        lulus = False

    # [66] backend ffmpeg Windows: SDP + pemisah JPEG + pencarian ffmpeg.
    try:
        import video as _V66
        s66 = _V66.sdp_untuk(5600)
        ok66 = ("m=video 5600 RTP/AVP 96" in s66
                and "a=rtpmap:96 H264/90000" in s66
                and "a=recvonly" in s66)
        fj66 = b"\xff\xd8AAA\xff\xd9" + b"\xff\xd8BB\xff\xd9" + b"\xff\xd8par"
        fr66, sisa66 = _V66.baca_mjpeg(fj66)
        ok66 = ok66 and len(fr66) == 2 and sisa66 == b"\xff\xd8par"
        ok66 = ok66 and hasattr(_V66, "StreamPlayerFf")
        ok66 = ok66 and (_V66.ffmpeg_path() is None
                         or os.path.isfile(_V66.ffmpeg_path()))
        print("   [66] sdp-ok=%s frames=%d sisa=%d ffmpeg=%r"
              % ("m=video 5600" in s66, len(fr66), len(sisa66),
                 _V66.ffmpeg_path()))
        print("[66] backend ffmpeg (SDP+JPEG+discovery):", ok66)
        lulus &= bool(ok66)
    except Exception as exc:                                # noqa: BLE001
        print("[66] ffmpeg backend GAGAL:", repr(exc))
        lulus = False

    # [67] folder hasil dive = Pictures/DeepVertex-ROV-Captures (akses gampang).
    try:
        import paths as _P67
        p67 = _P67.captures_dir()
        ok67 = p67.replace("\\", "/").endswith("DeepVertex-ROV-Captures") and \
            ("Pictures" in p67 or p67.startswith(os.path.expanduser("~")))
        print("   [67] captures_dir=%r" % p67)
        print("[67] folder hasil di Pictures (mudah diakses):", ok67)
        lulus &= bool(ok67)
    except Exception as exc:                                # noqa: BLE001
        print("[67] captures_dir GAGAL:", repr(exc))
        lulus = False

    # [68] folder media per sesi: default sesi/compressed; custom = root/<sesi>/compressed.
    try:
        ok68 = True
        p68a = w._folder_media()
        ok68 = ok68 and p68a.endswith("compressed") and os.path.isdir(p68a)
        w.media_root = "/tmp/dv_media_custom"
        p68b = w._folder_media()
        ok68 = ok68 and p68b.startswith("/tmp/dv_media_custom") and \
            os.path.isdir(p68b)
        w.media_root = None
        print("   [68] default=%r custom=%r" % (p68a, p68b))
        print("[68] folder media per sesi (ask sekali, custom/default):", ok68)
        lulus &= bool(ok68)
    except Exception as exc:                                # noqa: BLE001
        print("[68] folder media GAGAL:", repr(exc))
        lulus = False

    ok_pre = tunggu(lambda: w.bridge.state.get("prearm_ok") is True, 8)
    print("[16] bit PREARM dari SYS_STATUS:", ok_pre)
    lulus &= bool(ok_pre)

    d = VO.MotorTestDialog(w.bridge, w)
    d.cb_aman.setChecked(True)
    d.sp_dur.setValue(1)
    n209 = len([c for c in baca_stats().get("cmd", []) if c[0] == 209])
    d._test()
    jalan(2.8)
    cmds = baca_stats().get("cmd", [])
    baru = [c for c in cmds if c[0] == 209][n209:]
    ok_mot2 = len(baru) >= 2 and all(len(c) > 5 and c[5] == 2 for c in baru)
    print("[17] motor test berulang >=2x & p6=2:", ok_mot2, "(kiriman: %d)" % len(baru))
    lulus &= bool(ok_mot2)
    d.close()

    hasil = {}
    w.rawpanel.client.sig_result.connect(lambda tag, d: hasil.__setitem__(tag, d))
    w.rawpanel.client.status()
    ok_raw = tunggu(lambda: "status" in hasil, 8) and bool(hasil.get("status", {}).get("ok"))
    print("[10] agen RAW dari panel:", ok_raw)
    lulus &= bool(ok_raw)

    try:
        d1 = VO.SimpleCalDialog(w.bridge, "gyro", w); d1.close()
        d2 = VO.AccelCalDialog(w.bridge, w); d2.close()
        d3 = VO.MotorTestDialog(w.bridge, w); d3.close()
        print("[11] dialog kalibrasi & motor test konstruksi: True")
    except Exception as exc:                               # noqa: BLE001
        print("[11] dialog error:", exc)
        lulus = False

    print("HASIL-UJI-KONSOL:", "LULUS" if lulus else "GAGAL")
    w.close()
    jalan(0.5)
    for s in srcs:
        s.terminate()
    fake.terminate()
    for p in srcs + [fake]:
        try:
            p.wait(timeout=3)
        except Exception:                                  # noqa: BLE001
            p.kill()
    return 0 if lulus else 1


if __name__ == "__main__":
    sys.exit(main())
