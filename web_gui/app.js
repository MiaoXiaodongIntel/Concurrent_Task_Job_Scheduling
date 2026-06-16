const state = {
  health: null,
  tasks: [],
  resources: null,
  activeView: "dashboardView",
  selectedTaskIds: new Set(),
  detailTaskId: "",
  detailTask: null,       // full task detail fetched from GET /tasks/<id> (includes run_history)
  detailRunIndex: null,   // null = current run, number = specific historical run index
  logCursor: 0,
  logLines: [],
  commandHistory: [],
  lastRefreshAt: null,
  resourcesPollInterval: null,
};

const POLL_HEALTH_MS = 1000;
const POLL_TASKS_MS = 1500;
const POLL_LOGS_MS = 800;

function byId(id) {
  return document.getElementById(id);
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") {
    return "-";
  }
  return String(value);
}

function showAlert(message, kind = "success") {
  const box = byId("alertBox");
  box.className = `alert ${kind}`;
  box.textContent = message;
  setTimeout(() => {
    if (box.textContent === message) {
      box.className = "alert hidden";
      box.textContent = "";
    }
  }, 4200);
}

async function requestJson(path, options = {}) {
  const response = await fetch(path, options);
  let payload = {};
  try {
    payload = await response.json();
  } catch (_err) {
    payload = {};
  }

  if (!response.ok) {
    const reason = payload.error || payload.message || `HTTP ${response.status}`;
    throw new Error(reason);
  }
  return payload;
}

