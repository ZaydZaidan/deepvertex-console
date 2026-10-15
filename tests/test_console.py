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

from PySide6 import QtCore, QtWidgets    # noqa: E402

SCRATCH = os.path.expanduser("~/.hermes/cache/scratch/t1test")
os.makedirs(SCRATCH, exist_ok=True)
STATS = os.path.join(SCRATCH, "fv_stats.json")


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


def main():
    app = QtWidgets.QApplication(sys.argv)
    try:
        os.remove(STATS)
    except OSError:
        pass
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
        kind36 = w.snap.simulate_move_to(20, 200)
        ok36 = (kind36 == ("rail", "left"))
        w.snap.simulate_drop(20, 200); jalan(0.2)
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
        pt = QtCore.QPoint(tl.x() + peta.width() // 2,
                           tl.y() + int(peta.height() * 0.85))
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
                            tl2.y() + int(tf.height() * 0.85))
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
        w.snap.simulate_drop(20, 400); jalan(0.2)       # tarik ke rel kiri
        ok38 = ok38 and (not w.P["teleop"].isWindow())
        ok38 = ok38 and (w._asal_of("teleop") == ["left", len(w._active_spec
                           .get("left", [])) - 1])
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
        w.snap.simulate_move_to(20, 200)
        w.snap.simulate_drop(20, 200); jalan(0.2)
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
        w.snap.simulate_move_to(20, 200)
        w.snap.simulate_cancel(); jalan(0.1)
        ok40 = (json.dumps(w._active_spec, sort_keys=True) == spec0) and \
            (not w.P["raw"].isWindow())
        # posisi FLOAT pulang ke slot asal ("asal") saat preset diganti
        w.snap.simulate_begin("raw")
        w.snap.simulate_drop(w.width() + 300, 0); jalan(0.2)
        ok40 = ok40 and w.P["raw"].isWindow()
        w.snap.simulate_begin("raw")
        w.snap.simulate_drop(20, 200); jalan(0.2)       # dock kanan dulu? kiri
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
        ok43 = (c4 == 4 and 55 <= p4 <= 65) and (c4f == 4 and p4f == 100) and \
            (c2 == 2 and 55 <= p2 <= 80) and (c2e == 2 and p2e <= 10)
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
