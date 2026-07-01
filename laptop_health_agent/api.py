from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import io
import csv
import logging
import sys
import threading
import time
import subprocess
from pathlib import Path as FsPath

from fastapi import FastAPI, Path, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .actions import empty_recycle_bin_action, execute_recommendation, terminate_process
from .collectors import (
    collect_disks, collect_folders, collect_performance, collect_advanced_diagnostics
)
from .config import ROOT, load_config
from .logging_utils import audit
from .models import (
    ApprovalRequest,
    ApprovalResult,
    AdvancedDiagnostics,
    BatteryDiagnostics,
    BackupDiagnostics,
    HardwareDiagnostics,
    HealthReport,
    NetworkDiagnostics,
    PerformanceSnapshot,
    PortActionRequest,
    PortKillResult,
    PortLookupResult,
    ProcessActionRequest,
    Recommendation,
    SecurityDiagnostics,
    UpdateDiagnostics,
)
from .ports import kill_port, lookup_port
from .recommendations import (
    performance_recommendations, storage_recommendations, generate_diagnostics_recommendations
)
from .history import save_history, get_history
from .ai_assistant import ask_assistant
from .admin_utils import is_admin, relaunch_self_as_admin
from .fix_center import (
    archive_old_downloads,
    browser_cache_summary,
    build_fix_center,
    clean_browser_cache,
    free_space_safely,
)


class ChatQuery(BaseModel):
    query: str
    stats: dict


class RevealPathRequest(BaseModel):
    path: str


class SystemToolRequest(BaseModel):
    tool: str


class AdminStatusResponse(BaseModel):
    is_admin: bool


def _open_shell_target(target: str) -> None:
    import os

    if target.lower().endswith(".exe"):
        subprocess.Popen([target], shell=False)
        return
    os.startfile(target)


def _reveal_in_explorer(target: FsPath) -> None:
    resolved = target.expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        pass

    if resolved.exists() and resolved.is_file():
        subprocess.Popen(f'explorer.exe /select,"{resolved}"', shell=False)
        return

    if resolved.exists() and resolved.is_dir():
        subprocess.Popen(["explorer.exe", str(resolved)], shell=False)
        return

    parent = resolved.parent
    if parent.exists():
        subprocess.Popen(["explorer.exe", str(parent)], shell=False)
        return

    raise FileNotFoundError(str(target))


def _download_exe_path() -> FsPath | None:
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(FsPath(sys.executable))
    candidates.extend(
        [
            ROOT / "dist" / "LaptopHealthAgent.exe",
            ROOT / "dist" / "LaptopHealthAgent" / "LaptopHealthAgent.exe",
        ]
    )
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


app = FastAPI(title="Laptop Health Copilot", version="0.1.0")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

_last_recommendations: dict[str, Recommendation] = {}

# --- Storage recommendation cache (60-second TTL) ---
_storage_rec_cache: list[Recommendation] = []
_storage_rec_cache_ts: float = 0.0
_STORAGE_CACHE_TTL: float = 60.0
_health_scan_lock = threading.Lock()
_health_report_cache: HealthReport | None = None

logger = logging.getLogger("laptop_health")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/download/LaptopHealthAgent.exe", include_in_schema=False)
def download_exe() -> FileResponse:
    exe_path = _download_exe_path()
    if not exe_path:
        raise HTTPException(
            status_code=404,
            detail="LaptopHealthAgent.exe is not available yet. Build the executable first.",
        )
    return FileResponse(
        exe_path,
        filename="LaptopHealthAgent.exe",
        media_type="application/vnd.microsoft.portable-executable",
    )


def _default_diagnostics() -> AdvancedDiagnostics:
    return AdvancedDiagnostics(
        gpu_percent=0.0,
        boot_time_seconds=0.0,
        battery=BatteryDiagnostics(
            health_percent=100,
            capacity_designed_mwh=0,
            capacity_full_charge_mwh=0,
        ),
        hardware=HardwareDiagnostics(
            devices_all_ok=True,
            failed_devices=[],
            ssd_smart_ok=True,
            is_overheating=False,
        ),
        security=SecurityDiagnostics(
            defender_enabled=True,
            firewall_enabled=True,
            bitlocker_enabled=False,
        ),
        updates=UpdateDiagnostics(pending_updates_count=0),
        startup_apps=[],
        installed_software=[],
        network=NetworkDiagnostics(
            is_connected=False,
            ping_latency_ms=-1.0,
            adapter_status="Unknown",
        ),
        backup=BackupDiagnostics(last_backup=None, is_configured=False),
        temp_files_bytes=0,
        recycle_bin_bytes=0,
        downloads_bytes=0,
        health_score=100,
    )


