from __future__ import annotations

import hashlib
import os
import shutil
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import psutil

import subprocess
import json
import time
import socket
import ctypes
try:
    import winreg
except ImportError:
    winreg = None

from .config import AgentConfig, DATA_DIR
from .models import (
    DiskSummary, FileCandidate, FolderSummary, PerformanceSnapshot, ProcessInfo,
    BatteryDiagnostics, HardwareDiagnostics, SecurityDiagnostics, UpdateDiagnostics,
    StartupAppInfo, SoftwareInfo, NetworkDiagnostics, BackupDiagnostics, AdvancedDiagnostics
)
from .safety import can_terminate_process, is_protected_path



def bytes_to_human(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def collect_disks(config: AgentConfig) -> list[DiskSummary]:
    seen: set[str] = set()
    summaries: list[DiskSummary] = []

    for partition in psutil.disk_partitions(all=False):
        mountpoint = partition.mountpoint
        if not mountpoint or mountpoint in seen:
            continue
        seen.add(mountpoint)
        try:
            usage = shutil.disk_usage(mountpoint)
        except OSError:
            continue
        summaries.append(
            DiskSummary(
                path=mountpoint,
                total_bytes=usage.total,
                used_bytes=usage.used,
                free_bytes=usage.free,
                percent_used=round((usage.used / usage.total) * 100, 1) if usage.total else 0,
            )
        )

    for root in config.storage_roots:
        anchor = root.anchor or str(root)
        if anchor in seen:
            continue
        seen.add(anchor)
        try:
            usage = shutil.disk_usage(anchor)
        except OSError:
            continue
        summaries.append(
            DiskSummary(
                path=anchor,
                total_bytes=usage.total,
                used_bytes=usage.used,
                free_bytes=usage.free,
                percent_used=round((usage.used / usage.total) * 100, 1) if usage.total else 0,
            )
        )
    return summaries


def _walk_files(root: Path, config: AgentConfig, max_files: int | None = None) -> list[Path]:
    if not root.exists() or is_protected_path(root, config):
        return []

    files: list[Path] = []
    for current, dirs, names in os.walk(root):
        current_path = Path(current)
        dirs[:] = [name for name in dirs if not is_protected_path(current_path / name, config)]
        for name in names:
            path = current_path / name
            if is_protected_path(path, config):
                continue
            files.append(path)
            if max_files and len(files) >= max_files:
                return files
    return files


def collect_folders(config: AgentConfig) -> list[FolderSummary]:
    folders: list[FolderSummary] = []
    for root in config.storage_roots:
        total = 0
        count = 0
        for path in _walk_files(root, config, config.scan_max_files_per_root):
            try:
                total += path.stat().st_size
                count += 1
            except OSError:
                continue
        folders.append(FolderSummary(path=str(root), exists=root.exists(), total_bytes=total, file_count=count))
    return folders


def find_large_files(config: AgentConfig) -> list[FileCandidate]:
    threshold = config.large_file_mb * 1024 * 1024
    candidates: list[FileCandidate] = []
    for root in config.storage_roots:
        for path in _walk_files(root, config, config.scan_max_files_per_root):
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size >= threshold:
                candidates.append(
                    FileCandidate(
                        path=str(path),
                        size_bytes=stat.st_size,
                        modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                        reason=f"Larger than {config.large_file_mb} MB",
                    )
                )
    return sorted(candidates, key=lambda item: item.size_bytes, reverse=True)[:25]


def _hash_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def find_duplicate_groups(config: AgentConfig) -> list[list[FileCandidate]]:
    by_size: dict[int, list[Path]] = defaultdict(list)
    max_hash_size = config.duplicate_hash_max_mb * 1024 * 1024
    for root in config.storage_roots:
        for path in _walk_files(root, config, config.duplicate_scan_max_files):
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if 0 < size <= max_hash_size:
                by_size[size].append(path)

    by_hash: dict[str, list[Path]] = defaultdict(list)
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        for path in paths:
            file_hash = _hash_file(path)
            if file_hash:
                by_hash[file_hash].append(path)

    groups: list[list[FileCandidate]] = []
    for paths in by_hash.values():
        if len(paths) < 2:
            continue
        group: list[FileCandidate] = []
        for path in sorted(paths):
            try:
                stat = path.stat()
            except OSError:
                continue
            group.append(
                FileCandidate(
                    path=str(path),
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc),
                    reason="Duplicate content hash",
                )
            )
        if len(group) > 1:
            groups.append(group)
    return sorted(groups, key=lambda group: group[0].size_bytes * (len(group) - 1), reverse=True)[:15]