async function postJson(path, body) {
  return requestJson(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

function pushHistory(entry) {
  state.commandHistory.unshift(entry);
  if (state.commandHistory.length > 20) {
    state.commandHistory.pop();
  }
  renderCommandHistory();
}

const HOST_STATE_TRANSITIONS = {
  start:         ["NOT_RUN"],
  graceful_stop: ["RUNNING"],
  force_stop:    ["RUNNING", "DRAINING"],
  shutdown:      ["NOT_RUN"],
};

function updateButtonStates() {
  const hostState = state.health?.host_state || "";
  document.querySelectorAll("[data-command]").forEach((button) => {
    const cmd = button.dataset.command;
    const allowed = HOST_STATE_TRANSITIONS[cmd];
    if (allowed) {
      const isAllowed = allowed.includes(hostState);
      button.disabled = !isAllowed;
      button.title = isAllowed
        ? ""
        : `“${cmd}” requires host state: ${allowed.join(" or ")} (current: ${hostState || "unknown"})`;
    }
  });
  const shutdownBtn = byId("shutdownBtn");
  if (shutdownBtn) {
    const shutdownAllowed = hostState === "NOT_RUN";
    shutdownBtn.disabled = !shutdownAllowed;
    shutdownBtn.title = shutdownAllowed
      ? ""
      : `Shutdown requires host_state: NOT_RUN (current: ${hostState || "unknown"})`;
  }

  // Update host state badge in Control Panel
  const badge = byId("controlHostStateBadge");
  if (badge) {
    badge.textContent = hostState || "-";
    badge.className = `host-badge ${hostState.toLowerCase()}`;
  }
}

function renderSummaryCards() {
  const cards = byId("summaryCards");
  const h = state.health || {};
  const hostState = h.host_state || "-";
  const model = [
    ["host_state", hostState],
    ["queued", h.queued_count ?? "-"],
    ["pending", h.pending_count ?? "-"],
    ["starting", h.starting_count ?? "-"],
    ["running", h.running_count ?? "-"],
    ["completed", h.completed_count ?? "-"],
  ];

  cards.innerHTML = model
    .map(([label, value]) => `<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`)
    .join("");

  byId("topHostState").textContent = `host_state: ${hostState}`;
  updateButtonStates();
}

function renderResourceMeters() {
  const container = byId("resourceMeters");
  if (!container) return;
  const h = state.health || {};

  const meters = [
    { label: "CPU", key: "cpu_percent" },
    { label: "Memory", key: "memory_percent" },
    { label: "Disk Active", key: "disk_active_percent" },
  ];

  container.innerHTML = meters
    .map(({ label, key }) => {
      const val = h[key] ?? null;
      const pct = val !== null ? val : 0;
      const level = pct >= 90 ? "danger" : pct >= 70 ? "warning" : "normal";
      const display = val !== null ? `${pct}%` : "-";
      return `
        <div class="resource-meter">
          <div class="resource-meter-header">
            <span class="resource-label">${label}</span>
            <span class="resource-value ${level}">${display}</span>
          </div>
          <div class="resource-bar-track">
            <div class="resource-bar-fill ${level}" style="width:${pct}%"></div>
          </div>
        </div>`;
    })
    .join("");
}

function renderRecentTasks() {
  const tbody = byId("recentTaskTbody");
  const emptyState = byId("dashboardEmptyState");
  const recent = [...state.tasks]
    .sort((a, b) => (b.last_output_ts || "").localeCompare(a.last_output_ts || ""))
    .slice(0, 8);

  if (emptyState) {
    emptyState.classList.toggle("hidden", state.tasks.length > 0);
  }

  tbody.innerHTML = recent
    .map(
      (task) => `
      <tr>
        <td>${task.task_id}</td>
        <td>${formatValue(task.resource)}</td>
        <td><span class="badge ${task.status}">${task.status}</span></td>
        <td>${formatValue(task.started_at)}</td>
        <td>${formatValue(task.ended_at)}</td>
        <td>${formatValue(task.exit_code)}</td>
      </tr>`
    )
    .join("");
}

function currentTaskFilters() {
  return {
    search: byId("taskSearchInput").value.trim().toLowerCase(),
    status: byId("statusFilterSelect").value,
  };
}

function filteredTasks() {
  const filters = currentTaskFilters();
  return state.tasks.filter((task) => {
    if (filters.status !== "all" && task.status !== filters.status) {
      return false;
    }
    if (filters.search && !task.task_id.toLowerCase().includes(filters.search)) {
      return false;
    }
    return true;
  });
}

function renderTaskTable() {
  const tbody = byId("taskTableTbody");
  const list = filteredTasks();

  tbody.innerHTML = list
    .map((task) => {
      const checked = state.selectedTaskIds.has(task.task_id) ? "checked" : "";
      const canAbort = task.status === 'running' || task.status === 'pending';
      const blockedTip = task.blocked_by ? ` title="blocked by: ${task.blocked_by}"` : '';
      return `
        <tr>
          <td><input type="checkbox" data-select-task="${task.task_id}" ${checked} /></td>
          <td>${task.task_id}</td>
          <td>${formatValue(task.resource)}</td>
          <td>${formatValue(task.priority)}</td>
          <td><span class="badge ${task.status}"${blockedTip}>${task.status}${task.blocked_by ? ' ⏳' : ''}</span></td>
          <td>${formatValue(task.pid)}</td>
          <td>${formatValue(task.started_at)}</td>
          <td>${formatValue(task.ended_at)}</td>
          <td>${formatValue(task.exit_code)}</td>
          <td>
            <button class="btn btn-secondary" data-open-task="${task.task_id}">Open</button>
            ${canAbort ? `<button class="btn danger" data-abort-task="${task.task_id}" style="margin-left:4px">Abort</button>` : ''}
          </td>
        </tr>`;
    })
    .join("");

  byId("checkAllTasks").checked = list.length > 0 && list.every((t) => state.selectedTaskIds.has(t.task_id));

  const detailSelect = byId("detailTaskSelect");
  detailSelect.innerHTML = state.tasks
    .map((task) => `<option value="${task.task_id}">${task.task_id} (${task.status})</option>`)
    .join("");

  if (state.detailTaskId) {
    detailSelect.value = state.detailTaskId;
  }
}

function renderTaskDetail() {
  const panel = byId("taskInfoPanel");
  const breadcrumb = byId("breadcrumbTaskId");

  if (breadcrumb) {
    breadcrumb.textContent = state.detailTaskId || "Task Detail";
  }

  if (!state.detailTaskId) {
    panel.innerHTML = "<p>Select one task and click Open.</p>";
    _updateLogRunLabel();
    return;
  }

  // Use full detail (with run_history) if available; fall back to list snapshot.
  const task = state.detailTask || state.tasks.find((item) => item.task_id === state.detailTaskId);
  if (!task) {
    panel.innerHTML = `<p>Task not found: ${state.detailTaskId}</p>`;
    _updateLogRunLabel();
    return;
  }

  const canAbort = task.status === 'running' || task.status === 'pending';
  const abortTitle = canAbort ? 'Abort this task' : 'Task is not running or pending';
  const runHistory = task.run_history || [];

  // Build run history table rows (sorted newest first for display).
  const historyRows = [...runHistory]
    .sort((a, b) => b.run_index - a.run_index)
    .map((r) => {
      const statusClass = r.status || "unknown";
      const isViewing = state.detailRunIndex === r.run_index;
      const artifactCell = r.artifact_dir
        ? `<span title="${r.artifact_dir}" style="font-size:0.8em;word-break:break-all">${r.artifact_dir}</span>`
        : "-";
      return `
        <tr${isViewing ? ' class="viewing-run"' : ''}>
          <td>${r.run_index}</td>
          <td><span class="badge ${statusClass}">${r.status}</span></td>
          <td>${formatValue(r.exit_code)}</td>
          <td>${formatValue(r.started_at)}</td>
          <td>${formatValue(r.ended_at)}</td>
          <td>${artifactCell}</td>
          <td>
            <button class="btn btn-secondary" style="font-size:0.8em;padding:2px 8px"
              data-view-run="${r.run_index}">View Logs</button>
          </td>
        </tr>`;
    }).join("");

  const currentRunViewing = state.detailRunIndex === null || state.detailRunIndex === task.run_index;
  const currentArtifactCell = task.artifact_dir
    ? `<span title="${task.artifact_dir}" style="font-size:0.85em;word-break:break-all">${task.artifact_dir}</span>`
    : "-";

  const historySection = runHistory.length > 0 ? `
    <details style="margin-top:12px" open>
      <summary style="cursor:pointer;font-weight:600;margin-bottom:6px">
        Run History (${runHistory.length} past run${runHistory.length !== 1 ? "s" : ""})
      </summary>
      <div style="overflow-x:auto">
        <table style="width:100%;font-size:0.85em;border-collapse:collapse">
          <thead>
            <tr style="text-align:left;border-bottom:1px solid #ddd">
              <th style="padding:4px 8px">#</th>
              <th style="padding:4px 8px">Status</th>
              <th style="padding:4px 8px">Exit</th>
              <th style="padding:4px 8px">Started</th>
              <th style="padding:4px 8px">Ended</th>
              <th style="padding:4px 8px">Artifact Dir</th>
              <th style="padding:4px 8px">Logs</th>
            </tr>
          </thead>
          <tbody>${historyRows}</tbody>
        </table>
      </div>
    </details>` : "";

  panel.innerHTML = `
    <h3>${task.task_id}</h3>
    <p><strong>status:</strong> ${task.status}
       <strong>run #:</strong> ${formatValue(task.run_index)}
       <strong>pid:</strong> ${formatValue(task.pid)}
       <strong>exit_code:</strong> ${formatValue(task.exit_code)}</p>
    <p><strong>resource:</strong> ${formatValue(task.resource)} &nbsp; <strong>priority:</strong> ${formatValue(task.priority)}</p>
    <p><strong>blocked_by:</strong> ${formatValue(task.blocked_by)}</p>
    <p><strong>created_at:</strong> ${formatValue(task.created_at)}</p>
    <p><strong>started_at:</strong> ${formatValue(task.started_at)} <strong>ended_at:</strong> ${formatValue(task.ended_at)}</p>
    <p><strong>abort_reason:</strong> ${formatValue(task.abort_reason)}</p>
    <p><strong>log_path:</strong> ${formatValue(task.log_path)}</p>
    <p><strong>artifact_dir:</strong> ${currentArtifactCell}</p>
    <div class="actions" style="margin-top:8px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <button class="btn danger" data-abort-task="${task.task_id}"
        ${canAbort ? '' : 'disabled'}
        title="${abortTitle}">Abort</button>
      <button class="btn btn-secondary" id="viewCurrentRunLogsBtn"
        ${currentRunViewing ? 'disabled' : ''}
        title="Switch log viewer to current run">Current Run Logs</button>
    </div>
    ${historySection}
  `;

  _updateLogRunLabel();
}

function renderLogs() {
  byId("logViewer").textContent = state.logLines.join("\n");
}

function _updateLogRunLabel() {
  const label = byId("logRunLabel");
  if (!label) return;
  if (!state.detailTaskId) {
    label.textContent = "";
    return;
  }
  if (state.detailRunIndex === null) {
    label.textContent = "Viewing: current run";
    label.style.color = "#2a7";
  } else {
    label.textContent = `Viewing: run #${state.detailRunIndex}`;
    label.style.color = "#888";
  }
}

function renderCommandHistory() {
  const tbody = byId("commandHistoryTbody");
  tbody.innerHTML = state.commandHistory
    .map((row) => `
      <tr>
        <td>${formatValue(row.requested_at)}</td>
        <td>${formatValue(row.command)}</td>
        <td>${row.accepted ? "true" : "false"}</td>
        <td>${formatValue(row.reason_code)}</td>
        <td>${formatValue(row.message)}</td>
        <td>${(row.affected_task_ids || []).join(", ")}</td>
      </tr>`)
    .join("");
}

function switchView(viewId) {
  const enteringResources = viewId === "resourcesView" && state.activeView !== "resourcesView";
  const leavingResources = state.activeView === "resourcesView" && viewId !== "resourcesView";
  state.activeView = viewId;
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("hidden", section.id !== viewId);
  });
  document.querySelectorAll(".nav-link").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewId);
  });
  if (enteringResources) {
    refreshResources().catch(() => {});
    state.resourcesPollInterval = setInterval(() => {
      refreshResources().catch(() => {});
    }, 1000);
  }
  if (leavingResources && state.resourcesPollInterval) {
    clearInterval(state.resourcesPollInterval);
    state.resourcesPollInterval = null;
  }
}