def _scan_health_report(config) -> HealthReport:
    global _health_report_cache, _storage_rec_cache, _storage_rec_cache_ts
    scan_started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            "performance": pool.submit(collect_performance, config),
            "disks": pool.submit(collect_disks, config),
            "folders": pool.submit(collect_folders, config),
            "diagnostics": pool.submit(collect_advanced_diagnostics, config),
        }

        performance = futures["performance"].result()
        disks = futures["disks"].result()
        folders = futures["folders"].result()
        try:
            diagnostics = futures["diagnostics"].result()
        except Exception as exc:
            logger.exception("collect_advanced_diagnostics failed, using defaults: %s", exc)
            diagnostics = _default_diagnostics()

    # --- Storage recommendations with 60-second cache ---
    now = time.monotonic()
    if now - _storage_rec_cache_ts > _STORAGE_CACHE_TTL:
        try:
            _storage_rec_cache = storage_recommendations(config)
        except Exception as exc:
            logger.warning("storage_recommendations failed: %s", exc)
            _storage_rec_cache = []
        _storage_rec_cache_ts = now

    recommendations = [
        *_storage_rec_cache,
        *performance_recommendations(config, performance),
        *generate_diagnostics_recommendations(diagnostics)
    ]
    _last_recommendations.clear()
    _last_recommendations.update({item.id: item for item in recommendations if item.action_type})

    report = HealthReport(
        timestamp=datetime.now(timezone.utc),
        disks=disks,
        folders=folders,
        performance=performance,
        recommendations=recommendations,
        diagnostics=diagnostics
    )

    # Save snapshot to SQLite history
    total_used = sum(d.used_bytes for d in disks)
    total_size = sum(d.total_bytes for d in disks)
    save_history(
        diagnostics.health_score,
        performance.cpu_percent,
        performance.memory_percent,
        total_used,
        total_size,
        performance.battery_percent
    )

    elapsed_ms = round((time.perf_counter() - scan_started) * 1000)
    audit(
        "health_scan",
        {
            "recommendation_count": len(recommendations),
            "disk_count": len(report.disks),
            "folder_count": len(report.folders),
            "health_score": diagnostics.health_score,
            "elapsed_ms": elapsed_ms,
        },
    )
    _health_report_cache = report
    return report


def _refresh_health_cache(config) -> None:
    try:
        _scan_health_report(config)
    except Exception:
        logger.exception("background health scan failed")
    finally:
        try:
            _health_scan_lock.release()
        except RuntimeError:
            pass


def _quick_health_report(config) -> HealthReport:
    try:
        performance = collect_performance(config)
    except Exception:
        logger.exception("quick performance collection failed")
        performance = PerformanceSnapshot(
            timestamp=datetime.now(timezone.utc),
            cpu_percent=0.0,
            memory_percent=0.0,
            memory_used_bytes=0,
            memory_total_bytes=0,
            top_processes=[],
        )

    try:
        disks = collect_disks(config)
    except Exception:
        logger.exception("quick disk collection failed")
        disks = []

    diagnostics = _default_diagnostics()
    recommendations = performance_recommendations(config, performance)
    _last_recommendations.clear()
    _last_recommendations.update({item.id: item for item in recommendations if item.action_type})

    report = HealthReport(
        timestamp=datetime.now(timezone.utc),
        disks=disks,
        folders=[],
        performance=performance,
        recommendations=recommendations,
        diagnostics=diagnostics,
    )
    audit("health_scan_quick_returned", {"reason": "no_cache"})
    return report


@app.get("/api/health", response_model=HealthReport)
def health() -> HealthReport:
    global _health_report_cache
    config = load_config()

    if _health_report_cache is not None:
        if _health_scan_lock.acquire(blocking=False):
            threading.Thread(target=_refresh_health_cache, args=(config,), daemon=True).start()
            audit("health_scan_background_started", {"reason": "cache_available"})
        else:
            audit("health_scan_cache_returned", {"reason": "scan_already_running"})
        return _health_report_cache

    if _health_scan_lock.acquire(blocking=False):
        threading.Thread(target=_refresh_health_cache, args=(config,), daemon=True).start()
        audit("health_scan_background_started", {"reason": "no_cache"})

    return _quick_health_report(config)