def collect_performance(config: AgentConfig) -> PerformanceSnapshot:
    memory = psutil.virtual_memory()
    battery = psutil.sensors_battery()
    temperatures: dict[str, float] = {}
    if hasattr(psutil, "sensors_temperatures"):
        try:
            for name, entries in psutil.sensors_temperatures(fahrenheit=False).items():
                if entries:
                    temperatures[name] = round(entries[0].current, 1)
        except (AttributeError, OSError):
            temperatures = {}

    processes: list[ProcessInfo] = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
        try:
            info = proc.info
            pid = int(info["pid"])
            if pid <= 0:
                continue
            mem = info.get("memory_info")
            name = info.get("name") or "unknown"
            can_terminate, protection_reason = can_terminate_process(name, pid, config)
            processes.append(
                ProcessInfo(
                    pid=pid,
                    name=name,
                    cpu_percent=float(info.get("cpu_percent") or 0),
                    memory_bytes=int(mem.rss if mem else 0),
                    status=info.get("status"),
                    can_terminate=can_terminate,
                    protection_reason=None if can_terminate else protection_reason,
                )
            )
        except (psutil.Error, OSError, TypeError, ValueError):
            continue

    top_processes = sorted(processes, key=lambda item: (item.cpu_percent, item.memory_bytes), reverse=True)[:15]
    return PerformanceSnapshot(
        timestamp=datetime.now(timezone.utc),
        cpu_percent=psutil.cpu_percent(interval=0.15),
        memory_percent=memory.percent,
        memory_used_bytes=memory.used,
        memory_total_bytes=memory.total,
        battery_percent=battery.percent if battery else None,
        battery_plugged=battery.power_plugged if battery else None,
        temperatures_celsius=temperatures,
        top_processes=top_processes,
    )


# --- Advanced Diagnostics Windows WMI & Registry Collectors ---

def run_powershell(cmd: str) -> str:
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=4
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception:
        pass
    return ""


class SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong),
                ("i64Size", ctypes.c_int64),
                ("i64NumItems", ctypes.c_int64)]


def get_recycle_bin_size() -> int:
    try:
        rbinfo = SHQUERYRBINFO()
        rbinfo.cbSize = ctypes.sizeof(SHQUERYRBINFO)
        res = ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(rbinfo))
        if res == 0:
            return rbinfo.i64Size
    except Exception:
        pass
    return 0


def collect_startup_apps() -> list[StartupAppInfo]:
    apps: list[StartupAppInfo] = []
    if not winreg:
        return apps
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run")
    ]
    for hive, path in keys:
        try:
            with winreg.OpenKey(hive, path) as key:
                count = winreg.QueryInfoKey(key)[1]
                for i in range(count):
                    name, value, _ = winreg.EnumValue(key, i)
                    apps.append(
                        StartupAppInfo(
                            name=name,
                            command=value,
                            enabled=True,
                            scope="HKCU" if hive == winreg.HKEY_CURRENT_USER else "HKLM"
                        )
                    )
        except OSError:
            continue

    enabled_names = {app.name.lower() for app in apps}
    disabled_path = DATA_DIR / "disabled_startup.json"
    try:
        with disabled_path.open("r", encoding="utf-8") as handle:
            disabled_items = json.load(handle)
    except (OSError, json.JSONDecodeError):
        disabled_items = []

    if isinstance(disabled_items, list):
        for item in disabled_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            command = str(item.get("command") or "")
            if not name or name.lower() in enabled_names:
                continue
            apps.append(
                StartupAppInfo(
                    name=name,
                    command=command,
                    enabled=False,
                    scope=str(item.get("scope") or "Saved")
                )
            )

    return apps


def collect_installed_software() -> list[SoftwareInfo]:
    software: list[SoftwareInfo] = []
    if not winreg:
        return software
    paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
    ]
    seen = set()
    for hive, base_path in paths:
        try:
            with winreg.OpenKey(hive, base_path) as key:
                subkeys_count = winreg.QueryInfoKey(key)[0]
                for i in range(subkeys_count):
                    subkey_name = winreg.EnumKey(key, i)
                    try:
                        with winreg.OpenKey(key, subkey_name) as subkey:
                            try:
                                display_name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                                if not display_name or display_name in seen:
                                    continue
                                seen.add(display_name)
                                
                                try:
                                    display_version = winreg.QueryValueEx(subkey, "DisplayVersion")[0]
                                except OSError:
                                    display_version = "Unknown"
                                try:
                                    publisher = winreg.QueryValueEx(subkey, "Publisher")[0]
                                except OSError:
                                    publisher = "Unknown"
                                try:
                                    install_date = winreg.QueryValueEx(subkey, "InstallDate")[0]
                                except OSError:
                                    install_date = "Unknown"
                                try:
                                    estimated_size = winreg.QueryValueEx(subkey, "EstimatedSize")[0]
                                    size_bytes = int(estimated_size) * 1024
                                except (OSError, ValueError):
                                    size_bytes = 0

                                software.append(
                                    SoftwareInfo(
                                        name=str(display_name),
                                        version=str(display_version),
                                        publisher=str(publisher),
                                        install_date=str(install_date),
                                        size_bytes=size_bytes
                                    )
                                )
                            except OSError:
                                pass
                    except OSError:
                        pass
        except OSError:
            continue
    return sorted(software, key=lambda x: x.name)[:50]  # Cap at top 50 to avoid payload bloat


