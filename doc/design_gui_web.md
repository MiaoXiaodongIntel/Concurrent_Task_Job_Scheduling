# Web GUI Design and Frontend API Contract (Frozen v1)

Back to architecture: [design_arch.md](design_arch.md)

Related backend contracts:
1. [design_monitor_api.md](design_monitor_api.md)
2. [design_control_plane.md](design_control_plane.md)
3. [design_task_manager.md](design_task_manager.md)
4. [../monitor_api.py](../monitor_api.py)
5. [../task_manager.py](../task_manager.py)

## 1. Purpose

This document freezes a first frontend API contract for Web GUI integration and defines page-level wireframes for implementation.

Scope:
1. Frontend-visible API schema (request/response fields)
2. Error and reason code contract
3. Command state preconditions
4. GUI information architecture and wireframes

## 2. Frozen Contract Metadata

1. Contract version: `frontend-api-v1`
2. Status: `frozen`
3. Frozen date: `2026-06-13`
4. Compatibility rule:
   - Additive fields are allowed.
   - Renaming or removing existing fields requires a new contract version.

## 3. Global API Conventions

1. Transport: HTTP JSON
2. Default host/port: `127.0.0.1:8765`
3. Content type:
   - Request: `application/json`
   - Response: `application/json; charset=utf-8`
4. Timestamp format: local ISO-8601 string with seconds precision (for example `2026-06-13T11:20:15+08:00`)
5. Error body:
   - Generic endpoint errors: `{ "error": "..." }`
   - Control/submit business rejection: HTTP `400` with command response body including `accepted=false` and `reason_code`

## 4. Data Models Exposed to Frontend

### 4.1 Host Health Model (`GET /health`)

1. `host_state`: `NOT_RUN|RUNNING|DRAINING|STOPPING_FORCE|IDLE|SHUTTING_DOWN`
2. `queued_count`: integer
3. `starting_count`: integer
4. `running_count`: integer
5. `completed_count`: integer
6. `total_count`: integer
7. `last_status_ts`: timestamp string

### 4.2 Task Model (`GET /tasks`, `GET /tasks/{id}`)

1. `task_id`: string
2. `commands`: list of strings
3. `status`: `queued|starting|running|succeeded|failed|aborted`
4. `created_at`: timestamp string
5. `started_at`: timestamp string or null
6. `ended_at`: timestamp string or null
7. `pid`: integer or null
8. `exit_code`: integer or null
9. `abort_reason`: string or null
10. `last_output_ts`: timestamp string or null
11. `log_path`: string or null

### 4.3 Log Cursor Model (`GET /tasks/{id}/logs`)

1. `task_id`: string
2. `cursor`: integer (requested start line)
3. `next_cursor`: integer (next line index to request)
4. `eof`: boolean
5. `lines`: list of strings

### 4.4 Control Response Model (`POST /control/*`, `POST /tasks/submit`)

Common fields:
1. `accepted`: boolean
2. `command`: `start|graceful_stop|force_stop|rerun|shutdown|submit_tasks`
3. `requested_at`: timestamp string
4. `host_state_before`: host state string
5. `host_state_after_expected`: host state string
6. `message`: string
7. `reason_code`: string
8. `affected_task_ids`: list of strings

Optional fields:
1. `rejected_task_ids`: list of strings (rerun)
2. `submit_mode`: `append|replace` (submit_tasks)

## 5. Endpoint Contract Table (Frozen v1)

| Endpoint | Method | Request | Success (HTTP 200) | Rejection/Error |
|---|---|---|---|---|
| `/health` | GET | none | host health model | `404 {"error": ...}` for unknown route |
| `/tasks` | GET | none | `{ "tasks": Task[] }` | `404 {"error": ...}` for unknown route |
| `/tasks/{id}` | GET | path `id` | `Task` | `404 {"error": "task not found: <id>"}` |
| `/tasks/{id}/logs` | GET | query `cursor` (int, default `0`), `limit` (int, default `200`) | log cursor model | `400 {"error": "cursor and limit must be integers"}`; `404 task not found` |
| `/control/start` | POST | empty object or no body | control response | `400` + `accepted=false` and `reason_code` |
| `/control/graceful-stop` | POST | empty object or no body | control response | `400` + `accepted=false` and `reason_code` |
| `/control/force-stop` | POST | empty object or no body | control response | `400` + `accepted=false` and `reason_code` |
| `/control/rerun` | POST | `{ "task_ids": string[] }` | control response (may include partial success) | `400` + `accepted=false` and `reason_code` |
| `/control/shutdown` | POST | `{ "mode": "drain|force", "timeout_sec": number? }` | control response | `400` + `accepted=false` and `reason_code` |
| `/tasks/submit` | POST | `{ "submit_mode": "append|replace", "tasks": TaskPayload[] }` | submit response | `400` + `accepted=false` and `reason_code` |

