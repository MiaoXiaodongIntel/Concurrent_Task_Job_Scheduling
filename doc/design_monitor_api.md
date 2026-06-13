# MonitorAPI and Observability Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

Implementation file: [../monitor_api.py](../monitor_api.py)

Composition wiring: [../task_host.py](../task_host.py)

## 1. Responsibility

MonitorAPI provides read and control interfaces for monitoring and integration.

It does not own lifecycle transition rules. It exposes lifecycle state from TaskManager and forwards manual control intents to ControlPlane.

MonitorAPI is the primary control surface for GUI integration; stdin command control in TaskHost is optional and CLI-focused.

## 2. Read Interfaces

1. `GET /health`
2. `GET /tasks`
3. `GET /tasks/{id}`
4. `GET /tasks/{id}/logs?cursor=...`

Transport contract:

1. HTTP JSON API served by local host process.
2. `/tasks/{id}/logs` uses line-based `cursor` and optional `limit` query parameters.

## 3. Control Interfaces

1. `POST /control/start`
2. `POST /control/graceful-stop`
3. `POST /control/force-stop`
4. `POST /control/rerun`
5. `POST /control/shutdown`
6. `POST /tasks/submit`
7. `POST /tasks/{id}/abort`

Control endpoints are intent APIs, not direct state mutation APIs. Final task status changes are committed by TaskManager.

## 4. Data Exposure Contract

1. Host-level state and counters must reflect TaskManager source of truth.
2. Task list/details must include status, pid, timestamps, exit metadata, and log location.
3. Log streaming must support incremental cursor-based consumption.

### 4.1 Lifecycle-Observable Fields

Host-level fields:

1. `host_state` (`NOT_RUN|RUNNING|DRAINING|STOPPING_FORCE|IDLE|SHUTTING_DOWN`)
2. `queued_count`
3. `starting_count`
4. `running_count`
5. `completed_count`
6. `total_count`
7. `last_status_ts`

Task-level fields:

1. `task_id`
2. `status` (`queued|starting|running|succeeded|failed|aborted`)
3. `pid`
4. `created_at`
5. `started_at`
6. `ended_at`
7. `exit_code`
8. `abort_reason`
9. `last_output_ts`
10. `log_path`

### 4.2 Endpoint-to-Requirement Mapping

Requirement 2.4 (lifecycle governance):

1. `GET /health` exposes host lifecycle state and aggregate counters.
2. `GET /tasks` and `GET /tasks/{id}` expose canonical task lifecycle states.
3. `GET /tasks/{id}/logs?cursor=...` supports timeline correlation with lifecycle timestamps.

Requirement 2.5 (automatic lifecycle progression):

1. `GET /tasks` and `GET /tasks/{id}` expose automatic terminal outcomes (`succeeded|failed`) and `exit_code`.
2. `GET /health` shows admission/execution pressure via queue and running counters.

Requirement 2.6 (manual lifecycle intervention):

1. `POST /control/start` submits start/resume intent.
2. `POST /control/graceful-stop` submits drain intent.
3. `POST /control/force-stop` submits forced termination intent.
4. `POST /control/rerun` submits rerun intent for `succeeded|failed -> queued`.
5. `POST /tasks/submit` submits runtime task-list append/replace intent.
6. `POST /control/shutdown` submits host process shutdown intent (`drain` default).
7. `GET /health` and `GET /tasks` expose intervention effects (for example reduced admissions, `aborted` tasks, and `abort_reason`).
8. `POST /tasks/{id}/abort` submits per-task abort intent for a single `running` task.

### 4.3 Control Command Response Contract

For control endpoints, responses should include:

1. `accepted` (whether command was accepted)
2. `command` (`start|graceful_stop|force_stop|rerun|shutdown|submit_tasks|abort_task`)
3. `requested_at`
4. `host_state_before`
5. `host_state_after_expected`
6. `message` (human-readable command handling summary)
7. `reason_code` (when command is rejected or requires machine parsing)
8. `affected_task_ids` (required for `rerun` and `tasks/submit`, optional for others)

Lifecycle completion is observed through read APIs, not guaranteed by control endpoint immediate response.

## 5. Integration Expectations

1. Supports CLI polling and future GUI pages.
2. Must provide stable response schema for automation.
3. Access should be restricted to local or authenticated clients.
	- Note: this is a low-priority requirement for the current phase and should not be treated as a review blocker for now.

## 6. Requirement Boundary

1. Requirement 2.4 (lifecycle governance): MonitorAPI exposes canonical states but does not define transition invariants.
2. Requirement 2.5 (automatic progression): MonitorAPI exposes scheduler/runner-driven outcomes through read endpoints.
3. Requirement 2.6 (manual intervention): MonitorAPI accepts control intents and surfaces resulting status changes.

Related docs:

1. [design_arch.md](design_arch.md)
2. [design_task_manager.md](design_task_manager.md)
3. [design_control_plane.md](design_control_plane.md)