def collect_folder_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    try:
        count = 0
        for root, dirs, files in os.walk(path):
            depth = len(Path(root).relative_to(path).parts)
            if depth > 1:
                dirs.clear()
                continue
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                    count += 1
                    if count > 500:
                        return total
                except OSError:
                    continue
    except Exception:
        pass
    return total


def collect_temp_size() -> int:
    total = 0
    temp_paths = [Path(os.environ.get("TEMP", "")), Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp"]
    for path in temp_paths:
        if path.exists():
            total += collect_folder_size(path)
    return total


def collect_gpu_percent() -> float:
    out = run_powershell("Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine | Measure-Object -Property UtilizationPercentage -Sum | ConvertTo-Json")
    if out:
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                return float(data.get("Sum", 0.0))
        except Exception:
            pass
    return 0.0


def collect_battery_diagnostics() -> BatteryDiagnostics:
    out = run_powershell("Get-CimInstance Win32_Battery | Select-Object DesignCapacity, FullChargeCapacity | ConvertTo-Json")
    design = 0
    full = 0
    health = 100
    if out:
        try:
            data = json.loads(out)
            if isinstance(data, dict):
                design = data.get("DesignCapacity", 0) or 0
                full = data.get("FullChargeCapacity", 0) or 0
                if design > 0 and full > 0:
                    health = int(round((full / design) * 100))
        except Exception:
            pass
    
    battery = psutil.sensors_battery()
    percent = int(battery.percent) if battery else 100
    remaining = int(battery.secsleft // 60) if battery and battery.secsleft > 0 else None
    
    return BatteryDiagnostics(
        health_percent=health,
        capacity_designed_mwh=design,
        capacity_full_charge_mwh=full,
        cycle_count=None,
        remaining_life_minutes=remaining,
        power_consumption_mw=None
    )


def collect_hardware_diagnostics() -> HardwareDiagnostics:
    out = run_powershell("Get-CimInstance Win32_PnPEntity -Filter 'ConfigManagerErrorCode <> 0' | Select-Object -ExpandProperty Name | ConvertTo-Json")
    failed = []
    if out:
        try:
            data = json.loads(out)
            if isinstance(data, list):
                failed = [str(x) for x in data]
            elif isinstance(data, str):
                failed = [data]
        except Exception:
            pass

    smart_out = run_powershell("Get-WmiObject -Namespace root\\wmi -Class MSStorageDriver_FailurePredictStatus | Select-Object -ExpandProperty PredictFailure | ConvertTo-Json")
    smart_ok = True
    if smart_out:
        try:
            predict = json.loads(smart_out)
            if predict is True or predict == "True":
                smart_ok = False
        except Exception:
            pass
            
    temps = []
    if hasattr(psutil, "sensors_temperatures"):
        try:
            for entries in psutil.sensors_temperatures(fahrenheit=False).values():
                if entries:
                    temps.append(entries[0].current)
        except Exception:
            pass
    is_overheating = any(t > 85 for t in temps)

    return HardwareDiagnostics(
        devices_all_ok=len(failed) == 0,
        failed_devices=failed,
        ssd_smart_ok=smart_ok,
        is_overheating=is_overheating
    )


def collect_security_diagnostics() -> SecurityDiagnostics:
    defender_enabled = True
    def_out = run_powershell("Get-MpComputerStatus | Select-Object AMServiceEnabled, RealTimeProtectionEnabled | ConvertTo-Json")
    if def_out:
        try:
            data = json.loads(def_out)
            if isinstance(data, dict):
                defender_enabled = bool(data.get("AMServiceEnabled", True)) and bool(data.get("RealTimeProtectionEnabled", True))
        except Exception:
            pass

    firewall_enabled = True
    fw_out = run_powershell("Get-NetFirewallProfile | Select-Object Name, Enabled | ConvertTo-Json")
    if fw_out:
        try:
            data = json.loads(fw_out)
            if isinstance(data, list):
                firewall_enabled = all(bool(x.get("Enabled", True)) for x in data)
            elif isinstance(data, dict):
                firewall_enabled = bool(data.get("Enabled", True))
        except Exception:
            pass

    bitlocker_enabled = False
    bl_out = run_powershell("Get-BitLockerVolume | Select-Object ProtectionStatus | ConvertTo-Json")
    if bl_out:
        try:
            data = json.loads(bl_out)
            if isinstance(data, list):
                bitlocker_enabled = any(int(x.get("ProtectionStatus", 0)) == 1 for x in data)
            elif isinstance(data, dict):
                bitlocker_enabled = int(data.get("ProtectionStatus", 0)) == 1
        except Exception:
            pass

    return SecurityDiagnostics(
        defender_enabled=defender_enabled,
        firewall_enabled=firewall_enabled,
        bitlocker_enabled=bitlocker_enabled
    )


def collect_update_diagnostics() -> UpdateDiagnostics:
    count = 0
    if winreg:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired") as key:
                count = 1
        except OSError:
            pass
    return UpdateDiagnostics(pending_updates_count=count)


def collect_network_diagnostics() -> NetworkDiagnostics:
    latency = -1.0
    try:
        start = time.time()
        socket.create_connection(("8.8.8.8", 53), timeout=2)
        latency = round((time.time() - start) * 1000, 1)
    except Exception:
        pass
        
    is_connected = latency > 0
    adapter = "Disconnected"
    if is_connected:
        adapter = "Connected (Wi-Fi/Ethernet)"
        
    return NetworkDiagnostics(
        is_connected=is_connected,
        ping_latency_ms=latency,
        adapter_status=adapter
    )


def collect_advanced_diagnostics(config: AgentConfig) -> AdvancedDiagnostics:
    boot_time_seconds = time.time() - psutil.boot_time()

    # --- Run slow collectors concurrently ---
    def _gpu() -> float:
        return collect_gpu_percent()

    def _battery() -> BatteryDiagnostics:
        return collect_battery_diagnostics()

    def _hardware() -> HardwareDiagnostics:
        return collect_hardware_diagnostics()

    def _security() -> SecurityDiagnostics:
        return collect_security_diagnostics()

    def _network() -> NetworkDiagnostics:
        return collect_network_diagnostics()

    def _temp() -> int:
        return collect_temp_size()

    def _downloads() -> int:
        downloads_path = Path(os.path.expandvars("%USERPROFILE%\\Downloads")).resolve()
        return collect_folder_size(downloads_path)

    results: dict[str, object] = {}
    tasks = {
        "gpu": _gpu,
        "battery": _battery,
        "hardware": _hardware,
        "security": _security,
        "network": _network,
        "temp": _temp,
        "downloads": _downloads,
    }

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception:
                results[name] = None  # individual collector failures are tolerated

    gpu: float = results.get("gpu") or 0.0
    battery: BatteryDiagnostics = results.get("battery") or BatteryDiagnostics(
        health_percent=100, capacity_designed_mwh=0, capacity_full_charge_mwh=0
    )
    hardware: HardwareDiagnostics = results.get("hardware") or HardwareDiagnostics(
        devices_all_ok=True, failed_devices=[], ssd_smart_ok=True, is_overheating=False
    )
    security: SecurityDiagnostics = results.get("security") or SecurityDiagnostics(
        defender_enabled=True, firewall_enabled=True, bitlocker_enabled=False
    )
    network: NetworkDiagnostics = results.get("network") or NetworkDiagnostics(
        is_connected=False, ping_latency_ms=-1.0, adapter_status="Unknown"
    )
    temp_bytes: int = results.get("temp") or 0
    downloads_bytes: int = results.get("downloads") or 0

    recycle_bin_bytes = get_recycle_bin_size()
    startup_apps = collect_startup_apps()
    installed_software = collect_installed_software()

    # --- Health score calculation ---
    score = 100
    if recycle_bin_bytes > 5 * 1024 * 1024 * 1024:
        score -= 5
    if temp_bytes > 5 * 1024 * 1024 * 1024:
        score -= 5
    if battery.health_percent < 80:
        score -= (80 - battery.health_percent) // 2
    if not security.defender_enabled:
        score -= 15
    if not security.firewall_enabled:
        score -= 10
    # Fix: check higher threshold first (was unreachable elif)
    if updates := collect_update_diagnostics():
        if updates.pending_updates_count > 10:
            score -= 10
        elif updates.pending_updates_count > 5:
            score -= 5
    else:
        updates = UpdateDiagnostics(pending_updates_count=0)
    if not hardware.devices_all_ok:
        score -= 15
    if not hardware.ssd_smart_ok:
        score -= 30
    if hardware.is_overheating:
        score -= 15
    if not network.is_connected:
        score -= 20

    score = max(10, min(100, score))

    return AdvancedDiagnostics(
        gpu_percent=gpu,
        boot_time_seconds=boot_time_seconds,
        battery=battery,
        hardware=hardware,
        security=security,
        updates=updates,
        startup_apps=startup_apps,
        installed_software=installed_software,
        network=network,
        backup=BackupDiagnostics(last_backup=None, is_configured=False),
        temp_files_bytes=temp_bytes,
        recycle_bin_bytes=recycle_bin_bytes,
        downloads_bytes=downloads_bytes,
        health_score=score
    )