`TaskPayload`:
1. `task_id`: optional string (auto-generated if missing/empty)
2. `commands`: required non-empty list of non-empty strings

## 6. State Preconditions for Control Commands

### 6.1 Host-level command preconditions

| Command | Accepted when host_state is | Rejected when host_state is | Rejection reason_code |
|---|---|---|---|
| `start` | `NOT_RUN`, `IDLE` | `RUNNING`, `DRAINING`, `STOPPING_FORCE`, `SHUTTING_DOWN` | `invalid_state` |
| `graceful_stop` | `RUNNING` | all others | `invalid_state` |
| `force_stop` | `RUNNING`, `DRAINING` | all others | `invalid_state` |
| `shutdown` | first valid call from any non-shutdown state | if shutdown already requested | `shutdown_already_requested` |

Shutdown-specific parameter validation:
1. `mode` must be `drain` or `force`, otherwise `invalid_shutdown_mode`.
2. `timeout_sec` must be numeric when provided, otherwise `invalid_timeout`.

### 6.2 Task-level command preconditions

| Command | Task eligibility | Rejection reason_code |
|---|---|---|
| `rerun` | task must exist and status is `succeeded` or `failed` | `no_eligible_task` when none accepted |
| `submit_tasks` (`append`) | all incoming `task_id` must be non-duplicate against existing tasks | `duplicate_task_id` |
| `submit_tasks` (`replace`) | no in-flight tasks (`starting` or `running`) | `inflight_exists` |
| `submit_tasks` (all modes) | host must not be shutting down | `host_shutting_down` |
| `submit_tasks` (all modes) | payload shape must be valid | `invalid_task_payload`, `invalid_submit_mode` |

## 7. Reason Code Dictionary (Frozen v1)

### 7.1 Control command reason_code

1. `accepted`
2. `invalid_state`
3. `no_eligible_task`
4. `invalid_shutdown_mode`
5. `shutdown_already_requested`
6. `invalid_timeout`
7. `unknown_command`

### 7.2 Task submission reason_code

1. `accepted`
2. `invalid_submit_mode`
3. `invalid_task_payload`
4. `host_shutting_down`
5. `inflight_exists`
6. `duplicate_task_id`

## 8. Frontend Handling Rules

1. Treat `accepted=true` as command accepted, not command completed.
2. For lifecycle completion, poll `GET /health` and `GET /tasks`.
3. If command returns `accepted=false`, surface `message` and `reason_code` to user.
4. For `rerun`, show partial result if `affected_task_ids` is non-empty and `rejected_task_ids` exists.
5. Logs page keeps `cursor` per task and requests from `next_cursor`.
6. When `host_state=SHUTTING_DOWN`, disable mutable actions except passive refresh.

## 9. GUI Information Architecture

Primary pages:
1. Dashboard — unified observation + control (summary cards, host commands, resources, recent tasks, command history)
2. Tasks
3. Task Detail (with logs)
4. Submit Tasks

Navigation style:
1. Top bar: host status label, "Updated Xs ago" refresh indicator, manual Refresh Now button
2. Left nav: 4 items — Dashboard / Tasks / Task Detail / Submit Tasks
3. Main panel: page content

UX principles applied:
1. Dashboard is the single control hub; all host commands are co-located with the status they act on
2. Empty-state guidance appears in Dashboard when no tasks are loaded
3. Task Detail provides breadcrumb navigation back to Tasks
4. Host command buttons use disabled + tooltip to communicate state preconditions
5. Dashboard Host Commands panel shows a live host-state badge
6. Command History (last 20 entries) is visible directly on Dashboard, eliminating the need for a separate Control Panel page

## 10. Page Wireframes (Low Fidelity)

### 10.1 Dashboard

```text
+--------------------------------------------------------------------------------+
| WEB TASK HOST                  host_state: RUNNING   Updated 1s ago [Refresh] |
+----------------------+---------------------------------------------------------+
| Left Nav             | Summary Cards                                           |
| - Dashboard (active) | [host_state] [queued] [starting] [running] [completed]  |
| - Tasks              |                                                         |
| - Task Detail        | Host Commands  [● RUNNING]                               |
| - Submit Tasks       | [Start↓] [Graceful Stop] [Force Stop↓] [drain▾] [Shutdown↓] |
|                      | (disabled+tooltip when precondition not met)            |
|                      |                                                         |
|                      | System Resources                                        |
|                      | CPU / Memory / Disk bars                                |
|                      |                                                         |
|                      | Recent Task Changes                                     |
|                      | (empty state when no tasks: guidance to Submit Tasks)   |
|                      | task_id | status | started_at | ended_at | exit_code     |
|                      |                                                         |
|                      | Command History (last 20)                               |
|                      | time | command | accepted | reason_code | message        |
+----------------------+---------------------------------------------------------+
```