@app.get("/api/performance", response_model=PerformanceSnapshot)
def performance() -> PerformanceSnapshot:
    config = load_config()
    snapshot = collect_performance(config)
    audit(
        "performance_sample",
        {
            "cpu_percent": snapshot.cpu_percent,
            "memory_percent": snapshot.memory_percent,
            "process_count": len(snapshot.top_processes),
        },
    )
    return snapshot


@app.post("/api/processes/terminate", response_model=ApprovalResult)
def terminate_process_endpoint(request: ProcessActionRequest) -> ApprovalResult:
    if not request.confirm:
        audit("process_terminate_declined", {"pid": request.pid})
        return ApprovalResult(recommendation_id="direct", status="declined", detail="No action taken.")

    config = load_config()
    return terminate_process(request.pid, config)


@app.get("/api/ports/{port}", response_model=PortLookupResult)
def port_lookup_endpoint(port: int = Path(ge=1, le=65535)) -> PortLookupResult:
    config = load_config()
    return lookup_port(port, config)


@app.post("/api/ports/kill", response_model=PortKillResult)
def kill_port_endpoint(request: PortActionRequest) -> PortKillResult:
    if not request.confirm:
        audit("port_kill_declined", {"port": request.port})
        return PortKillResult(port=request.port, status="declined", detail="No action taken.")

    config = load_config()
    return kill_port(request.port, config)


@app.post("/api/actions/empty-recycle-bin", response_model=ApprovalResult)
def empty_recycle_bin_endpoint(request: ApprovalRequest) -> ApprovalResult:
    if not request.confirm:
        audit("recycle_bin_empty_declined", {"source": "direct"})
        return ApprovalResult(
            recommendation_id="empty_recycle_bin",
            status="declined",
            detail="No action taken.",
        )

    ok = empty_recycle_bin_action()
    audit("recycle_bin_emptied", {"source": "direct", "success": ok})
    if ok:
        return ApprovalResult(
            recommendation_id="empty_recycle_bin",
            status="completed",
            detail="Recycle Bin emptied successfully.",
        )
    return ApprovalResult(
        recommendation_id="empty_recycle_bin",
        status="failed",
        detail="Failed to empty Recycle Bin or it was already empty.",
    )


@app.post("/api/files/reveal", response_model=ApprovalResult)
def reveal_file_endpoint(request: RevealPathRequest) -> ApprovalResult:
    target = FsPath(request.path)
    try:
        _reveal_in_explorer(target)
    except FileNotFoundError:
        audit("file_reveal_failed", {"target": request.path, "reason": "Path not found"})
        return ApprovalResult(
            recommendation_id="reveal_file",
            status="not_found",
            detail="The file or folder no longer exists.",
        )
    except Exception as exc:
        audit("file_reveal_failed", {"target": request.path, "reason": str(exc)})
        return ApprovalResult(
            recommendation_id="reveal_file",
            status="failed",
            detail=str(exc),
        )

    resolved = target.expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        pass

    if resolved.exists() and resolved.is_file():
        audit("file_revealed", {"target": str(resolved), "mode": "select"})
        return ApprovalResult(
            recommendation_id="reveal_file",
            status="completed",
            detail=f"Opened Explorer and selected {resolved.name}.",
        )

    if resolved.exists() and resolved.is_dir():
        audit("file_revealed", {"target": str(resolved), "mode": "folder"})
        return ApprovalResult(
            recommendation_id="reveal_file",
            status="completed",
            detail=f"Opened Explorer at {resolved}.",
        )

    audit("file_revealed", {"target": str(resolved), "mode": "parent_missing_file"})
    return ApprovalResult(
        recommendation_id="reveal_file",
        status="not_found",
        detail=f"The file no longer exists. Opened {resolved.parent}.",
    )


