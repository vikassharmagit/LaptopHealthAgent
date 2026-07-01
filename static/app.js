let latestHealthReport = null;
let healthChartInstance = null;
let perfChartInstance = null;

const state = {
  scanning: false,
  sendingChat: false,
};

// Formatting helpers
const escapeHtml = (val) =>
  String(val || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");

const bytesToHuman = (value) => {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = Number(value || 0);
  for (const unit of units) {
    if (size < 1024 || unit === units[units.length - 1]) {
      return unit === "B" ? `${size} B` : `${size.toFixed(1)} ${unit}`;
    }
    size /= 1024;
  }
  return `${value} B`;
};

function defaultDiagnostics() {
  return {
    gpu_percent: 0,
    boot_time_seconds: 0,
    battery: {
      health_percent: 100,
      capacity_designed_mwh: 0,
      capacity_full_charge_mwh: 0,
      remaining_life_minutes: null,
    },
    hardware: {
      devices_all_ok: true,
      failed_devices: [],
      ssd_smart_ok: true,
      is_overheating: false,
    },
    security: {
      defender_enabled: true,
      firewall_enabled: true,
      bitlocker_enabled: false,
    },
    updates: {
      pending_updates_count: 0,
    },
    startup_apps: [],
    installed_software: [],
    network: {
      is_connected: false,
      ping_latency_ms: -1,
      adapter_status: "Unknown",
    },
    backup: {
      last_backup: null,
      is_configured: false,
    },
    temp_files_bytes: 0,
    recycle_bin_bytes: 0,
    downloads_bytes: 0,
    health_score: 100,
  };
}

function normalizeHealthReport(report) {
  const fallback = defaultDiagnostics();
  const diagnostics = report.diagnostics || {};
  report.diagnostics = {
    ...fallback,
    ...diagnostics,
    battery: { ...fallback.battery, ...(diagnostics.battery || {}) },
    hardware: { ...fallback.hardware, ...(diagnostics.hardware || {}) },
    security: { ...fallback.security, ...(diagnostics.security || {}) },
    updates: { ...fallback.updates, ...(diagnostics.updates || {}) },
    network: { ...fallback.network, ...(diagnostics.network || {}) },
    backup: { ...fallback.backup, ...(diagnostics.backup || {}) },
    startup_apps: diagnostics.startup_apps || fallback.startup_apps,
    installed_software: diagnostics.installed_software || fallback.installed_software,
  };
  report.recommendations = report.recommendations || [];
  report.disks = report.disks || [];
  report.folders = report.folders || [];
  report.performance = report.performance || {
    cpu_percent: 0,
    memory_percent: 0,
    battery_percent: null,
    top_processes: [],
  };
  report.performance.top_processes = report.performance.top_processes || [];
  return report;
}

// Render Health Score Ring Fill
function updateHealthRing(score) {
  const textEl = document.getElementById("score-text");
  const fillEl = document.getElementById("score-ring-fill");
  if (!textEl || !fillEl) return;
  
  textEl.textContent = score;
  
  // Calculate dash offset: stroke-dasharray is 157
  const r = 25;
  const c = 2 * Math.PI * r; // ~157.08
  const offset = c - (score / 100) * c;
  fillEl.style.strokeDashoffset = offset;
  
  // Color based on severity
  if (score >= 80) {
    fillEl.style.stroke = "var(--success)";
  } else if (score >= 50) {
    fillEl.style.stroke = "var(--warning)";
  } else {
    fillEl.style.stroke = "var(--danger)";
  }
}

// Tab navigation handler
const navItems = document.querySelectorAll(".nav-item");
const tabPanels = document.querySelectorAll(".tab-panel");
const tabTitle = document.getElementById("tab-title");

navItems.forEach((btn) => {
  btn.addEventListener("click", () => {
    const tabName = btn.dataset.tab;
    
    navItems.forEach((i) => i.classList.remove("active"));
    tabPanels.forEach((p) => p.classList.remove("active"));
    
    btn.classList.add("active");
    const activePanel = document.getElementById(`tab-${tabName}`);
    if (activePanel) {
      activePanel.classList.add("active");
    }
    
    tabTitle.textContent = btn.textContent.trim().replace(/^[^\s]+\s+/, "");
    
    // If user clicked history/reports, draw charts
    if (tabName === "history") {
      renderHistoryCharts();
    }
    if (tabName === "fix-center") {
      renderFixCenter();
    }
  });
});

async function openSystemTool(tool) {
  try {
    const res = await fetch("/api/system/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool })
    });
    const result = await res.json();
    if (!res.ok || result.status !== "completed") {
      window.alert(result.detail || "Could not open the requested system window.");
    }
  } catch (err) {
    window.alert("Could not open the requested system window: " + err.message);
  }
}

let adminPromptRequested = sessionStorage.getItem("laptop-health-admin-prompted") === "1";

async function ensureAdminMode() {
  if (adminPromptRequested) return;
  try {
    const res = await fetch("/api/system/admin-status");
    if (!res.ok) return;
    const data = await res.json();
    if (!data.is_admin) {
      adminPromptRequested = true;
      sessionStorage.setItem("laptop-health-admin-prompted", "1");
      await fetch("/api/relaunch-as-admin", { method: "POST" });
    }
  } catch (err) {
    console.warn("Admin mode check failed:", err);
  }
}

