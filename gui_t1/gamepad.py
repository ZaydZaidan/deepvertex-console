"""gamepad.py — teleop gamepad  (gaya QGC): MANUAL_CONTROL 20 Hz + deadman wajib.

Pemetaan default = SALINAN pemetaan yang sudah diverifikasi di sim (mission_params.yaml):
pad DS4 "Wireless Controller" (hid-sony): sumbu 0=LX 1=LY 2=L2 3=RX 4=RY 5=R2 6=dpadX
7=dpadY; tombol 0=X 1=O 2=△ 3=□ 4=L1 5=R1 6=L2 7=R2 8=Share 9=Options 10=PS 11=L3 12=R3.
Aturan keselamatan sim dibawa ke sini: deadman WAJIB ditahan; tanpa deadman/putus →
netral; keluar → netral + lepas RC override.

evdev dipakai (bukan pygame) supaya URUTAN indeks persis sama dengan pemetaan
verified sim. Config bisa ditimpa lewat gamepad.json di folder yang sama.
VERIFY di kolam: arah z (naik/turun), kanal tilt/lights.
"""
import json
import os
import threading
import time

from PySide6 import QtCore

try:
    import evdev
    from evdev import ecodes
except Exception:                                          # noqa: BLE001
    evdev = None
    ecodes = None

# Windows: dukungan gamepad lewat SDL (pygame) — DS4 DAN Xbox. Nilai dibaca
# dari SDL lalu DITERJEMAHKAN ke kode evdev yang sama sehingga seluruh logika
# pemetaan/preset/deadman di kelas Gamepad bekerja tanpa perubahan.
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
try:
    import pygame
except Exception:                                          # noqa: BLE001
    pygame = None

NT = os.name == "nt"

if ecodes is None:              # Windows: konstanta evdev minimal (nilai ABI tetap)
    class _Ecodes:              # noqa: N801 - pengganti modul ecodes
        EV_ABS, EV_KEY = 3, 1
        ABS_X, ABS_Y, ABS_Z = 0, 1, 2
        ABS_RX, ABS_RY, ABS_RZ = 3, 4, 5
        ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11
        BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
        BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 0x136, 0x137, 0x138, 0x139
        BTN_SELECT, BTN_START, BTN_MODE = 0x13a, 0x13b, 0x13c
        BTN_THUMBL, BTN_THUMBR = 0x13d, 0x13e

    ecodes = _Ecodes

# SDL (Windows/XInput-style) -> kode evdev
_SDL_BTN = {0: "BTN_SOUTH", 1: "BTN_EAST", 2: "BTN_WEST", 3: "BTN_NORTH",
            4: "BTN_TL", 5: "BTN_TR", 6: "BTN_SELECT", 7: "BTN_START",
            8: "BTN_MODE", 9: "BTN_THUMBL", 10: "BTN_THUMBR"}
_SDL_AX = {0: "ABS_X", 1: "ABS_Y", 2: "ABS_RX", 3: "ABS_RY"}
_SDL_TRIG = {4: "ABS_Z", 5: "ABS_RZ"}          # LT/RT axis; digital diturunkan
_TRIG_BTN = {"ABS_Z": "BTN_TL2", "ABS_RZ": "BTN_TR2"}


class _SdlPad:
    """Shim perangkat pygame -> antarmuka minim Gamepad (name/path/close/j)."""

    def __init__(self, joy):
        self.j = joy
        try:
            self.name = joy.get_name() or "gamepad (SDL)"
        except Exception:                                  # noqa: BLE001
            self.name = "gamepad (SDL)"
        self.path = "SDL joystick 0"

    def close(self):
        try:
            self.j.quit()
        except Exception:                                  # noqa: BLE001
            pass

DIR = os.path.dirname(os.path.abspath(__file__))

