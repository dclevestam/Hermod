"""Register bundled Geist/Geist Mono variable fonts at application startup.

The fonts live in ``assets/fonts/`` and are made visible to Pango by pushing
them into the application-private FontConfig config via ctypes. This leaves
the user's system font cache untouched while still letting Pango/HarfBuzz
resolve ``"Geist"`` and ``"Geist Mono"`` by name.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from pathlib import Path

_FONT_FILES = ("Geist-Variable.ttf", "GeistMono-Variable.ttf")


def _assets_font_dir() -> Path:
    return Path(__file__).resolve().parent / "assets" / "fonts"


def register_bundled_fonts() -> bool:
    """Register Hermod's bundled fonts so Pango can find them.

    Returns True if at least one font was registered successfully. Falls back
    silently if FontConfig is unavailable — the UI still renders with the
    system stack in that case.
    """
    lib_name = ctypes.util.find_library("fontconfig")
    if not lib_name:
        return False
    try:
        fc = ctypes.CDLL(lib_name)
        fc.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        fc.FcConfigAppFontAddFile.restype = ctypes.c_int
    except Exception:
        return False

    font_dir = _assets_font_dir()
    if not font_dir.is_dir():
        return False

    added = 0
    for name in _FONT_FILES:
        path = font_dir / name
        if not path.is_file():
            continue
        try:
            ok = fc.FcConfigAppFontAddFile(None, str(path).encode("utf-8"))
        except Exception:
            ok = 0
        if ok:
            added += 1
    return added > 0