async function refreshHealth() {
  const health = await requestJson("/health");
  state.health = health;
  state.lastRefreshAt = Date.now();
  renderSummaryCards();
  renderResourceMeters();
}

async function refreshTasks() {
  const data = await requestJson("/tasks");
  state.tasks = data.tasks || [];
  renderRecentTasks();
  renderTaskTable();
  if (state.activeView === "taskDetailView" && state.detailTaskId) {
    await refreshDetailTask();
  } else {
    renderTaskDetail();
  }
}

async function refreshDetailTask() {
  if (!state.detailTaskId) return;
  try {
    const task = await requestJson(`/tasks/${encodeURIComponent(state.detailTaskId)}`);
    state.detailTask = task;
  } catch (_err) {
    state.detailTask = null;
  }
  renderTaskDetail();
}

async function refreshResources() {
  const data = await requestJson("/resources");
  state.resources = data;
  renderResourcesPage();
}

function renderResourcesPage() {
  const data = state.resources;
  const hostState = state.health?.host_state || "";
  const loadPanel = byId("resourcesLoadPanel");
  const statusPanel = byId("resourcesStatusPanel");
  const canLoad = hostState === "NOT_RUN" && !(data && data.loaded);
  if (loadPanel) loadPanel.classList.toggle("hidden", !canLoad);
  if (!data || !data.loaded) {
    if (statusPanel) statusPanel.classList.toggle("hidden", true);
    return;
  }
  if (statusPanel) statusPanel.classList.toggle("hidden", false);
  const tbody = byId("resourcesTableTbody");
  if (!tbody) return;
  const resources = data.resources || [];
  tbody.innerHTML = resources.map((r) => {
    const badgeClass = r.status === "occupied" ? "running" : "queued";
    const pendingList = (r.pending_tasks || []).join(", ") || "-";
    return `<tr>
      <td>${r.resource}</td>
      <td><span class="badge ${badgeClass}">${r.status}</span></td>
      <td>${r.held_by || "-"}</td>
      <td>${pendingList}</td>
    </tr>`;
  }).join("");
}