document.querySelectorAll(".status-card[data-ref]").forEach((card) => {
  const tool = card.dataset.ref;
  card.addEventListener("click", () => openSystemTool(tool));
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openSystemTool(tool);
    }
  });
});

// Theme switcher
const themeToggle = document.getElementById("theme-toggle");
themeToggle.addEventListener("click", () => {
  if (document.body.classList.contains("light-theme")) {
    document.body.classList.replace("light-theme", "dark-theme");
    localStorage.setItem("theme", "dark-theme");
  } else {
    document.body.classList.replace("dark-theme", "light-theme");
    localStorage.setItem("theme", "light-theme");
  }
});

// Load saved theme
const savedTheme = localStorage.getItem("theme");
if (savedTheme === "dark-theme") {
  document.body.classList.replace("light-theme", "dark-theme");
}

// API Calls
async function triggerAction(actionId, btnElement) {
  const ok = window.confirm("Are you sure you want to perform this fix? Confirming runs this action on your system.");
  if (!ok) return;
  
  if (btnElement) {
    btnElement.disabled = true;
    btnElement.textContent = "Executing...";
  }
  
  try {
    const res = await fetch("/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recommendation_id: actionId, confirm: true })
    });
    const result = await res.json();
    window.alert(result.detail);
    await performScan();
  } catch (err) {
    window.alert("Failed to execute action: " + err.message);
  } finally {
    if (btnElement) {
      btnElement.disabled = false;
      btnElement.textContent = "Approve";
    }
  }
}

async function terminateProcess(pid, name, btnElement) {
  const ok = window.confirm(`Kill process '${name}' (PID ${pid})? Any unsaved work in this process will be lost.`);
  if (!ok) return;
  
  if (btnElement) {
    btnElement.disabled = true;
    btnElement.textContent = "Killing...";
  }
  
  try {
    const res = await fetch("/api/processes/terminate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pid, confirm: true })
    });
    const result = await res.json();
    window.alert(result.detail);
    await refreshPerformance();
  } catch (err) {
    window.alert("Failed to kill process: " + err.message);
  }
}

async function revealFilePath(path) {
  try {
    const res = await fetch("/api/files/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path })
    });
    const result = await res.json();
    if (result.status !== "completed") {
      window.alert(result.detail || "Could not open the path.");
    }
  } catch (err) {
    window.alert("Failed to open path: " + err.message);
  }
}

function linkifyWindowsPaths(text) {
  const source = String(text || "");
  const pathPattern = /[A-Za-z]:\\[^\r\n<>:"|?*]+/g;
  let html = "";
  let lastIndex = 0;
  const matches = source.matchAll(pathPattern);

  for (const match of matches) {
    const rawPath = cleanWindowsPath(match[0]);
    const start = match.index;
    const end = start + rawPath.length;
    html += escapeHtml(source.slice(lastIndex, start));
    html += `<button class="path-link" type="button" data-path="${escapeHtml(rawPath)}">${escapeHtml(rawPath)}</button>`;
    html += escapeHtml(source.slice(end, start + match[0].length));
    lastIndex = start + match[0].length;
  }

  html += escapeHtml(source.slice(lastIndex));
  return html;
}

function cleanWindowsPath(path) {
  let value = String(path || "").trim();
  value = value.split(/\s+(?=(uses|would|keeping|delete|free|or|and)\b)/i)[0];
  return value.replace(/[.,;:]+$/, "");
}

function cleanKnownWindowsPath(path) {
  return String(path || "").trim().replace(/[.,;:]+$/, "");
}

function linkifyRecommendationDetail(item) {
  const source = String(item?.detail || "");
  const paths = [];
  if (item?.target && /^[A-Za-z]:\\/.test(item.target)) {
    paths.push(item.target);
  }
  if (item?.metadata?.matching_file && /^[A-Za-z]:\\/.test(item.metadata.matching_file)) {
    paths.push(item.metadata.matching_file);
  }

  const uniquePaths = [...new Set(paths)].sort((a, b) => b.length - a.length);
  if (uniquePaths.length === 0) {
    return linkifyWindowsPaths(source);
  }

  let html = "";
  let index = 0;
  while (index < source.length) {
    const match = uniquePaths.find((path) => source.startsWith(path, index));
    if (match) {
      const cleanPath = cleanKnownWindowsPath(match);
      html += `<button class="path-link" type="button" data-path="${escapeHtml(cleanPath)}">${escapeHtml(cleanPath)}</button>`;
      index += match.length;
    } else {
      html += escapeHtml(source[index]);
      index += 1;
    }
  }
  return html;
}

async function searchPortTask(port) {
  const results = document.getElementById("port-lookup-results");
  results.innerHTML = `<div class="empty-state compact">Searching port ${port}...</div>`;
  try {
    const res = await fetch(`/api/ports/${port}`);
    if (!res.ok) {
      throw new Error(`Server returned ${res.status}`);
    }
    renderPortLookupResults(await res.json());
  } catch (err) {
    results.innerHTML = `<div class="empty-state compact">Port lookup failed: ${escapeHtml(err.message)}</div>`;
  }
}

async function killPortTask(port, btnElement) {
  const ok = window.confirm(`Kill the task using port ${port}? Any unsaved work in that application may be lost.`);
  if (!ok) return;

  btnElement.disabled = true;
  btnElement.textContent = "Killing...";
  try {
    const res = await fetch("/api/ports/kill", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ port, confirm: true })
    });
    const result = await res.json();
    window.alert(result.detail);
    await searchPortTask(port);
    await refreshPerformance();
  } catch (err) {
    window.alert("Failed to kill port task: " + err.message);
  } finally {
    btnElement.disabled = false;
    btnElement.textContent = "Kill task";
  }
}

