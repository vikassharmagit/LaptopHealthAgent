from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psutil

from .actions import clean_temp_action, empty_recycle_bin_action
from .collectors import bytes_to_human, collect_installed_software
from .config import DATA_DIR, AgentConfig
from .logging_utils import audit


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_powershell_json(command: str, timeout: int = 8) -> Any:
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()


def collect_temperature_warnings() -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    if not hasattr(psutil, "sensors_temperatures"):
        return warnings
    try:
        readings = psutil.sensors_temperatures(fahrenheit=False)
    except Exception:
        return warnings
    for sensor, entries in readings.items():
        for entry in entries:
            current = getattr(entry, "current", None)
            if current is None:
                continue
            level = "critical" if current >= 90 else "warning" if current >= 80 else "ok"
            if level != "ok":
                warnings.append(
                    {
                        "sensor": sensor,
                        "label": getattr(entry, "label", "") or sensor,
                        "temperature_c": round(float(current), 1),
                        "level": level,
                    }
                )
    return warnings


def scan_event_viewer() -> dict[str, Any]:
    command = (
        "Get-WinEvent -FilterHashtable @{LogName='System'; Level=1,2; "
        "StartTime=(Get-Date).AddDays(-7)} -MaxEvents 20 | "
        "Select-Object TimeCreated,ProviderName,Id,LevelDisplayName,Message | ConvertTo-Json -Depth 3"
    )
    data = run_powershell_json(command, timeout=12)
    if not data:
        events: list[dict[str, Any]] = []
    elif isinstance(data, list):
        events = data
    else:
        events = [data]
    return {"count": len(events), "events": events[:10]}


def scan_minidumps() -> dict[str, Any]:
    dump_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Minidump"
    dumps: list[dict[str, Any]] = []
    if dump_dir.exists():
        for path in sorted(dump_dir.glob("*.dmp"), key=lambda item: item.stat().st_mtime, reverse=True)[:10]:
            try:
                stat = path.stat()
            except OSError:
                continue
            dumps.append(
                {
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                }
            )
    return {"count": len(dumps), "dumps": dumps}


def scan_driver_health() -> dict[str, Any]:
    command = (
        "Get-CimInstance Win32_PnPEntity -Filter 'ConfigManagerErrorCode <> 0' | "
        "Select-Object Name,ClassGuid,ConfigManagerErrorCode | ConvertTo-Json -Depth 3"
    )
    data = run_powershell_json(command)
    if not data:
        devices: list[dict[str, Any]] = []
    elif isinstance(data, list):
        devices = data
    else:
        devices = [data]
    return {"failed_count": len(devices), "devices": devices[:20]}


def parse_battery_report() -> dict[str, Any]:
    report_path = DATA_DIR / "battery-report.html"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["powercfg", "/batteryreport", f"/output", str(report_path)],
            capture_output=True,
            text=True,
            timeout=12,
        )
    except Exception:
        return {"available": False, "path": str(report_path), "detail": "Battery report command failed."}
    return {"available": report_path.exists(), "path": str(report_path), "detail": "Battery report generated."}


def app_uninstall_recommendations() -> list[dict[str, Any]]:
    software = collect_installed_software()
    return [
        {
            "name": item.name,
            "publisher": item.publisher,
            "version": item.version,
            "size_bytes": item.size_bytes,
            "reason": "Large installed application" if item.size_bytes else "Review if unused",
        }
        for item in sorted(software, key=lambda item: item.size_bytes, reverse=True)[:10]
    ]


def browser_cache_paths() -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    roaming = Path(os.environ.get("APPDATA", ""))
    return [
        local / "Google" / "Chrome" / "User Data" / "Default" / "Cache",
        local / "Google" / "Chrome" / "User Data" / "Default" / "Code Cache",
        local / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache",
        local / "Microsoft" / "Edge" / "User Data" / "Default" / "Code Cache",
        roaming / "Mozilla" / "Firefox" / "Profiles",
    ]


def folder_size(path: Path, max_files: int = 5000) -> int:
    total = 0
    count = 0
    try:
        exists = path.exists()
    except OSError:
        return 0
    if not exists:
        return 0
    try:
        for current, _, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(current) / name).stat().st_size
                except OSError:
                    pass
                count += 1
                if count >= max_files:
                    return total
    except OSError:
        return total
    return total


def browser_cache_summary() -> dict[str, Any]:
    entries = []
    total = 0
    for path in browser_cache_paths():
        try:
            size = folder_size(path)
        except OSError:
            continue
        if size:
            total += size
            entries.append({"path": str(path), "size_bytes": size, "size": bytes_to_human(size)})
    return {"total_bytes": total, "total": bytes_to_human(total), "entries": entries}


