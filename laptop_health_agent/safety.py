from __future__ import annotations

from pathlib import Path

from .config import AgentConfig


def is_protected_path(path: Path, config: AgentConfig) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return True

    for protected in config.protected_paths:
        if resolved == protected or protected in resolved.parents:
            return True
    return False


import os

def is_temp_or_recycle_bin(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    temp_env = os.environ.get("TEMP", "")
    temp_dir = Path(temp_env).resolve() if temp_env else None
    sys_temp = Path(os.environ.get("SystemRoot", r"C:\Windows")).resolve() / "Temp"
    recycle_bin = Path("C:\\$Recycle.Bin").resolve()
    
    if temp_dir and (resolved == temp_dir or temp_dir in resolved.parents):
        return True
    if resolved == sys_temp or sys_temp in resolved.parents:
        return True
    if resolved == recycle_bin or recycle_bin in resolved.parents:
        return True
    return False


def can_delete_path(path: Path, config: AgentConfig) -> tuple[bool, str]:
    if not path.exists():
        return False, "Target no longer exists."
    if is_temp_or_recycle_bin(path):
        return True, "Allowed cleanup in temp or recycle bin."
    if not path.is_file():
        return False, "Only individual file deletion is supported."
    if is_protected_path(path, config):
        return False, "Target is inside a protected path."
    return True, "Allowed after explicit confirmation."


def can_terminate_process(name: str, pid: int, config: AgentConfig) -> tuple[bool, str]:
    if pid <= 4:
        return False, "System process IDs are protected."
    if name in config.process_whitelist:
        return False, f"{name} is whitelisted."
    return True, "Allowed after explicit confirmation."


def can_modify_startup(name: str, config: AgentConfig) -> tuple[bool, str]:
    protected_startup = {"securityhealth", "windowsdefender", "antivirus", "cmd.exe", "powershell.exe", "explorer.exe"}
    if name.lower() in protected_startup:
        return False, f"Startup item '{name}' is critical for system operation or security."
    return True, "Allowed after confirmation."