function renderPortLookupResults(data) {
  const results = document.getElementById("port-lookup-results");
  if (!data.found || data.processes.length === 0) {
    results.innerHTML = `<div class="empty-state compact">Port ${data.port} is not currently in use.</div>`;
    return;
  }

  results.innerHTML = "";
  data.processes.forEach((proc) => {
    const row = document.createElement("div");
    row.className = "port-task-row";
    const action = proc.can_terminate
      ? `<button class="action-btn secondary" data-port="${data.port}">Kill task</button>`
      : `<span class="badge" title="${escapeHtml(proc.protection_reason || "Protected")}">Protected</span>`;
    row.innerHTML = `
      <div class="port-task-meta">
        <strong>${escapeHtml(proc.name)}</strong>
        <p>Task No: ${proc.pid ?? "--"} | ${escapeHtml(proc.status)} | ${escapeHtml(proc.local_address)}</p>
      </div>
      <div>${action}</div>
    `;
    const btn = row.querySelector("button[data-port]");
    if (btn) {
      btn.addEventListener("click", () => killPortTask(data.port, btn));
    }
    results.appendChild(row);
  });
}

async function cleanTempFiles(btn) {
  const ok = window.confirm("Clean up Windows temporary files now?");
  if (!ok) return;
  btn.disabled = true;
  btn.textContent = "Cleaning...";
  try {
    // Generate a clean temp recommendation trigger dynamically
    const tempRec = latestHealthReport?.recommendations.find(r => r.action_type === "clean_temp");
    if (tempRec) {
      await triggerAction(tempRec.id, null);
    } else {
      // Direct request
      const res = await fetch("/api/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recommendation_id: "clean_temp", confirm: true })
      });
      const result = await res.json();
      window.alert(result.detail);
      await performScan();
    }
  } catch (err) {
    window.alert("Error: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Clean";
  }
}

async function emptyRecycleBin(btn) {
  const ok = window.confirm("Empty your Recycle Bin now?");
  if (!ok) return;
  btn.disabled = true;
  btn.textContent = "Emptying...";
  try {
    const rbRec = latestHealthReport?.recommendations.find(r => r.action_type === "empty_recycle_bin");
    if (rbRec) {
      await triggerAction(rbRec.id, null);
    } else {
      const res = await fetch("/api/actions/empty-recycle-bin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recommendation_id: "empty_recycle_bin", confirm: true })
      });
      const result = await res.json();
      window.alert(result.detail);
      await performScan();
    }
  } catch (err) {
    window.alert("Error: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Empty";
  }
}

async function flushDnsCache(btn) {
  btn.disabled = true;
  btn.textContent = "Flushing...";
  try {
    const res = await fetch("/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recommendation_id: "flush_dns", confirm: true })
    });
    const result = await res.json();
    window.alert(result.detail);
    await performScan();
  } catch (err) {
    window.alert("Error: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Flush";
  }
}

// Render UI Components

function renderOverviewTabs(report) {
  const diags = report.diagnostics;
  
  // Storage card
  const storageDesc = document.getElementById("overview-storage-desc");
  const firstDisk = report.disks[0];
  if (firstDisk && storageDesc) {
    storageDesc.textContent = `${bytesToHuman(firstDisk.free_bytes)} free of ${bytesToHuman(firstDisk.total_bytes)}`;
  }
  
  // Performance card
  const perfDesc = document.getElementById("overview-performance-desc");
  if (perfDesc) {
    perfDesc.textContent = `CPU: ${report.performance.cpu_percent.toFixed(1)}% | RAM: ${report.performance.memory_percent.toFixed(1)}%`;
  }
  
  // Battery card
  const batteryCard = document.querySelector('[data-ref="battery"]');
  if (batteryCard) {
    const batText = batteryCard.querySelector(".status-indicator");
    const batDesc = document.getElementById("overview-battery-desc");
    batText.textContent = report.performance.battery_percent !== null ? `${report.performance.battery_percent}%` : "--%";
    batDesc.textContent = `Health: ${diags.battery.health_percent}%`;
  }
  
  // Security card
  const securityCard = document.querySelector('[data-ref="security"]');
  if (securityCard) {
    const secIndicator = securityCard.querySelector(".status-indicator");
    const secDesc = document.getElementById("overview-security-desc");
    if (diags.security.defender_enabled && diags.security.firewall_enabled) {
      secIndicator.textContent = "Protected";
      secIndicator.className = "status-indicator success";
      secDesc.textContent = "Core security active";
    } else {
      secIndicator.textContent = "Vulnerable";
      secIndicator.className = "status-indicator danger";
      secDesc.textContent = "Defender or Firewall off!";
    }
  }
}