async function refreshTaskLogs() {
  if (state.activeView !== "taskDetailView") {
    return;
  }
  if (!state.detailTaskId) {
    return;
  }
  // For historical runs auto-refresh makes no sense (run is already complete).
  if (state.detailRunIndex !== null) {
    return;
  }
  if (!byId("autoLogRefresh").checked) {
    return;
  }

  const limitRaw = Number(byId("logLimitInput").value || "200");
  const limit = Number.isFinite(limitRaw) && limitRaw > 0 ? Math.floor(limitRaw) : 200;

  const payload = await requestJson(`/tasks/${encodeURIComponent(state.detailTaskId)}/logs?cursor=${state.logCursor}&limit=${limit}`);
  const lines = payload.lines || [];
  if (lines.length > 0) {
    state.logLines.push(...lines);
    if (state.logLines.length > 8000) {
      state.logLines = state.logLines.slice(-8000);
    }
    renderLogs();
  }
  state.logCursor = payload.next_cursor || state.logCursor;
}

async function loadRunLogs(runIndex) {
  state.detailRunIndex = runIndex;
  state.logCursor = 0;
  state.logLines = [];
  renderLogs();
  _updateLogRunLabel();
  if (!state.detailTaskId) return;
  const limitRaw = Number(byId("logLimitInput").value || "200");
  const limit = Number.isFinite(limitRaw) && limitRaw > 0 ? Math.floor(limitRaw) : 200;
  const runParam = runIndex !== null ? `&run=${runIndex}` : "";
  try {
    const payload = await requestJson(`/tasks/${encodeURIComponent(state.detailTaskId)}/logs?cursor=0&limit=${limit}${runParam}`);
    state.logLines = payload.lines || [];
    state.logCursor = payload.next_cursor || 0;
    renderLogs();
  } catch (err) {
    showAlert(`Failed to load logs: ${err.message}`, "error");
  }
  renderTaskDetail();
}