@app.post("/api/system/open", response_model=ApprovalResult)
def open_system_tool_endpoint(request: SystemToolRequest) -> ApprovalResult:
    commands = {
        "storage": ("ms-settings:about", "uri"),
        "performance": ("taskmgr.exe", "exe"),
        "battery": ("ms-settings:powersleep", "uri"),
        "security": ("windowsdefender:", "uri"),
        "event_viewer": ("eventvwr.msc", "uri"),
        "device_manager": ("devmgmt.msc", "uri"),
        "installed_apps": ("ms-settings:appsfeatures", "uri"),
    }
    labels = {
        "storage": "System About",
        "performance": "Task Manager",
        "battery": "Power & Battery",
        "security": "Windows Security",
        "event_viewer": "Event Viewer",
        "device_manager": "Device Manager",
        "installed_apps": "Installed Apps",
    }

    command = commands.get(request.tool)
    if not command:
        audit("system_tool_open_blocked", {"tool": request.tool})
        return ApprovalResult(
            recommendation_id="system_open",
            status="blocked",
            detail="Unknown system tool.",
        )

    target, kind = command
    try:
        if kind == "exe":
            subprocess.Popen([target], shell=False)
        else:
            _open_shell_target(target)
    except OSError as exc:
        audit("system_tool_open_failed", {"tool": request.tool, "error": str(exc)})
        return ApprovalResult(
            recommendation_id="system_open",
            status="failed",
            detail=f"Could not open {labels[request.tool]}: {exc}",
        )

    audit("system_tool_opened", {"tool": request.tool})
    return ApprovalResult(
        recommendation_id="system_open",
        status="completed",
        detail=f"Opened {labels[request.tool]}.",
    )


class StartupActionRequest(BaseModel):
    name: str
    confirm: bool


@app.post("/api/startup/disable", response_model=ApprovalResult)
def startup_disable_endpoint(request: StartupActionRequest) -> ApprovalResult:
    if not request.confirm:
        audit("startup_disable_declined", {"name": request.name})
        return ApprovalResult(
            recommendation_id="startup_disable",
            status="declined",
            detail="No action taken.",
        )

    from .actions import disable_startup_action
    from .safety import can_modify_startup

    config = load_config()
    allowed, reason = can_modify_startup(request.name, config)
    if not allowed:
        audit("startup_disable_blocked", {"name": request.name, "reason": reason})
        return ApprovalResult(
            recommendation_id="startup_disable",
            status="blocked",
            detail=reason,
        )

    ok = disable_startup_action(request.name)
    audit("startup_disabled", {"name": request.name, "success": ok})
    if ok:
        return ApprovalResult(
            recommendation_id="startup_disable",
            status="completed",
            detail=f"Startup item '{request.name}' has been disabled.",
        )
    return ApprovalResult(
        recommendation_id="startup_disable",
        status="failed",
        detail=f"Could not disable '{request.name}'. It may require Administrator privileges.",
    )


@app.post("/api/startup/enable", response_model=ApprovalResult)
def startup_enable_endpoint(request: StartupActionRequest) -> ApprovalResult:
    if not request.confirm:
        audit("startup_enable_declined", {"name": request.name})
        return ApprovalResult(
            recommendation_id="startup_enable",
            status="declined",
            detail="No action taken.",
        )

    from .actions import enable_startup_action
    from .safety import can_modify_startup

    config = load_config()
    allowed, reason = can_modify_startup(request.name, config)
    if not allowed:
        audit("startup_enable_blocked", {"name": request.name, "reason": reason})
        return ApprovalResult(
            recommendation_id="startup_enable",
            status="blocked",
            detail=reason,
        )

    ok = enable_startup_action(request.name)
    audit("startup_enabled", {"name": request.name, "success": ok})
    if ok:
        return ApprovalResult(
            recommendation_id="startup_enable",
            status="completed",
            detail=f"Startup item '{request.name}' has been enabled.",
        )
    return ApprovalResult(
        recommendation_id="startup_enable",
        status="failed",
        detail=f"Could not enable '{request.name}'. It may require Administrator privileges or no saved command was found.",
    )


@app.get("/api/fix-center")
def fix_center_endpoint() -> dict[str, object]:
    config = load_config()
    return build_fix_center(config)