function renderStorageAnalyzer(report) {
  const diags = report.diagnostics;
  
  // Sizes text
  document.getElementById("temp-size-text").textContent = bytesToHuman(diags.temp_files_bytes);
  document.getElementById("recycle-size-text").textContent = bytesToHuman(diags.recycle_bin_bytes);
  document.getElementById("downloads-size-text").textContent = bytesToHuman(diags.downloads_bytes);
  
  // Render Disks
  const disksContainer = document.getElementById("disks-container");
  disksContainer.innerHTML = "";
  report.disks.forEach(disk => {
    const div = document.createElement("div");
    div.className = "disk-row";
    div.setAttribute("role", "button");
    div.setAttribute("tabindex", "0");
    div.dataset.path = disk.path;
    div.innerHTML = `
      <div class="disk-meta">
        <strong>${escapeHtml(disk.path)}</strong>
        <span>${disk.percent_used}% Used</span>
      </div>
      <div class="progress-bar-container">
        <span style="width: ${disk.percent_used}%; background: ${disk.percent_used > 85 ? 'var(--danger)' : 'var(--accent)'}"></span>
      </div>
      <div class="disk-details">
        <span>${bytesToHuman(disk.free_bytes)} Free</span>
        <span>Total: ${bytesToHuman(disk.total_bytes)}</span>
      </div>
    `;
    div.addEventListener("click", () => revealFilePath(disk.path));
    div.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        revealFilePath(disk.path);
      }
    });
    disksContainer.appendChild(div);
  });
  
  // Render Folders
  const foldersContainer = document.getElementById("folders-container");
  foldersContainer.innerHTML = "";
  report.folders.forEach(folder => {
    const div = document.createElement("div");
    div.className = "folder-info-card";
    div.setAttribute("role", "button");
    div.setAttribute("tabindex", "0");
    div.dataset.path = folder.path;
    div.innerHTML = `
      <div class="folder-meta">
        <h4>📁 ${escapeHtml(folder.path.split('\\').pop() || folder.path)}</h4>
        <p>${folder.exists ? `${folder.file_count} files` : 'Offline'}</p>
      </div>
      <div class="folder-size">${bytesToHuman(folder.total_bytes)}</div>
    `;
    div.addEventListener("click", () => revealFilePath(folder.path));
    div.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        revealFilePath(folder.path);
      }
    });
    foldersContainer.appendChild(div);
  });
}