async function sendCommand(command, options = {}) {
  try {
    let payload = {};
    let path = "";

    if (command === "start") {
      path = "/control/start";
    } else if (command === "graceful_stop") {
      path = "/control/graceful-stop";
    } else if (command === "force_stop") {
      path = "/control/force-stop";
    } else if (command === "rerun") {
      path = "/control/rerun";
      payload.task_ids = options.task_ids || [];
    } else if (command === "shutdown") {
      path = "/control/shutdown";
      payload = options;
    } else {
      throw new Error(`unknown command: ${command}`);
    }

    const result = await postJson(path, payload);
    pushHistory(result);
    await Promise.all([refreshHealth(), refreshTasks()]);
    showAlert(`${result.command}: ${result.message} (${result.reason_code})`, result.accepted ? "success" : "error");
    return result;
  } catch (err) {
    showAlert(`Command failed: ${err.message}`, "error");
    throw err;
  }
}

async function sendAbortTask(taskId) {
  if (!window.confirm(`Abort task "${taskId}"?\nThe subprocess will be terminated immediately.`)) {
    return;
  }
  try {
    const result = await postJson(`/tasks/${encodeURIComponent(taskId)}/abort`, {});
    pushHistory(result);
    await Promise.all([refreshHealth(), refreshTasks()]);
    showAlert(`abort_task: ${result.message} (${result.reason_code})`, result.accepted ? "success" : "error");
  } catch (err) {
    showAlert(`Abort failed: ${err.message}`, "error");
  }
}

function collectSubmitMode() {
  const checked = document.querySelector("input[name='submitMode']:checked");
  return checked ? checked.value : "append";
}

function parseSubmitJson() {
  const editor = byId("submitJsonEditor");
  const parsed = JSON.parse(editor.value);
  if (!parsed || typeof parsed !== "object" || !Array.isArray(parsed.tasks)) {
    throw new Error("payload must be an object with tasks array");
  }
  return parsed.tasks;
}

