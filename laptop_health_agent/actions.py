import subprocess
import os
import shutil
import ctypes
import json
try:
    import winreg
except ImportError:
    winreg = None

from pathlib import Path
import psutil

from .config import AgentConfig, DATA_DIR
from .logging_utils import audit
from .models import ActionType, ApprovalResult, Recommendation
from .safety import can_delete_path, can_terminate_process, can_modify_startup


STARTUP_DISABLED_PATH = DATA_DIR / "disabled_startup.json"
STARTUP_RUN_KEYS = [
    ("HKCU", winreg.HKEY_CURRENT_USER if winreg else None, r"Software\Microsoft\Windows\CurrentVersion\Run"),
    ("HKLM", winreg.HKEY_LOCAL_MACHINE if winreg else None, r"Software\Microsoft\Windows\CurrentVersion\Run"),
]


def _read_disabled_startup_items() -> list[dict[str, str]]:
    try:
        with STARTUP_DISABLED_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _write_disabled_startup_items(items: list[dict[str, str]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with STARTUP_DISABLED_PATH.open("w", encoding="utf-8") as handle:
        json.dump(items, handle, indent=2)


def _remember_disabled_startup_item(name: str, command: str, scope: str, key_path: str) -> None:
    items = [item for item in _read_disabled_startup_items() if item.get("name", "").lower() != name.lower()]
    items.append({"name": name, "command": command, "scope": scope, "key_path": key_path})
    _write_disabled_startup_items(items)


def terminate_process(pid: int, config: AgentConfig, event_id: str = "direct", force: bool = False) -> ApprovalResult:
    try:
        proc = psutil.Process(pid)
        name = proc.name()
    except (psutil.Error, OSError) as exc:
        audit("process_terminate_failed", {"pid": pid, "error": str(exc)})
        return ApprovalResult(recommendation_id=event_id, status="failed", detail=str(exc))

    allowed, reason = can_terminate_process(name, pid, config)
    if not allowed:
        audit("process_terminate_blocked", {"pid": pid, "name": name, "reason": reason})
        return ApprovalResult(recommendation_id=event_id, status="blocked", detail=reason)

    try:
        if force:
            proc.kill()
            proc.wait(timeout=3)
        else:
            proc.terminate()
    except psutil.NoSuchProcess:
        audit("process_killed" if force else "process_terminated", {"pid": pid, "name": name, "source": event_id})
        return ApprovalResult(
            recommendation_id=event_id,
            status="completed",
            detail=f"{name} (PID {pid}) is no longer running.",
        )
    except (psutil.Error, OSError) as exc:
        if not force:
            audit("process_terminate_failed", {"pid": pid, "name": name, "error": str(exc)})
            return ApprovalResult(recommendation_id=event_id, status="failed", detail=str(exc))

        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as taskkill_exc:
            detail = f"Force kill failed for {name} (PID {pid}): {taskkill_exc}"
            audit("process_terminate_failed", {"pid": pid, "name": name, "error": detail})
            return ApprovalResult(recommendation_id=event_id, status="failed", detail=detail)

        if result.returncode != 0:
            output = (result.stderr or result.stdout or str(exc)).strip()
            detail = f"Force kill failed for {name} (PID {pid}): {output}"
            audit("process_terminate_failed", {"pid": pid, "name": name, "error": detail})
            return ApprovalResult(recommendation_id=event_id, status="failed", detail=detail)

        audit("process_taskkill_forced", {"pid": pid, "name": name, "source": event_id})
        return ApprovalResult(
            recommendation_id=event_id,
            status="completed",
            detail=f"Force killed {name} (PID {pid}) with taskkill /T /F.",
        )

    audit("process_killed" if force else "process_terminated", {"pid": pid, "name": name, "source": event_id})
    return ApprovalResult(
        recommendation_id=event_id,
        status="completed",
        detail=f"Force killed {name} (PID {pid})." if force else f"Termination requested for {name} (PID {pid}).",
    )


def execute_recommendation(recommendation: Recommendation, config: AgentConfig) -> ApprovalResult:
    if recommendation.action_type == ActionType.delete_file and recommendation.target:
        path = Path(recommendation.target).resolve()
        allowed, reason = can_delete_path(path, config)
        if not allowed:
            audit("action_blocked", {"recommendation_id": recommendation.id, "reason": reason, "target": str(path)})
            return ApprovalResult(recommendation_id=recommendation.id, status="blocked", detail=reason)
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError as exc:
            audit("action_failed", {"recommendation_id": recommendation.id, "error": str(exc), "target": str(path)})
            return ApprovalResult(recommendation_id=recommendation.id, status="failed", detail=str(exc))
        audit("file_deleted", {"recommendation_id": recommendation.id, "target": str(path), "bytes": size})
        return ApprovalResult(
            recommendation_id=recommendation.id,
            status="completed",
            detail=f"Deleted {path.name}.",
        )

    if recommendation.action_type == ActionType.terminate_process and recommendation.target:
        pid = int(recommendation.target)
        return terminate_process(pid, config, recommendation.id)

    from .collectors import bytes_to_human

    if recommendation.action_type == ActionType.clean_temp:
        bytes_freed = clean_temp_action()
        audit("temp_files_cleaned", {"recommendation_id": recommendation.id, "bytes_freed": bytes_freed})
        return ApprovalResult(
            recommendation_id=recommendation.id,
            status="completed",
            detail=f"Cleaned temporary folders, freeing {bytes_to_human(bytes_freed)}."
        )

    if recommendation.action_type == ActionType.empty_recycle_bin:
        ok = empty_recycle_bin_action()
        audit("recycle_bin_emptied", {"recommendation_id": recommendation.id, "success": ok})
        if ok:
            return ApprovalResult(
                recommendation_id=recommendation.id,
                status="completed",
                detail="Recycle Bin emptied successfully."
            )
        else:
            return ApprovalResult(
                recommendation_id=recommendation.id,
                status="failed",
                detail="Failed to empty Recycle Bin or it was already empty."
            )

    if recommendation.action_type == ActionType.flush_dns:
        ok = flush_dns_action()
        audit("dns_flushed", {"recommendation_id": recommendation.id, "success": ok})
        if ok:
            return ApprovalResult(
                recommendation_id=recommendation.id,
                status="completed",
                detail="DNS cache flushed successfully."
            )
        else:
            return ApprovalResult(
                recommendation_id=recommendation.id,
                status="failed",
                detail="Failed to flush DNS cache."
            )

    if recommendation.action_type == ActionType.restart_explorer:
        ok = restart_explorer_action()
        audit("explorer_restarted", {"recommendation_id": recommendation.id, "success": ok})
        return ApprovalResult(
            recommendation_id=recommendation.id,
            status="completed",
            detail="Windows Explorer restart requested."
        )

    if recommendation.action_type == ActionType.install_updates:
        ok = install_updates_action()
        audit("updates_triggered", {"recommendation_id": recommendation.id, "success": ok})
        return ApprovalResult(
            recommendation_id=recommendation.id,
            status="completed",
            detail="Windows Update download and installation triggered in background."
        )

    if recommendation.action_type == ActionType.disable_startup and recommendation.target:
        allowed, reason = can_modify_startup(recommendation.target, config)
        if not allowed:
            audit("startup_modify_blocked", {"recommendation_id": recommendation.id, "reason": reason})
            return ApprovalResult(recommendation_id=recommendation.id, status="blocked", detail=reason)
            
        ok = disable_startup_action(recommendation.target)
        audit("startup_disabled", {"recommendation_id": recommendation.id, "name": recommendation.target, "success": ok})
        if ok:
            return ApprovalResult(
                recommendation_id=recommendation.id,
                status="completed",
                detail=f"Disabled startup item '{recommendation.target}'."
            )
        else:
            return ApprovalResult(
                recommendation_id=recommendation.id,
                status="failed",
                detail=f"Failed to remove startup item '{recommendation.target}' (might require Administrator privileges)."
            )

    return ApprovalResult(
        recommendation_id=recommendation.id,
        status="blocked",
        detail="This recommendation has no executable action.",
    )


def clean_temp_action() -> int:
    cleaned = 0
    temp_paths = [Path(os.environ.get("TEMP", "")), Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp"]
    for path in temp_paths:
        if not path.exists():
            continue
        try:
            for item in path.iterdir():
                try:
                    if item.is_file() or item.is_symlink():
                        size = item.stat().st_size
                        item.unlink()
                        cleaned += size
                    elif item.is_dir():
                        size = 0
                        for f in item.rglob('*'):
                            if f.is_file():
                                try:
                                    size += f.stat().st_size
                                except OSError:
                                    pass
                        shutil.rmtree(item)
                        cleaned += size
                except Exception:
                    pass
        except Exception:
            pass
    return cleaned


def empty_recycle_bin_action() -> bool:
    try:
        flags = 0x00000001 | 0x00000002 | 0x00000004
        res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
        return res == 0
    except Exception:
        pass
    return False


def flush_dns_action() -> bool:
    try:
        res = subprocess.run(["ipconfig", "/flushdns"], capture_output=True, text=True, timeout=5)
        return res.returncode == 0
    except Exception:
        return False


def restart_explorer_action() -> bool:
    try:
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True, timeout=5)
        subprocess.Popen(["explorer.exe"], shell=True)
        return True
    except Exception:
        return False


def install_updates_action() -> bool:
    try:
        cmd = "$UpdateSession = New-Object -ComObject Microsoft.Update.Session; $UpdateSearcher = $UpdateSession.CreateUpdateSearcher(); $SearchResult = $UpdateSearcher.Search('IsInstalled=0 and Type=\\'Software\\\''); if ($SearchResult.Updates.Count -gt 0) { $Installer = $UpdateSession.CreateUpdateInstaller(); $Installer.Updates = $SearchResult.Updates; $Installer.Install() }"
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd])
        return True
    except Exception:
        return False


def disable_startup_action(name: str) -> bool:
    if not winreg:
        return False
    deleted = False
    for scope, hive, path in STARTUP_RUN_KEYS:
        if hive is None:
            continue
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
                command, _ = winreg.QueryValueEx(key, name)
                winreg.DeleteValue(key, name)
                _remember_disabled_startup_item(name, str(command), scope, path)
                deleted = True
        except OSError:
            continue
    return deleted


def enable_startup_action(name: str) -> bool:
    if not winreg:
        return False

    items = _read_disabled_startup_items()
    item = next((entry for entry in items if entry.get("name", "").lower() == name.lower()), None)
    if not item:
        return False

    scope = item.get("scope", "HKCU")
    command = item.get("command", "")
    key_path = item.get("key_path", r"Software\Microsoft\Windows\CurrentVersion\Run")
    hive = winreg.HKEY_CURRENT_USER if scope == "HKCU" else winreg.HKEY_LOCAL_MACHINE

    try:
        with winreg.OpenKey(hive, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, item.get("name", name), 0, winreg.REG_SZ, command)
    except OSError:
        return False

    remaining = [entry for entry in items if entry.get("name", "").lower() != name.lower()]
    _write_disabled_startup_items(remaining)
    return True
