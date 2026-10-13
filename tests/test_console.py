#!/usr/bin/env python3
"""Uji konsol T1 offscreen (v2): 3 stream dummy + MAVLink palsu (PARAM/ACK) +
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

from PySide6 import QtWidgets          # noqa: E402

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
    ok_mot2 = len(baru) >= 2 and all(len(c) > 2 and c[2] == 2 for c in baru)
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