function bindEvents() {
  byId("quickRefresh").addEventListener("click", async () => {
    const btn = byId("quickRefresh");
    const originalText = btn.textContent;
    btn.textContent = "Refreshing…";
    btn.disabled = true;
    try {
      await Promise.all([refreshHealth(), refreshTasks()]);
      showAlert("Refreshed");
    } finally {
      btn.textContent = originalText;
      btn.disabled = false;
    }
  });

  document.querySelectorAll(".nav-link").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });

  // Delegated handler for data-view elements outside the sidebar (breadcrumb, empty-state, CTA)
  document.querySelector(".content").addEventListener("click", (event) => {
    const el = event.target.closest("[data-view]:not(.nav-link)");
    if (el) {
      event.preventDefault();
      switchView(el.dataset.view);
    }
  });

  document.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", async () => {
      const command = button.dataset.command;
      if (command === "shutdown") {
        const mode = byId("shutdownModeSelect")?.value || "drain";
        await sendCommand("shutdown", { mode });
        return;
      }
      if (command === "force_stop") {
        if (!window.confirm("Force stop will abort in-flight tasks. Continue?")) {
          return;
        }
      }
      await sendCommand(command);
    });
  });

  byId("taskSearchInput").addEventListener("input", renderTaskTable);
  byId("statusFilterSelect").addEventListener("change", renderTaskTable);

  byId("checkAllTasks").addEventListener("change", (event) => {
    const checked = event.target.checked;
    filteredTasks().forEach((task) => {
      if (checked) {
        state.selectedTaskIds.add(task.task_id);
      } else {
        state.selectedTaskIds.delete(task.task_id);
      }
    });
    renderTaskTable();
  });

  byId("taskTableTbody").addEventListener("click", async (event) => {
    const selectTaskId = event.target?.dataset?.selectTask;
    if (selectTaskId) {
      if (event.target.checked) {
        state.selectedTaskIds.add(selectTaskId);
      } else {
        state.selectedTaskIds.delete(selectTaskId);
      }
      renderTaskTable();
      return;
    }

    const openTaskId = event.target?.dataset?.openTask;
    if (openTaskId) {
      state.detailTaskId = openTaskId;
      state.detailTask = null;
      state.detailRunIndex = null;
      state.logCursor = 0;
      state.logLines = [];
      renderTaskDetail();
      renderLogs();
      switchView("taskDetailView");
      refreshDetailTask().catch(() => {});
      return;
    }

    const abortTaskId = event.target?.dataset?.abortTask;
    if (abortTaskId) {
      await sendAbortTask(abortTaskId);
    }
  });

  byId("taskInfoPanel").addEventListener("click", async (event) => {
    const abortTaskId = event.target?.dataset?.abortTask;
    if (abortTaskId) {
      await sendAbortTask(abortTaskId);
      return;
    }

    const viewRunStr = event.target?.dataset?.viewRun;
    if (viewRunStr !== undefined) {
      await loadRunLogs(parseInt(viewRunStr, 10));
      return;
    }

    if (event.target?.id === "viewCurrentRunLogsBtn") {
      await loadRunLogs(null);
      return;
    }
  });

  byId("rerunSelectedBtn").addEventListener("click", async () => {
    const taskIds = [...state.selectedTaskIds];
    if (taskIds.length === 0) {
      showAlert("No task selected", "error");
      return;
    }
    // Eligible: succeeded, failed, or aborted tasks
    await sendCommand("rerun", { task_ids: taskIds });
  });

  byId("openTaskDetailBtn").addEventListener("click", () => {
    const selected = byId("detailTaskSelect").value;
    if (!selected) {
      showAlert("No task selected", "error");
      return;
    }
    state.detailTaskId = selected;
    state.detailTask = null;
    state.detailRunIndex = null;
    state.logCursor = 0;
    state.logLines = [];
    renderTaskDetail();
    renderLogs();
    refreshDetailTask().catch(() => {});
  });

  byId("clearLogBtn").addEventListener("click", () => {
    state.logCursor = 0;
    state.logLines = [];
    renderLogs();
  });

  byId("loadTaskFileBtn").addEventListener("click", () => {
    byId("taskFileInput").click();
  });

  byId("taskFileInput").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const parsed = JSON.parse(e.target.result);
        byId("submitJsonEditor").value = JSON.stringify(parsed, null, 2);
        byId("submitResult").textContent = `Loaded: ${file.name} (${file.size} bytes)`;
        showAlert(`Loaded ${file.name}`);
      } catch (err) {
        byId("submitResult").textContent = `Failed to parse file: ${err.message}`;
        showAlert(`Failed to parse file: ${err.message}`, "error");
      }
      event.target.value = "";
    };
    reader.readAsText(file);
  });

  byId("validateSubmitBtn").addEventListener("click", () => {
    try {
      const tasks = parseSubmitJson();
      byId("submitResult").textContent = `Validation passed. tasks=${tasks.length}`;
      showAlert("Submit payload valid");
    } catch (err) {
      byId("submitResult").textContent = `Validation failed: ${err.message}`;
      showAlert(`Validation failed: ${err.message}`, "error");
    }
  });

  byId("submitTasksBtn").addEventListener("click", async () => {
    try {
      const tasks = parseSubmitJson();
      const submit_mode = collectSubmitMode();
      const result = await postJson("/tasks/submit", { submit_mode, tasks });
      byId("submitResult").textContent = JSON.stringify(result, null, 2);
      pushHistory(result);
      await Promise.all([refreshHealth(), refreshTasks()]);
      showAlert(`submit_tasks: ${result.message} (${result.reason_code})`, result.accepted ? "success" : "error");
    } catch (err) {
      byId("submitResult").textContent = `Submit failed: ${err.message}`;
      showAlert(`Submit failed: ${err.message}`, "error");
    }
  });

  byId("loadResourceFileBtn").addEventListener("click", () => {
    byId("resourceFileInput").click();
  });

  byId("resourceFileInput").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const parsed = JSON.parse(e.target.result);
        byId("resourcesJsonEditor").value = JSON.stringify(parsed, null, 2);
        byId("resourcesResult").textContent = `Loaded: ${file.name} (${file.size} bytes)`;
        showAlert(`Loaded ${file.name}`);
      } catch (err) {
        byId("resourcesResult").textContent = `Failed to parse file: ${err.message}`;
        showAlert(`Failed to parse file: ${err.message}`, "error");
      }
      event.target.value = "";
    };
    reader.readAsText(file);
  });

  byId("submitResourceListBtn").addEventListener("click", async () => {
    try {
      const raw = byId("resourcesJsonEditor").value;
      const parsed = JSON.parse(raw);
      if (!parsed || !Array.isArray(parsed.resources)) {
        throw new Error("payload must be an object with a resources array");
      }
      const result = await postJson("/resources", { resources: parsed.resources });
      byId("resourcesResult").textContent = JSON.stringify(result, null, 2);
      pushHistory(result);
      await refreshResources();
      showAlert(`load_resources: ${result.message} (${result.reason_code})`, result.accepted ? "success" : "error");
    } catch (err) {
      byId("resourcesResult").textContent = `Submit failed: ${err.message}`;
      showAlert(`Submit failed: ${err.message}`, "error");
    }
  });
}