CFG_DEFAULT = {
    "axis_order": ["ABS_X", "ABS_Y", "ABS_Z", "ABS_RX", "ABS_RY", "ABS_RZ",
                   "ABS_HAT0X", "ABS_HAT0Y"],
    "btn_order": ["BTN_SOUTH", "BTN_EAST", "BTN_NORTH", "BTN_WEST", "BTN_TL", "BTN_TR",
                  "BTN_TL2", "BTN_TR2", "BTN_SELECT", "BTN_START", "BTN_MODE",
                  "BTN_THUMBL", "BTN_THUMBR"],
    "axis_fwd": 1, "axis_lat": 0, "axis_depth": 4, "axis_yaw": 3, "axis_tilt": 7,
    "invert_fwd": False, "invert_lat": True, "invert_depth": True, "invert_yaw": False,
    "invert_tilt": True,
    "deadman_buttons": [4, 5],
    "btn_center_cam": 0, "btn_arm_toggle": -1, "btn_mode_cycle": 8, "btn_depth_hold": 6,
    "btn_record": 3, "btn_photo": 1,     # □ = record toggle, ○ = photo (bisa diremap)
    # btn_arm_toggle = -1: ARM lewat PAD DIMATIKAN sampai uji kolam (tombol ARM di GUI
    # memakai konfirmasi checklist). Aktifkan lagi (9 = Options) saat uji kolam dimulai.
    "mode_base": 19, "mode_hold": 2, "mode_cycle": [19, 2, 0],
    "mode_repeat_s": 1.5,
    "deadzone": 0.05, "rate_hz": 20.0,
    "scale_fwd": 1.0, "scale_lat": 1.0, "scale_yaw": 0.6, "scale_depth": 1.0,
    "tilt_step_per_s": 220.0,          # us per detik saat dpad ditahan
    "tilt_min": 1100, "tilt_max": 1900, "tilt_center": 1500,
    "ch_tilt": 8, "ch_lights": 9,      # VERIFY: default ArduSub (AUX 213 @RC8)
    "disarm_on_exit": False,
}