def clean_browser_cache() -> int:
    cleaned = 0
    for path in browser_cache_paths():
        try:
            exists = path.exists()
        except OSError:
            continue
        if not exists:
            continue
        if path.name == "Profiles":
            targets = list(path.glob("*/cache2")) + list(path.glob("*/startupCache"))
        else:
            targets = [path]
        for target in targets:
            size = folder_size(target)
            try:
                shutil.rmtree(target)
                cleaned += size
            except OSError:
                pass
    audit("browser_cache_cleaned", {"bytes_freed": cleaned})
    return cleaned


def archive_old_downloads(config: AgentConfig) -> dict[str, Any]:
    downloads = Path(os.path.expandvars(r"%USERPROFILE%\Downloads")).resolve()
    if not downloads.exists():
        return {"status": "not_found", "detail": "Downloads folder was not found.", "bytes_archived": 0}
    cutoff = datetime.now().timestamp() - (config.old_file_days * 86400)
    candidates = []
    for path in downloads.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff and path.suffix.lower() not in {".zip", ".7z", ".rar"}:
                candidates.append(path)
        except OSError:
            continue
    candidates = sorted(candidates, key=lambda item: item.stat().st_mtime)[:50]
    if not candidates:
        return {"status": "not_found", "detail": "No old Downloads files were found to archive.", "bytes_archived": 0}
    archive_dir = downloads / "LaptopHealthArchives"
    archive_dir.mkdir(exist_ok=True)
    archive_path = archive_dir / f"old-downloads-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    archived = 0
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in candidates:
            try:
                archived += path.stat().st_size
                zf.write(path, arcname=path.name)
            except OSError:
                continue
    audit("old_downloads_archived", {"archive": str(archive_path), "bytes": archived, "count": len(candidates)})
    return {
        "status": "completed",
        "detail": f"Archived {len(candidates)} old Downloads files into {archive_path}.",
        "archive": str(archive_path),
        "bytes_archived": archived,
    }


def free_space_safely() -> dict[str, Any]:
    temp = clean_temp_action()
    browser = clean_browser_cache()
    recycle_ok = empty_recycle_bin_action()
    total = temp + browser
    audit("free_space_safely", {"temp_bytes": temp, "browser_bytes": browser, "recycle_bin": recycle_ok})
    return {
        "status": "completed",
        "detail": f"Freed at least {bytes_to_human(total)} from temp and browser cache. Recycle Bin empty requested: {recycle_ok}.",
        "bytes_freed": total,
        "recycle_bin_emptied": recycle_ok,
    }


def build_fix_center(config: AgentConfig) -> dict[str, Any]:
    admin = is_admin()
    browser = browser_cache_summary()
    temps = collect_temperature_warnings()
    minidumps = scan_minidumps()
    minidump_folder = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Minidump"
    drivers = scan_driver_health()
    events = scan_event_viewer()
    uninstall = app_uninstall_recommendations()
    battery_report = parse_battery_report()
    alerts = []
    if not admin:
        alerts.append("Some fixes require Run as Administrator.")
    if temps:
        alerts.append("Temperature warning detected.")
    if minidumps["count"]:
        alerts.append("BSOD minidump files were found.")
    if drivers["failed_count"]:
        alerts.append("Driver/device issues were found.")
    if events["count"]:
        alerts.append("Recent critical/error system events were found.")
    return {
        "admin": {"is_admin": admin, "message": "Running as Administrator" if admin else "Run as Administrator for protected fixes"},
        "alerts": alerts,
        "temperature": {"warnings": temps},
        "event_viewer": events,
        "bsod": {"folder": str(minidump_folder), **minidumps},
        "drivers": drivers,
        "battery_report": battery_report,
        "uninstall_recommendations": uninstall,
        "browser_cache": browser,
        "categories": [
            {"name": "Storage", "status": "Review", "actions": ["Free Space Safely", "Archive Old Downloads", "Clean Browser Cache"]},
            {"name": "Performance", "status": "Monitor", "actions": ["Review heavy processes", "Startup Management"]},
            {"name": "Startup", "status": "Manage", "actions": ["Disable or enable startup apps"]},
            {"name": "Network", "status": "Repair", "actions": ["Flush DNS"]},
            {"name": "Security", "status": "Review", "actions": ["Windows Security", "Admin mode check"]},
            {"name": "Battery", "status": "Analyze", "actions": ["Battery report", "Power settings"]},
        ],
    }