@app.post("/api/relaunch-as-admin")
def relaunch_as_admin() -> dict[str, str]:
    """Relaunch the application with Administrator privileges via UAC prompt."""
    import threading
    import os

    def _do_relaunch() -> None:
        import time
        time.sleep(0.8)  # give the HTTP response time to reach the browser

        launched = relaunch_self_as_admin()
        logger.warning("Admin relaunch requested: %s", launched)
        if launched:
            # Elevation succeeded – terminate this non-elevated process
            logger.warning("Elevation successful, exiting parent process.")
            os._exit(0)

    threading.Thread(target=_do_relaunch, daemon=True).start()
    audit("relaunch_as_admin", {})
    return {"status": "relaunching"}


@app.get("/api/system/admin-status", response_model=AdminStatusResponse)
def admin_status() -> AdminStatusResponse:
    return AdminStatusResponse(is_admin=is_admin())


@app.get("/api/fix-center/browser-cache")
def browser_cache_endpoint() -> dict[str, object]:
    return browser_cache_summary()


@app.post("/api/fix-center/clean-browser-cache", response_model=ApprovalResult)
def clean_browser_cache_endpoint(request: ApprovalRequest) -> ApprovalResult:
    if not request.confirm:
        return ApprovalResult(recommendation_id="clean_browser_cache", status="declined", detail="No action taken.")
    from .collectors import bytes_to_human

    bytes_freed = clean_browser_cache()
    return ApprovalResult(
        recommendation_id="clean_browser_cache",
        status="completed",
        detail=f"Cleaned browser cache, freeing {bytes_to_human(bytes_freed)}.",
    )


@app.post("/api/fix-center/archive-old-files", response_model=ApprovalResult)
def archive_old_files_endpoint(request: ApprovalRequest) -> ApprovalResult:
    if not request.confirm:
        return ApprovalResult(recommendation_id="archive_old_files", status="declined", detail="No action taken.")
    result = archive_old_downloads(load_config())
    return ApprovalResult(
        recommendation_id="archive_old_files",
        status="completed" if result["status"] == "completed" else "not_found",
        detail=str(result["detail"]),
    )


@app.post("/api/fix-center/free-space-safely", response_model=ApprovalResult)
def free_space_safely_endpoint(request: ApprovalRequest) -> ApprovalResult:
    if not request.confirm:
        return ApprovalResult(recommendation_id="free_space_safely", status="declined", detail="No action taken.")
    result = free_space_safely()
    return ApprovalResult(
        recommendation_id="free_space_safely",
        status="completed",
        detail=str(result["detail"]),
    )


@app.post("/api/approve", response_model=ApprovalResult)
def approve(request: ApprovalRequest) -> ApprovalResult:
    if not request.confirm:
        audit("approval_declined", {"recommendation_id": request.recommendation_id})
        return ApprovalResult(
            recommendation_id=request.recommendation_id,
            status="declined",
            detail="No action taken.",
        )

    config = load_config()
    recommendation = _last_recommendations.get(request.recommendation_id)
    if not recommendation:
        audit("approval_not_found", {"recommendation_id": request.recommendation_id})
        return ApprovalResult(
            recommendation_id=request.recommendation_id,
            status="not_found",
            detail="Run a fresh scan and approve one of the current recommendations.",
        )
    return execute_recommendation(recommendation, config)


@app.get("/api/activity")
def activity(limit: int = 50) -> list[dict[str, object]]:
    from .config import LOG_PATH
    import json

    if not LOG_PATH.exists():
        return []
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()[-limit:]
    records: list[dict[str, object]] = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


@app.get("/api/history")
def history_endpoint(limit: int = 50) -> list[dict[str, object]]:
    return get_history(limit)


@app.post("/api/ai/chat")
def ai_chat_endpoint(chat: ChatQuery) -> dict[str, str]:
    response_text = ask_assistant(chat.query, chat.stats)
    return {"response": response_text}


@app.get("/api/reports/csv")
def export_csv_report() -> StreamingResponse:
    history = get_history(1000)
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow([
        "Timestamp", "Health Score", "CPU Percent", 
        "Memory Percent", "Storage Used (Bytes)", 
        "Storage Total (Bytes)", "Battery Percent"
    ])
    
    for row in history:
        writer.writerow([
            row.get("timestamp"),
            row.get("health_score"),
            row.get("cpu_percent"),
            row.get("memory_percent"),
            row.get("storage_used_bytes"),
            row.get("storage_total_bytes"),
            row.get("battery_percent")
        ])
        
    output.seek(0)
    
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=laptop_health_report.csv"}
    )