class Gamepad(QtCore.QObject):
    sig_status = QtCore.Signal(str)
    sig_live = QtCore.Signal(dict)      # {axes:[...], buttons:[bool..], deadman, tilt, mode_idx}
    sig_aksi = QtCore.Signal(str)       # edge aksi: "record" / "photo" / "arm" / "mode" / "center"

    def __init__(self, bridge, cfg_path=None, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.cfg = dict(CFG_DEFAULT)
        path = cfg_path or os.path.join(DIR, "gamepad.json")
        try:
            with open(path, encoding="utf-8") as f:
                self.cfg.update(json.load(f))
        except OSError:
            pass
        self.device = None
        self.dev_name = None
        self.axes = {}
        self.buttons = {}
        self.tilt = int(self.cfg["tilt_center"])
        self.cap = 1.0                      # MAX SPEED (konsol): skala perintah pilot
        self.last_out = (0, 0, 500, 0)      # perintah manual terakhir (utk peta estimasi)
        self.lights = 0.0               # 0..100
        self._mode_idx = 0
        self._prev_deadman = False
        self._prev_btn = {}
        self._last_mode_send = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._konek = False
        self.aktif = False
        # hook UJI tanpa hardware: peta idx->nilai (-1..1) / idx->bool
        self.sim_axes = None
        # MODE MAPPING (halaman PAD MAPPING terbuka): input pad HANYA utk UI.
        self._output_block_n = 0        # penghitung dialog yang memblokir
        self.output_blocked = False
        self.sim_buttons = None

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)

        # preset pad BERNAMA (permintaan pemilik 13 Okt): muat preset aktif dari
        # gamepad_presets.json (folder data user — paths.py; fallback lama app).
        try:
            import paths as _paths
            self.presets_path = _paths.aset_json("gamepad_presets.json")
        except Exception:                              # noqa: BLE001
            self.presets_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "gamepad_presets.json")
        try:
            with open(self.presets_path, encoding="utf-8") as f:
                _data = json.load(f)
            _ganti = (_data.get("presets") or {}).get(_data.get("aktif"))
            if isinstance(_ganti, dict):
                self.terapkan_cfg(_ganti)
        except Exception:                              # noqa: BLE001
            pass

    # ------------------------------------------------------------------ hidup
    def cari_device(self):
        if NT:
            return self._cari_device_sdl()
        if evdev is None:
            return None, "evdev module is not installed"
        kandidat = []
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
            except Exception:                              # noqa: BLE001
                continue
            caps = d.capabilities()
            if ecodes.EV_ABS not in caps or ecodes.EV_KEY not in caps:
                d.close()
                continue
            nama = (d.name or "").lower()
            skor = 0
            if "wireless controller" in nama or "gamepad" in nama or "dualshock" in nama \
                    or "x-box" in nama or "xbox" in nama or "joystick" in nama:
                skor = 2
            abs_codes = [c for c, _ in caps.get(ecodes.EV_ABS, [])]
            if ecodes.ABS_X in abs_codes and ecodes.ABS_RX in abs_codes:
                skor += 1
            if skor:
                kandidat.append((skor, d))
            else:
                d.close()
        kandidat.sort(key=lambda t: -t[0])
        if not kandidat:
            return None, "no gamepad device found"
        dev = kandidat[0][1]
        for _s, d in kandidat[1:]:
            d.close()
        return dev, None

    def start(self):
        if self.aktif:
            return
        dev, err = self.cari_device()
        if dev is None:
            self.sig_status.emit("gamepad: %s" % err)
            self._konek = False
            return
        self.device = dev
        self.dev_name = dev.name
        self._konek = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self._timer.start(int(1000 / max(2.0, self.cfg["rate_hz"])))
        self.aktif = True
        self.sig_status.emit("gamepad connected: %s (%s)" % (dev.name, dev.path))

    def terapkan_cfg(self, ganti):
        """Terapkan bagian config (preset pad). Key tak dikenal diabaikan."""
        dipakai = []
        for k, val in (ganti or {}).items():
            if k in self.cfg:
                self.cfg[k] = val
                dipakai.append(k)
        try:
            self.tilt = int(max(self.cfg["tilt_min"],
                                min(self.cfg["tilt_max"], self.tilt)))
        except Exception:                                  # noqa: BLE001
            pass
        return dipakai

    def simpan_preset(self, nama, sub=None):
        """Simpan config aktif (atau `sub`) sebagai preset bernama + jadikan aktif."""
        try:
            with open(self.presets_path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:                                  # noqa: BLE001
            data = {"aktif": None, "presets": {}}
        data.setdefault("presets", {})[str(nama)] = dict(sub or self.cfg)
        data["aktif"] = str(nama)
        try:
            with open(self.presets_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:                                  # noqa: BLE001
            return False

    def coba_hotplug(self):
        """Coba sambungkan pad yang dicolok BELAKANGAN, tanpa restart konsol."""
        if self.aktif:
            return
        dev, _err = self.cari_device()
        if dev is None:
            return
        try:
            dev.close()          # start() akan membuka sendiri
        except Exception:        # noqa: BLE001
            pass
        self.start()

    def stop(self, keluar=False):
        self.aktif = False
        self._stop.set()
        self._timer.stop()
        try:
            if self.device is not None:
                self.device.close()
        except Exception:                                  # noqa: BLE001
            pass
        self.device = None
        # keselamatan: netral + lepas override
        try:
            for _ in range(10):
                self.bridge.send_manual_control(0, 0, 500, 0, 0)
            self.bridge.rc_release()
            if keluar and self.cfg.get("disarm_on_exit"):
                self.bridge.queue_arm(False)
        except Exception:                                  # noqa: BLE001
            pass
        self.sig_status.emit("gamepad dihentikan (netral dikirim)")

    def set_lights(self, pct):
        self.lights = max(0.0, min(100.0, float(pct)))

    # ------------------------------------------------------------ pembacaan
    def _cari_device_sdl(self):
        """Cari gamepad di Windows lewat SDL (pygame): DS4 maupun Xbox."""
        if pygame is None:
            return None, "pygame module is not installed"
        try:
            pygame.joystick.quit()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            pygame.init()
            pygame.joystick.init()
        except Exception as exc:                           # noqa: BLE001
            return None, "pygame init failed: %s" % exc
        try:
            jumlah = pygame.joystick.get_count()
        except Exception:                                  # noqa: BLE001
            jumlah = 0
        if jumlah <= 0:
            return None, "no gamepad device found"
        try:
            j = pygame.joystick.Joystick(0)
            j.init()
        except Exception as exc:                           # noqa: BLE001
            return None, "gamepad detected but could not be opened: %s" % exc
        return _SdlPad(j), None

    def _sdl_baca(self):
        """Satu putaran baca SDL -> self.axes/self.buttons (kode evdev).

        Dipisah supaya bisa diuji deterministik dengan joystick palsu [49].
        """
        j = self.device.j
        try:
            pygame.event.pump()
        except Exception:                                  # noqa: BLE001
            pass
        for si, nm in _SDL_AX.items():
            try:
                v = float(j.get_axis(si))
            except Exception:                              # noqa: BLE001
                v = 0.0
            self.axes[getattr(ecodes, nm)] = int(max(-1.0, min(1.0, v)) * 32767)
        try:
            if j.get_numhats() and j.get_numhats() > 0:
                hx, hy = j.get_hat(0)
                self.axes[ecodes.ABS_HAT0X] = int(hx) * 32767
                self.axes[ecodes.ABS_HAT0Y] = int(hy) * 32767
        except Exception:                                  # noqa: BLE001
            pass
        for si, nm in _SDL_TRIG.items():
            try:
                v = float(j.get_axis(si))
            except Exception:                              # noqa: BLE001
                v = 0.0
            # normalisasi lintas rentang: rest (-1 atau 0) -> -1; tekan -> +1
            u = v if v < 0 else (2.0 * v - 1.0)
            self.axes[getattr(ecodes, nm)] = int(max(-1.0, min(1.0, u)) * 32767)
            self.buttons[getattr(ecodes, _TRIG_BTN[nm])] = 1 if u > 0.0 else 0
        for si, nm in _SDL_BTN.items():
            try:
                tekan = 1 if j.get_button(si) else 0
            except Exception:                              # noqa: BLE001
                tekan = 0
            self.buttons[getattr(ecodes, nm)] = tekan

    def _read_loop_sdl(self):
        jeda = 1.0 / max(10.0, float(self.cfg.get("rate_hz", 20.0)) * 2.0)
        while not self._stop.is_set():
            try:
                self._sdl_baca()
            except Exception:                              # noqa: BLE001
                self._konek = False
                return
            time.sleep(jeda)

    def _read_loop(self):
        if NT:
            return self._read_loop_sdl()
        try:
            for event in self.device.read_loop():
                if self._stop.is_set():
                    break
                if event.type == ecodes.EV_ABS:
                    self.axes[event.code] = event.value
                elif event.type == ecodes.EV_KEY:
                    self.buttons[event.code] = event.value          # 1 tekan, 0 lepas
        except Exception:                                  # noqa: BLE001
            self._konek = False

    def _norm_axis(self, idx):
        """Nilai ternormalisasi -1..1 dari indeks pemetaan (memakai rentang nyata)."""
        if self.sim_axes is not None:
            return float(self.sim_axes.get(idx, 0.0))
        if not self._konek or idx is None or idx >= len(self.cfg["axis_order"]):
            return 0.0
        kode = getattr(ecodes, self.cfg["axis_order"][idx], None)
        if kode is None or kode not in self.axes:
            return 0.0
        val = self.axes[kode]
        try:
            info = self.device.absinfo(kode)
            if info.max > info.min:
                if info.max == 1 and info.min == -1:           # hat
                    return float(val)
                tengah = (info.max + info.min) / 2.0
                return max(-1.0, min(1.0, (val - tengah) / max(1.0, (info.max - info.min) / 2.0)))
        except Exception:                                      # noqa: BLE001
            pass
        return max(-1.0, min(1.0, val / 32767.0))

    def _btn(self, idx):
        if self.sim_buttons is not None:
            return bool(self.sim_buttons.get(idx, False))
        if not self._konek or idx is None or idx >= len(self.cfg["btn_order"]):
            return False
        kode = getattr(ecodes, self.cfg["btn_order"][idx], None)
        return bool(self.buttons.get(kode, 0)) if kode is not None else False

    # ------------------------------------------------------------------ tick
    def set_output_block(self, on=True):
        """Blokir output ke kendaraan (dipakai saat halaman PAD MAPPING terbuka).

        Input pad TETAP mengalir ke UI lewat sig_live (remap + highlight), tapi
        TIDAK ada perintah yang dikirim: manual control netral, override tilt
        dilepas, dan tombol edge (arm/mode/center/record/photo) diabaikan.
        Pakai penghitung supaya aman bila dialog dibuka lebih dari sekali.
        """
        self._output_block_n = max(0, self._output_block_n + (1 if on else -1))
        baru = self._output_block_n > 0
        if baru and not self.output_blocked:
            try:
                self.bridge.rc_release()
            except Exception:                          # noqa: BLE001
                pass
        self.output_blocked = baru
        return self.output_blocked

    def _tick(self):
        c = self.cfg
        dz = c["deadzone"]
        fwd = self._norm_axis(c["axis_fwd"])
        lat = self._norm_axis(c["axis_lat"])
        dep = self._norm_axis(c["axis_depth"])
        yaw = self._norm_axis(c["axis_yaw"])
        tlt = self._norm_axis(c["axis_tilt"])
        if c["invert_fwd"]:
            fwd = -fwd
        if c["invert_lat"]:
            lat = -lat
        if c["invert_depth"]:
            dep = -dep
        if c["invert_yaw"]:
            yaw = -yaw

        def dz0(v):
            return 0.0 if abs(v) < dz else v

        fwd, lat, dep, yaw = dz0(fwd), dz0(lat), dz0(dep), dz0(yaw)

        if self.sim_axes is not None:
            self._konek = True

        if self.output_blocked:
            # MODE MAPPING: kendaraan tidak disentuh sama sekali — hanya UI yang
            # menerima data (sig_live utk dialog remap). Manual control netral dan
            # tombol edge TIDAK diproses (contoh: D-PAD atas tidak menggerakkan tilt).
            try:
                self.bridge.send_manual_control(0, 0, 500, 0, 0)
            except Exception:                          # noqa: BLE001
                pass
            self.last_out = (0, 0, 500, 0)
            try:
                self.sig_live.emit({
                    "axes": [self._norm_axis(i) for i in range(len(c["axis_order"]))],
                    "buttons": [self._btn(i) for i in range(len(c["btn_order"]))],
                    "deadman": any(self._btn(b) for b in c["deadman_buttons"]),
                    "tilt": self.tilt, "lights": self.lights,
                    "nama": self.dev_name, "konek": self._konek,
                })
            except Exception:                          # noqa: BLE001
                pass
            return

        deadman = any(self._btn(b) for b in c["deadman_buttons"])
        if deadman:
            x = int(fwd * 1000 * c["scale_fwd"])
            y = int(lat * 1000 * c["scale_lat"])
            r = int(yaw * 1000 * c["scale_yaw"])
            # z: ArduSub 0..1000, netral 500. dep>0 = naik (setelah invert) -> VERIFY kolam
            z = int(500 + dep * 500 * c["scale_depth"])
            # MAX SPEED (konsol): skala seluruh perintah pilot (0.25/0.5/0.75/1.0)
            _cap = float(getattr(self, "cap", 1.0))
            if _cap < 1.0:
                x = int(x * _cap)
                y = int(y * _cap)
                r = int(r * _cap)
                z = int(500 + (z - 500) * _cap)
            self.last_out = (x, y, z, r)
            self.bridge.send_manual_control(x, y, z, r, 0)
        else:
            self.bridge.send_manual_control(0, 0, 500, 0, 0)
            self.last_out = (0, 0, 500, 0)

        # tombol (edge)
        sekarang = time.monotonic()
        for idx, aksi in ((c["btn_arm_toggle"], "arm"), (c["btn_mode_cycle"], "mode"),
                          (c["btn_center_cam"], "center"),
                          (c.get("btn_record", -1), "record"), (c.get("btn_photo", -1), "photo")):
            kini = self._btn(idx)
            if kini and not self._prev_btn.get(idx, False):
                if aksi in ("record", "photo"):
                    self.sig_aksi.emit(aksi)
                elif aksi == "arm":
                    self.bridge.queue_arm(not bool(self.bridge.state.get("armed")))
                elif aksi == "mode" and sekarang - self._last_mode_send > c["mode_repeat_s"]:
                    daftar = c["mode_cycle"]
                    self._mode_idx = (self._mode_idx + 1) % len(daftar)
                    self.bridge.queue_mode(daftar[self._mode_idx])
                    self._last_mode_send = sekarang
                elif aksi == "center":
                    self.tilt = int(c["tilt_center"])
            self._prev_btn[idx] = kini

        # depth hold momentary (L2 tahan)
        dh = self._btn(c["btn_depth_hold"])
        dh_prev = self._prev_btn.get("dh", False)
        if dh and not dh_prev:
            self.bridge.queue_mode(c["mode_hold"])
        elif (not dh) and dh_prev:
            self.bridge.queue_mode(c["mode_base"])
        self._prev_btn["dh"] = dh

        # tilt dari dpad (rate) + kirim RC override bersama lights
        if abs(tlt) > 0.1:
            arah = -1.0 if c["invert_tilt"] else 1.0
            dt = 1.0 / max(2.0, c["rate_hz"])
            self.tilt += arah * tlt * c["tilt_step_per_s"] * dt
            self.tilt = int(max(c["tilt_min"], min(c["tilt_max"], self.tilt)))
        pwm_lights = int(1100 + (self.lights / 100.0) * 800)
        self.bridge.rc_override({c["ch_tilt"]: self.tilt, c["ch_lights"]: pwm_lights})

        self.sig_live.emit({
            "axes": [self._norm_axis(i) for i in range(len(c["axis_order"]))],
            "buttons": [self._btn(i) for i in range(len(c["btn_order"]))],
            "deadman": deadman, "tilt": self.tilt, "lights": self.lights,
            "nama": self.dev_name, "konek": self._konek,
        })
