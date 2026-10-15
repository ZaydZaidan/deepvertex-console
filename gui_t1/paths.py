"""Lokasi data DeepVertex Marine Console — satu sumber kebenaran.

Hierarki (rapi per-platform):
* Data aplikasi (layout/preset/parameter/failsafe): data_dir()/saved/
  - Windows : %LOCALAPPDATA%\\DeepVertexMarineConsole\\saved
  - Linux   : $XDG_CONFIG_HOME/deepvertex-console/saved (~/.config/...)
  - Override: env DV_DATA_DIR (dipakai uji/CI dan run portable).
* Log file : data_dir()/logs/console.log (rolling ringan).
* Hasil capture: ~/DeepVertex-ROV-Captures (default --outdir; konvensi
  lapangan dipertahankan, bisa diubah user).

Legacy: file di <app>/saved/* dimigrasikan otomatis sekali ke saved_dir()
bila belum ada (tidak menghapus sumbernya).
"""
import os
import shutil

APP = os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """Folder data user (dibuat saat dipakai)."""
    env = os.environ.get("DV_DATA_DIR")
    if env:
        return env
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            "~\\AppData\\Local")
        return os.path.join(base, "DeepVertexMarineConsole")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "deepvertex-console")


def saved_dir():
    d = os.path.join(data_dir(), "saved")
    os.makedirs(d, exist_ok=True)
    return d


def logs_dir():
    d = os.path.join(data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def aset_json(nama):
    """Path berkas data user bernama (mis. gamepad_presets.json)."""
    return os.path.join(saved_dir(), nama)


def file_log():
    return os.path.join(logs_dir(), "console.log")


def putar_log(max_bytes=2_000_000):
    """Rotasi ringan: console.log -> console.log.1 bila melebihi max_bytes."""
    p = file_log()
    try:
        if os.path.getsize(p) > max_bytes:
            os.replace(p, p + ".1")
    except OSError:
        pass


def legacy_saved_dir():
    return os.path.join(APP, "saved")


def migrate_legacy():
    """Salin berkas legacy <app>/saved/* ke saved_dir() bila belum ada.

    Return daftar nama berkas yang DISALIN (kosong bila tidak ada apa-apa).
    """
    out = []
    src = legacy_saved_dir()
    if not os.path.isdir(src):
        return out
    dst = saved_dir()
    for nama in sorted(os.listdir(src)):
        p_src = os.path.join(src, nama)
        p_dst = os.path.join(dst, nama)
        if os.path.isfile(p_src) and not os.path.exists(p_dst):
            try:
                shutil.copy2(p_src, p_dst)
                out.append(nama)
            except OSError:
                pass
    return out
