"""
make_launcher.py — Gera launcher/Forja de Ficção.exe, um pythonw.exe com o ícone da Forja.

Docks e barras de tarefas que mostram o ícone do executável exibiriam o do Python, porque o
app roda no pythonw.exe. Este script copia o pythonw.exe e as DLLs do Python instalado para
launcher/, troca o ícone do executável pelo webapp/static/forja.ico e remove a descrição
"Python" do executável (sem ela, o Windows mostra o nome do arquivo). Um pyvenv.cfg aponta
para o Python instalado, então a biblioteca padrão e os pacotes continuam os de lá.

Uso (Windows):
    python tools/make_launcher.py

É uma ponte até o instalador; a pasta launcher/ fica fora do git e pode ser gerada de novo.
"""

import ctypes
import shutil
import struct
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON = ROOT / "webapp" / "static" / "forja.ico"
OUT_DIR = ROOT / "launcher"
EXE_NAME = "Forja de Ficção.exe"

RT_ICON, RT_GROUP_ICON, RT_VERSION = 3, 14, 16
LOAD_LIBRARY_AS_DATAFILE = 0x2

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.BeginUpdateResourceW.restype = wintypes.HANDLE
k32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
k32.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD,
                                ctypes.c_void_p, wintypes.DWORD]
k32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
k32.LoadLibraryExW.restype = wintypes.HMODULE
k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
k32.FreeLibrary.argtypes = [wintypes.HMODULE]

ENUM_NAME = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
ENUM_LANG = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD,
                               ctypes.c_void_p)
k32.EnumResourceNamesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ENUM_NAME, ctypes.c_void_p]
k32.EnumResourceLanguagesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ENUM_LANG, ctypes.c_void_p]


def existing(exe: Path, rtype: int) -> list[tuple[int, int]]:
    """(id, idioma) dos recursos numéricos de um tipo."""
    mod = k32.LoadLibraryExW(str(exe), None, LOAD_LIBRARY_AS_DATAFILE)
    if not mod:
        raise ctypes.WinError(ctypes.get_last_error())
    found: list[tuple[int, int]] = []

    def on_lang(_m, _t, name, lang, _p):
        found.append((name, lang))
        return True

    lang_cb = ENUM_LANG(on_lang)

    def on_name(m, t, name, _p):
        if name is not None and name >> 16 == 0:  # só ids numéricos
            k32.EnumResourceLanguagesW(m, t, name, lang_cb, None)
        return True

    name_cb = ENUM_NAME(on_name)
    k32.EnumResourceNamesW(mod, rtype, name_cb, None)
    k32.FreeLibrary(mod)
    return found


def read_ico(path: Path) -> list[tuple[bytes, bytes]]:
    """(cabeçalho de 12 bytes da entrada, dados da imagem) de cada imagem do .ico."""
    data = path.read_bytes()
    _reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    if kind != 1:
        raise ValueError(f"{path} não é um .ico")
    images = []
    for i in range(count):
        entry = data[6 + 16 * i: 6 + 16 * (i + 1)]
        size, offset = struct.unpack_from("<II", entry, 8)
        images.append((entry[:8] + struct.pack("<I", size), data[offset: offset + size]))
    return images


def replace_icon(exe: Path, ico: Path):
    old_icons = existing(exe, RT_ICON)
    old_groups = existing(exe, RT_GROUP_ICON)
    old_version = existing(exe, RT_VERSION)
    lang = old_groups[0][1] if old_groups else 0
    handle = k32.BeginUpdateResourceW(str(exe), False)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        for rtype, items in ((RT_ICON, old_icons), (RT_GROUP_ICON, old_groups), (RT_VERSION, old_version)):
            for name, lng in items:
                if not k32.UpdateResourceW(handle, rtype, name, lng, None, 0):
                    raise ctypes.WinError(ctypes.get_last_error())
        images = read_ico(ico)
        group = struct.pack("<HHH", 0, 1, len(images))
        for n, (entry, blob) in enumerate(images, start=1):
            buf = ctypes.create_string_buffer(blob, len(blob))
            if not k32.UpdateResourceW(handle, RT_ICON, n, lang, buf, len(blob)):
                raise ctypes.WinError(ctypes.get_last_error())
            group += entry + struct.pack("<H", n)
        gbuf = ctypes.create_string_buffer(group, len(group))
        if not k32.UpdateResourceW(handle, RT_GROUP_ICON, 1, lang, gbuf, len(group)):
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception:
        k32.EndUpdateResourceW(handle, True)
        raise
    if not k32.EndUpdateResourceW(handle, False):
        raise ctypes.WinError(ctypes.get_last_error())


def main() -> Path:
    if sys.platform != "win32":
        sys.exit("O launcher com ícone só existe no Windows.")
    base = Path(sys.base_prefix)
    pythonw = base / "pythonw.exe"
    if not pythonw.exists():
        sys.exit(f"pythonw.exe não encontrado em {base}")
    OUT_DIR.mkdir(exist_ok=True)
    exe = OUT_DIR / EXE_NAME
    shutil.copy2(pythonw, exe)
    for dll in base.glob("*.dll"):
        shutil.copy2(dll, OUT_DIR / dll.name)
    # Como num venv: a biblioteca padrão e os pacotes instalados vêm do Python de base.
    (OUT_DIR / "pyvenv.cfg").write_text(
        f"home = {base}\ninclude-system-site-packages = true\nversion = {sys.version.split()[0]}\n",
        encoding="utf-8")
    replace_icon(exe, ICON)
    print(f"Launcher gerado: {exe}")
    return exe


if __name__ == "__main__":
    main()
