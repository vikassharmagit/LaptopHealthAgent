from __future__ import annotations

import psutil

from .actions import terminate_process
from .config import AgentConfig
from .logging_utils import audit
from .models import ApprovalResult, PortKillResult, PortLookupResult, PortProcessInfo
from .safety import can_terminate_process


def _address_text(address: object) -> str | None:
    if not address:
        return None
    ip = getattr(address, "ip", None)
    port = getattr(address, "port", None)
    if ip is None or port is None:
        return str(address)
    return f"{ip}:{port}"


def lookup_port(port: int, config: AgentConfig) -> PortLookupResult:
    matches: list[PortProcessInfo] = []
    seen: set[int | None] = set()

    connections = [
        conn
        for conn in psutil.net_connections(kind="inet")
        if conn.laddr and getattr(conn.laddr, "port", None) == port and conn.pid and conn.pid > 0
    ]
    connections.sort(key=lambda conn: (conn.status != psutil.CONN_LISTEN, conn.pid or 0))

    for conn in connections:
        local_port = getattr(conn.laddr, "port", None) if conn.laddr else None
        if local_port != port:
            continue

        pid = conn.pid
        name = "unknown"
        can_terminate = False
        protection_reason = "No owning process was reported."
        if pid is not None:
            try:
                proc = psutil.Process(pid)
                name = proc.name()
                can_terminate, protection_reason = can_terminate_process(name, pid, config)
            except (psutil.Error, OSError) as exc:
                protection_reason = str(exc)

        local_address = _address_text(conn.laddr) or f"*:{port}"
        remote_address = _address_text(conn.raddr)
        if pid in seen:
            continue
        seen.add(pid)

        matches.append(
            PortProcessInfo(
                port=port,
                pid=pid,
                name=name,
                status=conn.status,
                local_address=local_address,
                remote_address=remote_address,
                can_terminate=can_terminate,
                protection_reason=None if can_terminate else protection_reason,
            )
        )

    result = PortLookupResult(port=port, found=bool(matches), processes=matches)
    audit("port_lookup", {"port": port, "match_count": len(matches)})
    return result


def kill_port(port: int, config: AgentConfig) -> PortKillResult:
    lookup = lookup_port(port, config)
    if not lookup.found:
        audit("port_kill_not_found", {"port": port})
        return PortKillResult(port=port, status="not_found", detail=f"No process is using port {port}.")

    pids = sorted({item.pid for item in lookup.processes if item.pid is not None})
    if not pids:
        audit("port_kill_blocked", {"port": port, "reason": "No owning process was reported."})
        return PortKillResult(port=port, status="blocked", detail="No owning process was reported.")

    results: list[ApprovalResult] = []
    for pid in pids:
        results.append(terminate_process(pid, config, event_id=f"port:{port}", force=True))

    completed = [result for result in results if result.status == "completed"]
    failed_or_blocked = [result for result in results if result.status in {"blocked", "failed"}]
    if completed and failed_or_blocked:
        status = "partial"
    elif completed:
        status = "completed"
    elif any(result.status == "failed" for result in results):
        status = "failed"
    else:
        status = "blocked"

    detail = "; ".join(result.detail for result in results) or f"No action taken for port {port}."
    audit("port_kill_result", {"port": port, "status": status, "pid_count": len(pids)})
    return PortKillResult(port=port, status=status, detail=detail, results=results)
