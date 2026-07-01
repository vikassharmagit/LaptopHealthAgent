from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from .collectors import bytes_to_human, find_duplicate_groups, find_large_files
from .config import AgentConfig
from .models import ActionType, PerformanceSnapshot, Recommendation, RiskLevel, AdvancedDiagnostics
from .safety import can_terminate_process


def _rec_id() -> str:
    return uuid.uuid4().hex[:12]


def storage_recommendations(config: AgentConfig) -> list[Recommendation]:
    recommendations: list[Recommendation] = []

    for candidate in find_large_files(config):
        recommendations.append(
            Recommendation(
                id=_rec_id(),
                title=f"Review large file: {Path(candidate.path).name}",
                detail=f"{candidate.path} uses {bytes_to_human(candidate.size_bytes)}. Delete it only if you recognize it as disposable.",
                category="storage",
                risk=RiskLevel.medium,
                estimated_benefit=f"Free {bytes_to_human(candidate.size_bytes)}",
                estimated_bytes=candidate.size_bytes,
                action_type=ActionType.delete_file,
                target=candidate.path,
                metadata={"modified_at": candidate.modified_at.isoformat(), "reason": candidate.reason},
            )
        )

    for group in find_duplicate_groups(config):
        keep = group[0]
        for duplicate in group[1:]:
            recommendations.append(
                Recommendation(
                    id=_rec_id(),
                    title=f"Remove duplicate: {Path(duplicate.path).name}",
                    detail=f"Matches {keep.path}. Keeping the first copy would free {bytes_to_human(duplicate.size_bytes)}.",
                    category="storage",
                    risk=RiskLevel.low,
                    estimated_benefit=f"Free {bytes_to_human(duplicate.size_bytes)}",
                    estimated_bytes=duplicate.size_bytes,
                    action_type=ActionType.delete_file,
                    target=duplicate.path,
                    metadata={"matching_file": keep.path, "reason": duplicate.reason},
                )
            )

    return recommendations


def performance_recommendations(config: AgentConfig, snapshot: PerformanceSnapshot) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    for process in snapshot.top_processes:
        allowed, reason = can_terminate_process(process.name, process.pid, config)
        if not allowed:
            continue
        if process.cpu_percent < 25 and process.memory_bytes < 750 * 1024 * 1024:
            continue
        recommendations.append(
            Recommendation(
                id=_rec_id(),
                title=f"Review heavy process: {process.name}",
                detail=f"PID {process.pid} is using {process.cpu_percent:.1f}% CPU and {bytes_to_human(process.memory_bytes)} memory. {reason}",
                category="performance",
                risk=RiskLevel.medium,
                estimated_benefit=f"Recover up to {bytes_to_human(process.memory_bytes)} memory",
                estimated_bytes=process.memory_bytes,
                action_type=ActionType.terminate_process,
                target=str(process.pid),
                metadata={"name": process.name, "cpu_percent": process.cpu_percent},
            )
        )

    if snapshot.memory_percent >= 85:
        recommendations.append(
            Recommendation(
                id=_rec_id(),
                title="Memory pressure is high",
                detail="Close unused apps or browser tabs before starting another heavy workload.",
                category="performance",
                risk=RiskLevel.low,
                estimated_benefit="Improve responsiveness",
                requires_confirmation=False,
            )
        )

    if snapshot.cpu_percent >= 85:
        recommendations.append(
            Recommendation(
                id=_rec_id(),
                title="CPU usage is high",
                detail="Let the current workload finish or review the top processes list.",
                category="performance",
                risk=RiskLevel.low,
                estimated_benefit="Reduce fan noise and heat",
                requires_confirmation=False,
            )
        )

    return recommendations


def generate_diagnostics_recommendations(diagnostics: AdvancedDiagnostics) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    
    # 1. Temp files check
    if diagnostics.temp_files_bytes > 200 * 1024 * 1024:  # 200 MB
        recommendations.append(
            Recommendation(
                id=_rec_id(),
                title="Clean temporary files",
                detail=f"System has accumulated {bytes_to_human(diagnostics.temp_files_bytes)} of temporary files. Cleaning them up will recover disk space safely.",
                category="storage",
                risk=RiskLevel.low,
                estimated_benefit=f"Free {bytes_to_human(diagnostics.temp_files_bytes)}",
                estimated_bytes=diagnostics.temp_files_bytes,
                action_type=ActionType.clean_temp,
                requires_confirmation=True
            )
        )
        
    # 2. Recycle Bin check
    if diagnostics.recycle_bin_bytes > 50 * 1024 * 1024:  # 50 MB
        recommendations.append(
            Recommendation(
                id=_rec_id(),
                title="Empty Recycle Bin",
                detail=f"The Recycle Bin contains {bytes_to_human(diagnostics.recycle_bin_bytes)} of discarded files.",
                category="storage",
                risk=RiskLevel.low,
                estimated_benefit=f"Free {bytes_to_human(diagnostics.recycle_bin_bytes)}",
                estimated_bytes=diagnostics.recycle_bin_bytes,
                action_type=ActionType.empty_recycle_bin,
                requires_confirmation=True
            )
        )
        
    # 3. Defender check
    if not diagnostics.security.defender_enabled:
        recommendations.append(
            Recommendation(
                id=_rec_id(),
                title="Enable Windows Defender",
                detail="Real-time antivirus protection is currently disabled. Protect your computer from malware now.",
                category="organization",
                risk=RiskLevel.high,
                estimated_benefit="Enable antivirus security",
                requires_confirmation=False
            )
        )
        
    # 4. Firewall check
    if not diagnostics.security.firewall_enabled:
        recommendations.append(
            Recommendation(
                id=_rec_id(),
                title="Enable Windows Firewall",
                detail="The network firewall is disabled. Turn it back on to prevent unauthorized network access.",
                category="organization",
                risk=RiskLevel.high,
                estimated_benefit="Enable firewall",
                requires_confirmation=False
            )
        )

    # 5. Pending updates check
    if diagnostics.updates.pending_updates_count > 0:
        recommendations.append(
            Recommendation(
                id=_rec_id(),
                title="Install pending updates",
                detail=f"You have {diagnostics.updates.pending_updates_count} pending Windows Updates. Keeping your system updated ensures security and stability.",
                category="performance",
                risk=RiskLevel.medium,
                estimated_benefit="Install updates",
                action_type=ActionType.install_updates,
                requires_confirmation=True
            )
        )
        
    # 6. High latency network check
    if diagnostics.network.is_connected and diagnostics.network.ping_latency_ms > 150:
        recommendations.append(
            Recommendation(
                id=_rec_id(),
                title="Flush DNS cache",
                detail=f"Your network latency is high ({diagnostics.network.ping_latency_ms} ms). Flushing the DNS cache can resolve network resolution issues.",
                category="performance",
                risk=RiskLevel.low,
                estimated_benefit="Improve network resolution speed",
                action_type=ActionType.flush_dns,
                requires_confirmation=True
            )
        )
        
    # 7. Overheating check
    if diagnostics.hardware.is_overheating:
        recommendations.append(
            Recommendation(
                id=_rec_id(),
                title="System is running hot",
                detail="CPU/hardware temperatures exceed 85°C. Ensure cooling vents are clear or close intensive background tasks.",
                category="performance",
                risk=RiskLevel.high,
                estimated_benefit="Lower temperature and prevent throttling",
                requires_confirmation=False
            )
        )
        
    return recommendations

