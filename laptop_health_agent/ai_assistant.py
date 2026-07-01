from __future__ import annotations

import os
import urllib.request
import urllib.error
import json
from .collectors import bytes_to_human


def query_gemini_api(api_key: str, prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
    headers = {"Content-Type": "application/json"}
    body = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
        ]
    }
    
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            # Parse response
            candidates = res_data.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts:
                    return str(parts[0].get("text", "No response text found."))
    except Exception as exc:
        return f"[AI Assistant Error: Failed to call Gemini API - {str(exc)}]"
    return "No reply received from Gemini API."


def local_rules_assistant(query: str, stats: dict) -> str:
    q = query.lower()
    
    # Extract some diagnostics variables
    perf = stats.get("performance", {})
    cpu = perf.get("cpu_percent", 0.0)
    ram = perf.get("memory_percent", 0.0)
    
    diags = stats.get("diagnostics", {})
    score = diags.get("health_score", 100)
    temp_bytes = diags.get("temp_files_bytes", 0)
    rb_bytes = diags.get("recycle_bin_bytes", 0)
    
    battery = diags.get("battery", {})
    bat_health = battery.get("health_percent", 100)
    
    sec = diags.get("security", {})
    defender = sec.get("defender_enabled", True)
    firewall = sec.get("firewall_enabled", True)
    
    updates = diags.get("updates", {})
    pending_updates = updates.get("pending_updates_count", 0)
    
    # 1. Why is my laptop slow?
    if "slow" in q or "performance" in q or "lag" in q:
        reasons = []
        if cpu > 70:
            reasons.append(f"High CPU usage ({cpu}%). There might be background processes consuming processing power.")
        if ram > 80:
            reasons.append(f"High RAM memory utilization ({ram}%). You might have too many browser tabs or heavy apps open.")
        if diags.get("hardware", {}).get("is_overheating", False):
            reasons.append("The processor is running hot (over 85°C), which causes thermal throttling and slows down performance.")
        
        if not reasons:
            reasons.append("Your current CPU and RAM loads are normal. Boot time and startup applications could be factors.")
            
        return (
            f"**Laptop Health Diagnostics:**\n\n"
            f"- Your Laptop Health Score is **{score}/100**.\n"
            f"- " + "\n- ".join(reasons) + "\n\n"
            f"**Recommendations:**\n"
            f"1. Check the 'Top Processes' panel and terminate any non-critical heavy apps.\n"
            f"2. Disable unnecessary startup programs via the Settings/Startup tab.\n"
            f"3. Make sure cooling vents are clear of dust."
        )
        
    # 2. Can I delete this folder / what should I clean?
    if "delete" in q or "clean" in q or "junk" in q or "clear" in q:
        storage_details = []
        if temp_bytes > 0:
            storage_details.append(f"Temporary Files: **{bytes_to_human(temp_bytes)}** can be safely deleted.")
        if rb_bytes > 0:
            storage_details.append(f"Recycle Bin: **{bytes_to_human(rb_bytes)}** of discarded data.")
            
        if not storage_details:
            return "Your system temporary folders are already clean! If you need more space, look at the duplicate files list or large files in your Downloads folder."
            
        return (
            f"Here is a summary of junk storage on your laptop:\n\n"
            + "\n".join(f"- {detail}" for detail in storage_details)
            + "\n\n**Safety Guide:**\n"
            "You can safely clean temporary files, cache, and empty the Recycle Bin. "
            "Never delete folders in C:\\Windows or C:\\Program Files. "
            "Click **Clean temporary files** or **Empty Recycle Bin** on the Recommendations dashboard to execute a safe one-click cleanup."
        )
        
    # 3. Battery drain
    if "battery" in q or "drain" in q or "charge" in q:
        status = "normal" if bat_health > 80 else "degraded"
        return (
            f"**Battery Diagnostics:**\n\n"
            f"- Current Battery Health: **{bat_health}%** (Status: *{status}*).\n"
            f"- Remaining charge: **{perf.get('battery_percent', '--')}%**.\n\n"
            f"**Advice to reduce drain:**\n"
            f"1. Change Windows Power Mode to 'Balanced' or 'Power Saver'.\n"
            f"2. Reduce display brightness.\n"
            f"3. Close heavy background applications like web browsers or game clients."
        )

    # 4. Security
    if "security" in q or "defender" in q or "firewall" in q or "virus" in q:
        issues = []
        if not defender:
            issues.append("Windows Defender Real-time Protection is disabled.")
        if not firewall:
            issues.append("Windows Firewall is disabled.")
            
        if not issues:
            return "Your core security features (Windows Defender & Firewall) are active and protecting your laptop."
            
        return (
            f"**Security Warnings:**\n\n"
            + "\n".join(f"- {iss}" for iss in issues)
            + "\n\n**Action Item:**\n"
            "It is highly recommended to enable Windows Defender and Windows Firewall immediately. "
            "You can review recommended security fixes on the home panel."
        )

    # 5. Generic / Fallback
    return (
        f"Hi! I am your Laptop Health Assistant. I analyze your system state in real time.\n\n"
        f"Currently, your Laptop Health Score is **{score}/100**.\n"
        f"- CPU: **{cpu}%** | Memory: **{ram}%**\n"
        f"- Pending Updates: **{pending_updates}**\n"
        f"- Junk Files: **{bytes_to_human(temp_bytes + rb_bytes)}**\n\n"
        f"Ask me questions like:\n"
        f"- *Why is my laptop slow?*\n"
        f"- *Can I delete temporary folders?*\n"
        f"- *How healthy is my battery?*"
    )


def ask_assistant(query: str, stats: dict) -> str:
    # Check if Gemini API key is configured
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        # Build prompt with context
        context = {
            "health_score": stats.get("diagnostics", {}).get("health_score", 100),
            "cpu_percent": stats.get("performance", {}).get("cpu_percent", 0.0),
            "memory_percent": stats.get("performance", {}).get("memory_percent", 0.0),
            "battery_health_percent": stats.get("diagnostics", {}).get("battery", {}).get("health_percent", 100),
            "temp_files_bytes": stats.get("diagnostics", {}).get("temp_files_bytes", 0),
            "recycle_bin_bytes": stats.get("diagnostics", {}).get("recycle_bin_bytes", 0),
            "pending_updates": stats.get("diagnostics", {}).get("updates", {}).get("pending_updates_count", 0),
            "hardware_all_ok": stats.get("diagnostics", {}).get("hardware", {}).get("devices_all_ok", True),
            "failed_devices": stats.get("diagnostics", {}).get("hardware", {}).get("failed_devices", []),
            "defender_enabled": stats.get("diagnostics", {}).get("security", {}).get("defender_enabled", True),
            "firewall_enabled": stats.get("diagnostics", {}).get("security", {}).get("firewall_enabled", True)
        }
        prompt = (
            f"You are a helpful IT Systems Engineer assistant on Windows called 'Laptop Health Assistant'.\n"
            f"Analyze this laptop telemetry and answer the user's question.\n\n"
            f"Telemetry:\n{json.dumps(context, indent=2)}\n\n"
            f"User Question: {query}\n\n"
            f"Provide a friendly, structured answer in plain Markdown format."
        )
        return query_gemini_api(api_key, prompt)
    else:
        return local_rules_assistant(query, stats)