function renderPerformanceOptimizer(report) {
  // Live stats
  document.getElementById("cpu-text").textContent = `${report.performance.cpu_percent.toFixed(1)}%`;
  document.getElementById("cpu-bar").style.width = `${report.performance.cpu_percent}%`;
  
  document.getElementById("ram-text").textContent = `${report.performance.memory_percent.toFixed(1)}%`;
  document.getElementById("ram-bar").style.width = `${report.performance.memory_percent}%`;
  
  document.getElementById("gpu-text").textContent = `${report.diagnostics.gpu_percent.toFixed(1)}%`;
  document.getElementById("gpu-bar").style.width = `${report.diagnostics.gpu_percent}%`;
  
  // Processes
  const tbody = document.getElementById("processes-table-body");
  tbody.innerHTML = "";
  report.performance.top_processes.forEach(proc => {
    const tr = document.createElement("tr");
    const killBtn = proc.can_terminate
      ? `<button class="action-btn secondary" data-pid="${proc.pid}" data-name="${escapeHtml(proc.name)}">Kill</button>`
      : `<span class="badge" title="${proc.protection_reason}">Protected</span>`;
      
    tr.innerHTML = `
      <td><strong>${escapeHtml(proc.name)}</strong></td>
      <td>${proc.pid}</td>
      <td>${proc.cpu_percent.toFixed(1)}%</td>
      <td>${bytesToHuman(proc.memory_bytes)}</td>
      <td><span class="status-indicator success">${escapeHtml(proc.status || 'running')}</span></td>
      <td>${killBtn}</td>
    `;
    
    const btn = tr.querySelector("button");
    if (btn) {
      btn.addEventListener("click", () => terminateProcess(proc.pid, proc.name, btn));
    }
    tbody.appendChild(tr);
  });
  
  // Startup programs
  const startupContainer = document.getElementById("startup-apps-container");
  startupContainer.innerHTML = "";
  if (report.diagnostics.startup_apps.length === 0) {
    startupContainer.innerHTML = `<div class="empty-state">No startup app records found.</div>`;
  } else {
    report.diagnostics.startup_apps.forEach(app => {
      const item = document.createElement("div");
      item.className = "startup-item";
      const isEnabled = app.enabled !== false;
      const actionLabel = isEnabled ? "Disable" : "Enable";
      const actionVerb = isEnabled ? "disable" : "enable";
      item.innerHTML = `
        <div class="startup-meta">
          <h4>⚙️ ${escapeHtml(app.name)}</h4>
          <p title="${escapeHtml(app.command)}">${escapeHtml(app.command)}</p>
        </div>
        <button class="action-btn secondary" data-name="${escapeHtml(app.name)}">${actionLabel}</button>
      `;
      const btn = item.querySelector("button");
      btn.addEventListener("click", async () => {
        const ok = window.confirm(`${actionLabel} startup program: '${app.name}'?`);
        if (!ok) return;
        btn.disabled = true;
        btn.textContent = `${actionLabel}...`;
        try {
          const res = await fetch(`/api/startup/${actionVerb}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: app.name, confirm: true })
          });
          const result = await res.json();
          window.alert(result.detail || "Action completed.");
          await performScan();
        } catch (e) {
          window.alert(`Error updating startup item: ${e.message}`);
        } finally {
          btn.disabled = false;
          btn.textContent = actionLabel;
        }
      });
      startupContainer.appendChild(item);
    });
  }
}

function renderDiagnosticsBattery(report) {
  const diags = report.diagnostics;
  
  // Battery gauges
  document.getElementById("bat-diag-pct").textContent = report.performance.battery_percent !== null ? `${report.performance.battery_percent}%` : "100%";
  document.getElementById("bat-health-text").textContent = `${diags.battery.health_percent}%`;
  document.getElementById("bat-design-cap").textContent = diags.battery.capacity_designed_mwh > 0 ? `${diags.battery.capacity_designed_mwh} mWh` : "Desktop (N/A)";
  document.getElementById("bat-full-cap").textContent = diags.battery.capacity_full_charge_mwh > 0 ? `${diags.battery.capacity_full_charge_mwh} mWh` : "Desktop (N/A)";
  
  const rem = diags.battery.remaining_life_minutes;
  document.getElementById("bat-remaining-time").textContent = rem ? `${Math.floor(rem / 60)} hrs ${rem % 60} mins` : "Calculating or Plugged In";
  
  // Hardware status
  const ssdIndicator = document.getElementById("hw-ssd-smart");
  if (diags.hardware.ssd_smart_ok) {
    ssdIndicator.textContent = "Healthy";
    ssdIndicator.className = "status-indicator success";
  } else {
    ssdIndicator.textContent = "Failing (SMART Alert)";
    ssdIndicator.className = "status-indicator danger";
  }
  
  document.getElementById("hw-boot-time").textContent = `${(diags.boot_time_seconds / 60).toFixed(1)} mins`;
  
  const failList = document.getElementById("hw-device-failures");
  if (diags.hardware.failed_devices.length === 0) {
    failList.textContent = "No component errors reported.";
    failList.className = "failed-list";
  } else {
    failList.textContent = diags.hardware.failed_devices.join(", ");
    failList.className = "failed-list status-indicator danger";
  }
}

const fixItem = (title, detail, badge = "") => `
  <div class="fix-item">
    <div>
      <strong>${escapeHtml(title)}</strong>
      <p>${escapeHtml(detail || "")}</p>
    </div>
    ${badge ? `<span class="badge info">${escapeHtml(badge)}</span>` : ""}
  </div>
`;

function setFixActions(containerId, html) {
  const container = document.getElementById(containerId);
  if (container) {
    container.innerHTML = html || "";
  }
}

async function renderFixCenter() {
  const alerts = document.getElementById("fix-alerts");
  const categories = document.getElementById("fix-categories");
  if (!alerts || !categories) return;

  alerts.innerHTML = `<div class="empty-state compact">Loading Fix Center...</div>`;
  try {
    const res = await fetch("/api/fix-center");
    if (!res.ok) throw new Error(`Server returned ${res.status}`);
    const data = await res.json();

    alerts.innerHTML = (data.alerts || []).length
      ? data.alerts.map(alert => `<div class="fix-alert">${escapeHtml(alert)}</div>`).join("")
      : `<div class="fix-alert success">No urgent Fix Center alerts.</div>`;

    categories.innerHTML = (data.categories || []).map(cat => `
      <div class="fix-category-card">
        <h3>${escapeHtml(cat.name)}</h3>
        <span class="badge info">${escapeHtml(cat.status)}</span>
        <p>${escapeHtml((cat.actions || []).join(" | "))}</p>
      </div>
    `).join("");

    // Admin Mode card
    const adminIsAdmin = data.admin?.is_admin ?? false;
    const adminBadge = document.getElementById("admin-badge");
    if (adminBadge) {
      adminBadge.textContent = adminIsAdmin ? "Active" : "Limited";
      adminBadge.className = adminIsAdmin
        ? "admin-badge admin-badge--active"
        : "admin-badge admin-badge--limited";
    }
    document.getElementById("fix-admin-status").innerHTML = fixItem(
      adminIsAdmin ? "Running as Administrator" : "Standard user mode",
      adminIsAdmin
        ? "All fixes including system-level operations are available."
        : "Some fixes (driver repair, SFC scan, system temp cleanup) require elevated privileges.",
      adminIsAdmin ? "OK" : "Limited"
    );
    const adminActionArea = document.getElementById("admin-action-area");
    if (adminActionArea) {
      if (!adminIsAdmin) {
        adminActionArea.innerHTML = `
          <button class="admin-relaunch-btn" id="relaunch-admin-btn">
            <span>🛡️</span> Relaunch as Administrator
          </button>
          <p class="admin-info-text">
            Clicking this will close the app and reopen it with Administrator privileges.<br>
            Windows will ask for your permission via the UAC prompt.
          </p>`;
        document.getElementById("relaunch-admin-btn")?.addEventListener("click", async () => {
          const btn = document.getElementById("relaunch-admin-btn");
          if (btn) { btn.disabled = true; btn.textContent = "Relaunching..."; }
          try {
            await fetch("/api/relaunch-as-admin", { method: "POST" });
          } catch (_) {}
          setTimeout(() => window.alert("App is restarting with admin rights. Please wait a moment."), 300);
        });
      } else {
        adminActionArea.innerHTML = "";
      }
    }

    const tempWarnings = data.temperature?.warnings || [];
    document.getElementById("fix-temperature-status").innerHTML = tempWarnings.length
      ? tempWarnings.map(t => fixItem(t.label || t.sensor, `${t.temperature_c} C`, t.level)).join("")
      : fixItem("No temperature warning", "No high temperature reading was reported by available sensors.", "OK");

    const events = data.event_viewer?.events || [];
    document.getElementById("fix-event-status").innerHTML = events.length
      ? events.slice(0, 5).map(e => fixItem(`${e.ProviderName || "System"} (${e.Id || "-"})`, e.Message || e.LevelDisplayName || "Event found", e.LevelDisplayName || "Event")).join("")
      : fixItem("No recent critical/error events", "No recent System log errors were returned.", "OK");
    setFixActions("fix-event-actions", `
      <button class="action-btn secondary" id="open-event-viewer-btn" type="button">Open Event Viewer</button>
    `);

    const dumps = data.bsod?.dumps || [];
    document.getElementById("fix-bsod-status").innerHTML = dumps.length
      ? dumps.map(d => fixItem(d.path, `${bytesToHuman(d.size_bytes)} | ${new Date(d.modified_at).toLocaleString()}`, "Dump")).join("")
      : fixItem("No BSOD minidumps found", "No dump files were found in Windows Minidump.", "OK");
    setFixActions("fix-bsod-actions", `
      <button class="action-btn secondary" id="open-minidump-btn" type="button">Open Minidump Folder</button>
    `);

    const drivers = data.drivers?.devices || [];
    document.getElementById("fix-driver-status").innerHTML = drivers.length
      ? drivers.map(d => fixItem(d.Name || "Device issue", `Error code ${d.ConfigManagerErrorCode ?? "-"}`, "Driver")).join("")
      : fixItem("No driver/device errors", "Windows did not report failed Plug and Play devices.", "OK");
    setFixActions("fix-driver-actions", `
      <button class="action-btn secondary" id="open-device-manager-btn" type="button">Open Device Manager</button>
    `);

    document.getElementById("fix-battery-report").innerHTML = fixItem(
      data.battery_report?.available ? "Battery report generated" : "Battery report unavailable",
      data.battery_report?.path || data.battery_report?.detail || "",
      data.battery_report?.available ? "Report" : "N/A"
    );
    setFixActions("fix-battery-actions", `
      <button class="action-btn secondary" id="open-battery-report-btn" type="button" ${data.battery_report?.available ? "" : "disabled"}>Open Battery Report</button>
    `);

    const apps = data.uninstall_recommendations || [];
    document.getElementById("fix-uninstall-list").innerHTML = apps.length
      ? apps.slice(0, 8).map(app => fixItem(app.name, `${app.publisher || "Unknown publisher"} | ${bytesToHuman(app.size_bytes || 0)}`, app.reason)).join("")
      : fixItem("No uninstall suggestions", "Installed software list did not return candidates.", "OK");
    setFixActions("fix-uninstall-actions", `
      <button class="action-btn secondary" id="open-installed-apps-btn" type="button">Open Installed Apps</button>
    `);

    const cacheEntries = data.browser_cache?.entries || [];
    document.getElementById("fix-browser-cache").innerHTML = fixItem(
      "Browser cache",
      `${data.browser_cache?.total || "0 B"} across ${cacheEntries.length} cache locations.`,
      "Cleanup"
    );
    setFixActions("fix-browser-cache-actions", `
      <button class="action-btn secondary" id="open-browser-cache-btn" type="button" ${cacheEntries.length ? "" : "disabled"}>Open Cache Folder</button>
    `);

    document.getElementById("open-event-viewer-btn")?.addEventListener("click", () => openSystemTool("event_viewer"));
    document.getElementById("open-minidump-btn")?.addEventListener("click", () => {
      const path = data.bsod?.folder;
      if (path) revealFilePath(path);
    });
    document.getElementById("open-device-manager-btn")?.addEventListener("click", () => openSystemTool("device_manager"));
    document.getElementById("open-battery-report-btn")?.addEventListener("click", () => {
      const path = data.battery_report?.path;
      if (path) revealFilePath(path);
    });
    document.getElementById("open-installed-apps-btn")?.addEventListener("click", () => openSystemTool("installed_apps"));
    document.getElementById("open-browser-cache-btn")?.addEventListener("click", () => {
      const firstCache = cacheEntries[0]?.path;
      if (firstCache) revealFilePath(firstCache);
    });
  } catch (err) {
    alerts.innerHTML = `<div class="fix-alert danger">Fix Center failed: ${escapeHtml(err.message)}</div>`;
  }
}

async function runFixAction(endpoint, message) {
  if (!window.confirm(message)) return;
  try {
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recommendation_id: endpoint, confirm: true })
    });
    const result = await res.json();
    window.alert(result.detail || "Action completed.");
    await renderFixCenter();
    await performScan();
  } catch (err) {
    window.alert("Fix action failed: " + err.message);
  }
}

function renderSecurityNetwork(report) {
  const diags = report.diagnostics;
  
  // Security
  const def = document.getElementById("sec-defender-badge");
  def.textContent = diags.security.defender_enabled ? "Active" : "Disabled";
  def.className = diags.security.defender_enabled ? "badge low" : "badge high";
  
  const fw = document.getElementById("sec-firewall-badge");
  fw.textContent = diags.security.firewall_enabled ? "Active" : "Disabled";
  fw.className = diags.security.firewall_enabled ? "badge low" : "badge high";
  
  const bl = document.getElementById("sec-bitlocker-badge");
  bl.textContent = diags.security.bitlocker_enabled ? "Encrypted" : "Off";
  bl.className = diags.security.bitlocker_enabled ? "badge low" : "badge medium";
  
  // Network
  document.getElementById("net-ping").textContent = diags.network.is_connected ? `${diags.network.ping_latency_ms} ms` : "Offline";
  document.getElementById("net-adapter").textContent = diags.network.adapter_status;
}

async function renderActivityLog() {
  try {
    const res = await fetch("/api/activity?limit=15");
    const logs = await res.json();
    const container = document.getElementById("log-entries");
    container.innerHTML = "";
    
    logs.reverse().forEach(log => {
      const entry = document.createElement("div");
      entry.className = "log-entry";
      
      const ts = new Date(log.timestamp).toLocaleTimeString();
      entry.innerHTML = `
        <span class="log-timestamp">[${ts}]</span>
        <span class="log-event">${escapeHtml(log.event)}</span>
        <div class="log-details">${escapeHtml(JSON.stringify(log.payload, null, 2))}</div>
      `;
      container.appendChild(entry);
    });
  } catch (err) {
    console.error("Log error: ", err);
  }
}

// Master Scan Routine
async function performScan() {
  if (state.scanning) return;
  state.scanning = true;

  const btn = document.getElementById("scan-btn");
  btn.disabled = true;
  btn.textContent = "Scanning...";

  const controller = new AbortController();
  const timeoutMs = 75000;
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch("/api/health", { signal: controller.signal });
    clearTimeout(timeoutId);
    if (!res.ok) {
      throw new Error(`Server returned ${res.status}`);
    }
    const report = normalizeHealthReport(await res.json());
    latestHealthReport = report;

    document.getElementById("last-scan").textContent = `Last diagnostics run: ${new Date(report.timestamp).toLocaleString()}`;

    // Update dashboard elements
    updateHealthRing(report.diagnostics.health_score);
    renderRecommendations(report.recommendations);
    renderOverviewTabs(report);
    renderStorageAnalyzer(report);
    renderPerformanceOptimizer(report);
    renderDiagnosticsBattery(report);
    renderSecurityNetwork(report);
    await renderActivityLog();
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === "AbortError") {
      window.alert(`Diagnostics scan timed out (${Math.round(timeoutMs / 1000)}s). The server may be busy - try again.`);
      return;
    } else {
      window.alert("Diagnostics scan failed: " + err.message);
    }
  } finally {
    state.scanning = false;
    btn.disabled = false;
    btn.textContent = "Scan Now";
  }
}

async function refreshPerformance() {
  try {
    const res = await fetch("/api/performance");
    const perf = await res.json();
    if (latestHealthReport) {
      latestHealthReport.performance = perf;
      renderPerformanceOptimizer(latestHealthReport);
      // Update Overview stats too
      const perfDesc = document.getElementById("overview-performance-desc");
      if (perfDesc) {
        perfDesc.textContent = `CPU: ${perf.cpu_percent.toFixed(1)}% | RAM: ${perf.memory_percent.toFixed(1)}%`;
      }
    }
  } catch (err) {
    console.error("Failed to poll performance metrics: ", err);
  }
}

// AI Chat Interaction
const chatForm = document.getElementById("chat-form");
const chatInput = document.getElementById("chat-input");
const chatMessages = document.getElementById("chat-messages");

async function handleSendMessage(queryText) {
  if (!queryText || state.sendingChat) return;
  state.sendingChat = true;
  
  // Append user message
  const userMsg = document.createElement("div");
  userMsg.className = "message user";
  userMsg.textContent = queryText;
  chatMessages.appendChild(userMsg);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  chatInput.value = "";
  
  // Append temporary loading bubble
  const loader = document.createElement("div");
  loader.className = "message assistant";
  loader.textContent = "Analyzing telemetry...";
  chatMessages.appendChild(loader);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  
  try {
    const res = await fetch("/api/ai/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: queryText,
        stats: latestHealthReport || {}
      })
    });
    const result = await res.json();
    loader.innerHTML = result.response.replace(/\n/g, "<br>");
  } catch (err) {
    loader.textContent = "Error: Failed to fetch reply from Laptop Health Assistant.";
  } finally {
    state.sendingChat = false;
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }
}

chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  handleSendMessage(chatInput.value.trim());
});

// Quick query chips
document.querySelectorAll(".query-chip").forEach(chip => {
  chip.addEventListener("click", () => {
    handleSendMessage(chip.textContent.trim());
  });
});

// History Trend Charting
async function renderHistoryCharts() {
  try {
    const res = await fetch("/api/history?limit=15");
    const historyData = await res.json();
    if (historyData.length === 0) return;
    
    const labels = historyData.map(h => new Date(h.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    const scores = historyData.map(h => h.health_score);
    const cpuLoads = historyData.map(h => h.cpu_percent);
    const ramLoads = historyData.map(h => h.memory_percent);
    
    // Draw Health Chart
    const ctx1 = document.getElementById("healthChart").getContext("2d");
    if (healthChartInstance) healthChartInstance.destroy();
    healthChartInstance = new Chart(ctx1, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'Laptop Health Score',
          data: scores,
          borderColor: '#198754',
          backgroundColor: 'rgba(25, 135, 84, 0.05)',
          fill: true,
          tension: 0.3,
          borderWidth: 3
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { min: 0, max: 100 }
        }
      }
    });
    
    // Draw Performance Chart
    const ctx2 = document.getElementById("perfChart").getContext("2d");
    if (perfChartInstance) perfChartInstance.destroy();
    perfChartInstance = new Chart(ctx2, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'CPU Load (%)',
            data: cpuLoads,
            borderColor: '#0f62fe',
            tension: 0.3,
            borderWidth: 2,
            fill: false
          },
          {
            label: 'RAM Load (%)',
            data: ramLoads,
            borderColor: '#d97706',
            tension: 0.3,
            borderWidth: 2,
            fill: false
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: { min: 0, max: 100 }
        }
      }
    });
  } catch (err) {
    console.error("Error drawing trend charts: ", err);
  }
}

renderRecommendations = function(recs) {
  const container = document.getElementById("recommendations-container");
  container.innerHTML = "";

  const executableRecs = recs.filter(r => r.action_type);
  if (executableRecs.length === 0) {
    container.innerHTML = `
      <div class="empty-state">
        <p>No actionable items at the moment. Your laptop health score is solid!</p>
      </div>
    `;
    return;
  }

  executableRecs.forEach((item) => {
    const row = document.createElement("div");
    row.className = "recommendation-item";
    const riskBadge = `<span class="badge ${item.risk}">${item.risk} risk</span>`;
    const categoryLabel = item.category === "storage" ? "Storage" : item.category === "performance" ? "Performance" : "System";
    const detailHtml = linkifyRecommendationDetail(item);

    row.innerHTML = `
      <div class="rec-content">
        <h3>${categoryLabel}: ${escapeHtml(item.title)}</h3>
        <p>${detailHtml}</p>
        <div>
          ${riskBadge}
          <span class="badge info" style="margin-left: 8px">${escapeHtml(item.estimated_benefit)}</span>
        </div>
      </div>
      <div>
        <button class="action-btn primary" data-id="${item.id}">Approve</button>
      </div>
    `;

    const approveBtn = row.querySelector("button[data-id]");
    if (approveBtn) {
      approveBtn.addEventListener("click", () => triggerAction(item.id, approveBtn));
    }
    row.querySelectorAll("button[data-path]").forEach((pathBtn) => {
      pathBtn.addEventListener("click", () => revealFilePath(pathBtn.dataset.path));
    });
    container.appendChild(row);
  });
};

// Connect custom buttons
document.getElementById("clean-temp-btn").addEventListener("click", (e) => cleanTempFiles(e.target));
document.getElementById("empty-recycle-btn").addEventListener("click", (e) => emptyRecycleBin(e.target));
document.getElementById("flush-dns-btn").addEventListener("click", (e) => flushDnsCache(e.target));
document.getElementById("scan-btn").addEventListener("click", performScan);
document.getElementById("fix-refresh-btn")?.addEventListener("click", renderFixCenter);
document.getElementById("fix-free-space-btn")?.addEventListener("click", () => {
  runFixAction("/api/fix-center/free-space-safely", "Run safe cleanup for temp files, browser cache, and Recycle Bin?");
});
document.getElementById("fix-browser-cache-btn")?.addEventListener("click", () => {
  runFixAction("/api/fix-center/clean-browser-cache", "Clean browser cache now? Close browsers first for best results.");
});
document.getElementById("fix-archive-btn")?.addEventListener("click", () => {
  runFixAction("/api/fix-center/archive-old-files", "Archive old files in Downloads into a zip file?");
});
document.getElementById("port-lookup-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const value = Number(document.getElementById("port-lookup-input").value);
  if (!Number.isInteger(value) || value < 1 || value > 65535) {
    document.getElementById("port-lookup-results").innerHTML = `<div class="empty-state compact">Enter a valid port from 1 to 65535.</div>`;
    return;
  }
  searchPortTask(value);
});

// Initialize App
ensureAdminMode();
performScan();
// Poll cpu/ram metrics every 4 seconds
setInterval(refreshPerformance, 4000);