### 10.2 Tasks List

```text
+--------------------------------------------------------------------------------+
| Filters: [status dropdown] [task_id search] [only failed]                      |
| Actions: [Rerun Selected]                                                      |
+--------------------------------------------------------------------------------+
| Select | task_id | status | pid | started_at | ended_at | exit_code | detail  |
| [ ]    | demo-1  | failed | 123 | ...        | ...      | 1         | [open]  |
| [ ]    | demo-2  | running| 345 | ...        | -        | -         | [open]  |
+--------------------------------------------------------------------------------+
```

### 10.3 Task Detail + Logs

```text
+--------------------------------------------------------------------------------+
| Tasks  ›  demo-1                   (breadcrumb — click "Tasks" to go back)    |
+--------------------------------------------------------------------------------+
| Task: demo-1         status: failed      pid: 12345      exit_code: 1          |
| created_at: ...  started_at: ...  ended_at: ...  log_path: logs/demo-1.log     |
+--------------------------------------------------------------------------------+
| Log Controls: [auto refresh on/off] [limit=200] [clear view]                   |
+--------------------------------------------------------------------------------+
| 2026-06-13T10:00:00+08:00 [STDOUT] task start                                  |
| 2026-06-13T10:00:02+08:00 [STDERR] command failed                              |
| ...                                                                              |
+--------------------------------------------------------------------------------+
```

### 10.4 Submit Tasks

```text
+--------------------------------------------------------------------------------+
| submit_mode: (o) append   ( ) replace                                          |
| [Validate Payload] [Submit]                                                     |
+--------------------------------------------------------------------------------+
| JSON Editor                                                                      |
| {                                                                                |
|   "tasks": [                                                                    |
|     {"task_id":"demo-1", "commands":["Write-Host 'hello'"]}                |
|   ]                                                                              |
| }                                                                                |
+--------------------------------------------------------------------------------+
| Validation Result / Submit Response                                              |
+--------------------------------------------------------------------------------+
```

### 10.5 Submit Tasks

```text
+--------------------------------------------------------------------------------+
| submit_mode: (o) append   ( ) replace                                          |
| [Load File] [Validate Payload] [Submit]                                        |
+--------------------------------------------------------------------------------+
| JSON Editor                                                                      |
| {                                                                                |
|   "tasks": [                                                                    |
|     {"task_id":"demo-1", "commands":["Write-Host 'hello'"]}                |
|   ]                                                                              |
| }                                                                                |
+--------------------------------------------------------------------------------+
| Validation Result / Submit Response                                              |
+--------------------------------------------------------------------------------+
```

_(Control Panel page removed; all host commands consolidated into Dashboard.)_

## 11. Polling and Refresh Plan

1. `GET /health`: every 1 second
2. `GET /tasks`: every 1 to 2 seconds
3. `GET /tasks/{id}/logs`: every 0.5 to 1 second only when detail page is active
4. After any successful command submit, trigger immediate `health` and `tasks` refresh

## 12. Open Gaps and Follow-up

1. CORS policy is not specified for browser-hosted frontend.
2. Authentication and authorization are not yet defined.
3. No API pagination for `/tasks` in large task sets.
4. No server push channel (SSE/WebSocket); polling only.
5. Reason code list is frozen for current implementation and must be revised if backend adds new business branches.

---

## 13. UX Conventions

1. **Unified Dashboard**: Dashboard is the single control hub. Host commands and command history are co-located with status cards and resource meters. The Control Panel page has been eliminated.
2. **Separation of data entry**: Submit Tasks is the only page with mutable data input (task JSON). All host lifecycle commands remain on Dashboard.
3. **Empty-state guidance**: When `tasks` list is empty, Dashboard shows an inline prompt directing the user to Submit Tasks.
4. **Breadcrumb navigation**: Task Detail view shows `Tasks › <task_id>` breadcrumb. Clicking "Tasks" returns to the Tasks list.
5. **Button disabled state**: All host command buttons use `disabled=true` when the current `host_state` does not satisfy the precondition. CSS renders disabled buttons at 35% opacity with `cursor: not-allowed`. The `title` attribute carries a human-readable explanation of the required state.
6. **Host state badge**: The Host Commands panel heading includes a live-updating colored badge showing the current `host_state`.
7. **Refresh indicator**: The Refresh Now button is accompanied by an "Updated Xs ago" label that updates every second. Clicking the button disables it and changes its label to "Refreshing…" during the request.
8. **Shutdown mode**: The drain/force mode `<select>` is inline with the Shutdown button in the Host Commands action row.

---

Last updated: 2026-06-13
