from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class ActionType(str, Enum):
    delete_file = "delete_file"
    terminate_process = "terminate_process"
    clean_temp = "clean_temp"
    empty_recycle_bin = "empty_recycle_bin"
    flush_dns = "flush_dns"
    restart_explorer = "restart_explorer"
    install_updates = "install_updates"
    disable_startup = "disable_startup"



class DiskSummary(BaseModel):
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent_used: float


class FolderSummary(BaseModel):
    path: str
    exists: bool
    total_bytes: int = 0
    file_count: int = 0


class ProcessInfo(BaseModel):
    pid: int
    name: str
    cpu_percent: float
    memory_bytes: int
    status: str | None = None
    can_terminate: bool = False
    protection_reason: str | None = None


class PerformanceSnapshot(BaseModel):
    timestamp: datetime
    cpu_percent: float
    memory_percent: float
    memory_used_bytes: int
    memory_total_bytes: int
    battery_percent: float | None = None
    battery_plugged: bool | None = None
    temperatures_celsius: dict[str, float] = Field(default_factory=dict)
    top_processes: list[ProcessInfo]


class FileCandidate(BaseModel):
    path: str
    size_bytes: int
    modified_at: datetime
    reason: str


class Recommendation(BaseModel):
    id: str
    title: str
    detail: str
    category: Literal["storage", "performance", "organization"]
    risk: RiskLevel
    estimated_benefit: str
    estimated_bytes: int = 0
    action_type: ActionType | None = None
    target: str | None = None
    requires_confirmation: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class BatteryDiagnostics(BaseModel):
    health_percent: int
    capacity_designed_mwh: int
    capacity_full_charge_mwh: int
    cycle_count: int | None = None
    remaining_life_minutes: int | None = None
    power_consumption_mw: int | None = None

class HardwareDiagnostics(BaseModel):
    devices_all_ok: bool
    failed_devices: list[str]
    ssd_smart_ok: bool
    is_overheating: bool

class SecurityDiagnostics(BaseModel):
    defender_enabled: bool
    firewall_enabled: bool
    bitlocker_enabled: bool

class UpdateDiagnostics(BaseModel):
    pending_updates_count: int

class StartupAppInfo(BaseModel):
    name: str
    command: str
    enabled: bool = True
    scope: str

class SoftwareInfo(BaseModel):
    name: str
    version: str
    publisher: str
    install_date: str
    size_bytes: int

class NetworkDiagnostics(BaseModel):
    is_connected: bool
    ping_latency_ms: float
    adapter_status: str

class BackupDiagnostics(BaseModel):
    last_backup: str | None = None
    is_configured: bool = False

class AdvancedDiagnostics(BaseModel):
    gpu_percent: float
    boot_time_seconds: float
    battery: BatteryDiagnostics
    hardware: HardwareDiagnostics
    security: SecurityDiagnostics
    updates: UpdateDiagnostics
    startup_apps: list[StartupAppInfo]
    installed_software: list[SoftwareInfo]
    network: NetworkDiagnostics
    backup: BackupDiagnostics
    temp_files_bytes: int
    recycle_bin_bytes: int
    downloads_bytes: int
    health_score: int = 100


class HealthReport(BaseModel):
    timestamp: datetime
    disks: list[DiskSummary]
    folders: list[FolderSummary]
    performance: PerformanceSnapshot
    recommendations: list[Recommendation]
    diagnostics: AdvancedDiagnostics



class ApprovalRequest(BaseModel):
    recommendation_id: str
    confirm: bool


class ProcessActionRequest(BaseModel):
    pid: int
    confirm: bool


class PortActionRequest(BaseModel):
    port: int = Field(ge=1, le=65535)
    confirm: bool


class PortProcessInfo(BaseModel):
    port: int
    pid: int | None
    name: str
    status: str
    local_address: str
    remote_address: str | None = None
    can_terminate: bool = False
    protection_reason: str | None = None


class PortLookupResult(BaseModel):
    port: int
    found: bool
    processes: list[PortProcessInfo]


class ApprovalResult(BaseModel):
    recommendation_id: str
    status: Literal["completed", "declined", "not_found", "blocked", "failed"]
    detail: str


class PortKillResult(BaseModel):
    port: int
    status: Literal["completed", "declined", "not_found", "blocked", "failed", "partial"]
    detail: str
    results: list[ApprovalResult] = Field(default_factory=list)


def path_from_string(value: str) -> Path:
    return Path(value).expanduser().resolve()