function preloadSubmitTemplate() {
  const sample = {
    tasks: [
      {
        task_id: "demo-ui-1",
        resource: "machine-A",
        priority: 1,
        commands: [
          "Write-Host 'demo-ui-1 start'; Start-Sleep -Seconds 1",
          "Write-Host 'demo-ui-1 done'",
        ],
      },
      {
        task_id: "demo-ui-2",
        resource: "machine-B",
        priority: 2,
        commands: [
          "Write-Host 'demo-ui-2 start'; Start-Sleep -Seconds 2",
          "Write-Host 'demo-ui-2 done'",
        ],
      },
    ],
  };
  byId("submitJsonEditor").value = JSON.stringify(sample, null, 2);
}

async function bootstrap() {
  bindEvents();
  preloadSubmitTemplate();
  renderSummaryCards();
  renderResourceMeters();
  renderRecentTasks();
  renderTaskTable();
  renderTaskDetail();
  renderCommandHistory();
  renderResourcesPage();

  try {
    await Promise.all([refreshHealth(), refreshTasks()]);
  } catch (err) {
    showAlert(`Initial load failed: ${err.message}`, "error");
  }

  setInterval(() => {
    refreshHealth().catch((err) => showAlert(`Health poll failed: ${err.message}`, "error"));
  }, POLL_HEALTH_MS);

  setInterval(() => {
    refreshTasks().catch((err) => showAlert(`Tasks poll failed: ${err.message}`, "error"));
  }, POLL_TASKS_MS);

  setInterval(() => {
    refreshTaskLogs().catch((err) => showAlert(`Log poll failed: ${err.message}`, "error"));
  }, POLL_LOGS_MS);

  setInterval(() => {
    const label = byId("lastRefreshLabel");
    if (label && state.lastRefreshAt) {
      const secs = Math.round((Date.now() - state.lastRefreshAt) / 1000);
      label.textContent = `Updated ${secs}s ago`;
    }
  }, 1000);
}

bootstrap();
