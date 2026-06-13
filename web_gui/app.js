const state = {
  health: null,
  tasks: [],
  activeView: "dashboardView",
  selectedTaskIds: new Set(),
  detailTaskId: "",
  logCursor: 0,
  logLines: [],
  commandHistory: [],
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

function renderSummaryCards() {
  const cards = byId("summaryCards");
  const h = state.health || {};
  const model = [
    ["host_state", h.host_state || "-"],
    ["queued", h.queued_count ?? "-"],
    ["starting", h.starting_count ?? "-"],
    ["running", h.running_count ?? "-"],
    ["completed", h.completed_count ?? "-"],
    ["total", h.total_count ?? "-"],
  ];

  cards.innerHTML = model
    .map(([label, value]) => `<div class="card"><div class="label">${label}</div><div class="value">${value}</div></div>`)
    .join("");

  byId("topHostState").textContent = `host_state: ${h.host_state || "-"}`;
}

function renderRecentTasks() {
  const tbody = byId("recentTaskTbody");
  const recent = [...state.tasks]
    .sort((a, b) => (b.last_output_ts || "").localeCompare(a.last_output_ts || ""))
    .slice(0, 8);

  tbody.innerHTML = recent
    .map(
      (task) => `
      <tr>
        <td>${task.task_id}</td>
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
      return `
        <tr>
          <td><input type="checkbox" data-select-task="${task.task_id}" ${checked} /></td>
          <td>${task.task_id}</td>
          <td><span class="badge ${task.status}">${task.status}</span></td>
          <td>${formatValue(task.pid)}</td>
          <td>${formatValue(task.started_at)}</td>
          <td>${formatValue(task.ended_at)}</td>
          <td>${formatValue(task.exit_code)}</td>
          <td><button class="btn btn-secondary" data-open-task="${task.task_id}">Open</button></td>
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
  if (!state.detailTaskId) {
    panel.innerHTML = "<p>Select one task and click Open.</p>";
    return;
  }

  const task = state.tasks.find((item) => item.task_id === state.detailTaskId);
  if (!task) {
    panel.innerHTML = `<p>Task not found: ${state.detailTaskId}</p>`;
    return;
  }

  panel.innerHTML = `
    <h3>${task.task_id}</h3>
    <p><strong>status:</strong> ${task.status} <strong>pid:</strong> ${formatValue(task.pid)} <strong>exit_code:</strong> ${formatValue(task.exit_code)}</p>
    <p><strong>created_at:</strong> ${formatValue(task.created_at)}</p>
    <p><strong>started_at:</strong> ${formatValue(task.started_at)} <strong>ended_at:</strong> ${formatValue(task.ended_at)}</p>
    <p><strong>abort_reason:</strong> ${formatValue(task.abort_reason)}</p>
    <p><strong>log_path:</strong> ${formatValue(task.log_path)}</p>
  `;
}

function renderLogs() {
  byId("logViewer").textContent = state.logLines.join("\n");
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
  state.activeView = viewId;
  document.querySelectorAll(".view").forEach((section) => {
    section.classList.toggle("hidden", section.id !== viewId);
  });
  document.querySelectorAll(".nav-link").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewId);
  });
}

async function refreshHealth() {
  const health = await requestJson("/health");
  state.health = health;
  renderSummaryCards();
}

async function refreshTasks() {
  const data = await requestJson("/tasks");
  state.tasks = data.tasks || [];
  renderRecentTasks();
  renderTaskTable();
  renderTaskDetail();
}

async function refreshTaskLogs() {
  if (state.activeView !== "taskDetailView") {
    return;
  }
  if (!state.detailTaskId) {
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
    await Promise.all([refreshHealth(), refreshTasks()]);
    showAlert("Refreshed");
  });

  document.querySelectorAll(".nav-link").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });

  document.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("click", async () => {
      const command = button.dataset.command;
      if (command === "shutdown") {
        await sendCommand("shutdown", { mode: "drain" });
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

  byId("taskTableTbody").addEventListener("click", (event) => {
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
      state.logCursor = 0;
      state.logLines = [];
      renderTaskDetail();
      renderLogs();
      switchView("taskDetailView");
    }
  });

  byId("rerunSelectedBtn").addEventListener("click", async () => {
    const taskIds = [...state.selectedTaskIds];
    if (taskIds.length === 0) {
      showAlert("No task selected", "error");
      return;
    }
    await sendCommand("rerun", { task_ids: taskIds });
  });

  byId("openTaskDetailBtn").addEventListener("click", () => {
    const selected = byId("detailTaskSelect").value;
    if (!selected) {
      showAlert("No task selected", "error");
      return;
    }
    state.detailTaskId = selected;
    state.logCursor = 0;
    state.logLines = [];
    renderTaskDetail();
    renderLogs();
  });

  byId("clearLogBtn").addEventListener("click", () => {
    state.logCursor = 0;
    state.logLines = [];
    renderLogs();
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

  byId("shutdownBtn").addEventListener("click", async () => {
    const mode = byId("shutdownModeSelect").value;
    const timeoutRaw = byId("shutdownTimeoutInput").value.trim();
    const payload = { mode };
    if (timeoutRaw) {
      const timeout = Number(timeoutRaw);
      if (!Number.isFinite(timeout)) {
        showAlert("timeout_sec must be numeric", "error");
        return;
      }
      payload.timeout_sec = timeout;
    }
    await sendCommand("shutdown", payload);
  });
}

function preloadSubmitTemplate() {
  const sample = {
    tasks: [
      {
        task_id: "demo-ui-1",
        commands: [
          "Write-Host 'demo-ui-1 start'; Start-Sleep -Seconds 1",
          "Write-Host 'demo-ui-1 done'",
        ],
      },
      {
        task_id: "demo-ui-2",
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
  renderRecentTasks();
  renderTaskTable();
  renderTaskDetail();
  renderCommandHistory();

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
}

bootstrap();
