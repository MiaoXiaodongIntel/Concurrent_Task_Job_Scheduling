# MonitorAPI and Observability Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

Implementation file: [../core/monitor_api.py](../core/monitor_api.py)

Composition wiring: [../core/task_host.py](../core/task_host.py)

## 1. Responsibility

MonitorAPI provides read and control interfaces for monitoring and integration.

It does not own lifecycle transition rules. It exposes lifecycle state from TaskManager and forwards manual control intents to ControlPlane.

MonitorAPI is the primary control surface for GUI integration; stdin command control in TaskHost is optional and CLI-focused.

## 2. Read Interfaces

1. `GET /health`
2. `GET /tasks`
3. `GET /tasks/{id}` (includes `run_history`)
4. `GET /tasks/{id}/logs?cursor=...&limit=...&run=...`
5. `GET /resources`

Transport contract:

1. HTTP JSON API served by local host process.
2. `/tasks/{id}/logs` uses line-based `cursor`, optional `limit`, and optional `run` (run_index) query parameters. When `run` is omitted, the current run's log is returned.

## 3. Control Interfaces

1. `POST /control/start`
2. `POST /control/graceful-stop`
3. `POST /control/force-stop`
4. `POST /control/rerun`
5. `POST /control/shutdown`
6. `POST /tasks/submit`
7. `POST /tasks/{id}/abort`
8. `POST /resources`

Control endpoints are intent APIs, not direct state mutation APIs. Final task status changes are committed by TaskManager.

## 4. Data Exposure Contract

1. Host-level state and counters must reflect TaskManager source of truth.
2. Task list/details must include status, pid, timestamps, exit metadata, and log location.
3. Log streaming must support incremental cursor-based consumption.

### 4.1 Lifecycle-Observable Fields

Host-level fields:

1. `host_state` (`NOT_RUN|RUNNING|DRAINING|STOPPING_FORCE|SHUTTING_DOWN`)
2. `queued_count`
3. `pending_count`
4. `starting_count`
5. `running_count`
6. `completed_count`
7. `total_count`
8. `last_status_ts`

Task-level fields:

1. `task_id`
2. `resource`
3. `priority`
4. `status` (`queued|pending|starting|running|succeeded|failed|aborted`)
5. `blocked_by` (task_id of the task holding the resource when `status=pending`, otherwise null)
6. `pid`
7. `created_at`
8. `started_at`
9. `ended_at`
10. `exit_code`
11. `abort_reason`
12. `last_output_ts`
13. `log_path` (current run's system log path)
14. `artifact_dir` (current run's tool artifact directory, or null)
15. `run_index` (0-based run counter; incremented each rerun)
16. `run_history` (list of `RunRecord` — **only present in `GET /tasks/{id}` response**, omitted from `GET /tasks` list for response size)

RunRecord fields (per entry in `run_history`):

1. `run_index`: integer
2. `started_at`: timestamp or null
3. `ended_at`: timestamp or null
4. `exit_code`: integer or null
5. `status`: terminal status string (`succeeded`/`failed`/`aborted`)
6. `log_path`: string or null
7. `artifact_dir`: string or null

Resource-level fields (`GET /resources`):

1. `loaded` (boolean — whether resources have been registered)
2. `resources` (list of resource objects):
   - `resource`: string identifier
   - `status`: `"occupied"` or `"free"`
   - `held_by`: task_id of the holding task, or null
   - `pending_tasks`: list of task_ids waiting for this resource (sorted by priority ascending, stable by created_at)

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
4. `POST /control/rerun` submits rerun intent for `succeeded|failed|aborted -> queued`.
5. `POST /tasks/submit` submits runtime task-list append/replace intent.
6. `POST /control/shutdown` submits host process shutdown intent (`drain` default).
7. `GET /health` and `GET /tasks` expose intervention effects (for example reduced admissions, `aborted` tasks, and `abort_reason`).
8. `POST /tasks/{id}/abort` submits per-task abort intent for a single `running` or `pending` task.
9. `POST /resources` submits the resource registry (accepted only when host is `NOT_RUN` and resources not yet loaded).
10. `GET /resources` exposes current resource occupancy and pending-task queues per resource.

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
