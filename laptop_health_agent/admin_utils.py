from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from .config import ROOT


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_self_as_admin() -> bool:
    is_frozen = getattr(sys, "frozen", False)
    python_exe = sys.executable

    if is_frozen:
        target = python_exe
        params = ""
    else:
        target = python_exe
        params = f'"{Path(ROOT / "run_app.py")}"'

    ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", target, params, str(ROOT), 1)
    return ret > 32

