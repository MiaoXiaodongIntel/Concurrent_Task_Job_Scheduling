# TaskManager and State Machine Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

Implementation file: [../task_manager.py](../task_manager.py)

## 1. Responsibility

TaskManager is the source of truth for task lifecycle and host runtime counters.

## 2. Task Lifecycle Model

Task states:

1. `queued`
2. `starting`
3. `running`
4. `succeeded`
5. `failed`
6. `aborted`

State semantics:

1. `queued`: task is eligible for future admission and has not been bound to a live process in the current attempt.
2. `starting`: task has been admitted and startup has begun (script/materialization/spawn phase), but stable running execution has not been confirmed yet.
3. `running`: task process is alive and task output can be observed.
4. `succeeded`: task finished with success exit code in the latest attempt.
5. `failed`: task finished with non-zero exit code or spawn failure in the latest attempt.
6. `aborted`: task was interrupted by manual force-stop policy or by a per-task user abort.

Terminal states:

1. `succeeded`
2. `failed`

Note: `aborted` is not a terminal state; aborted tasks may be rerun.

### 2.1 Transition Sources and Ownership

TaskManager is the only state-commit authority. It applies transitions from two sources under one validation rule set:

1. Automatic events:
	- scheduler admission and runner completion
	- admission condition: host must be in `RUNNING` state for `queued -> starting` to trigger
	- example: `running -> succeeded|failed` based on process exit code
2. Manual events:
	- stop/abort commands coordinated by ControlPlane
	- example: `running -> aborted` when forced stop policy applies or when per-task user abort is requested
3. Rerun events:
	- user rerun command coordinated by ControlPlane
	- example: `succeeded|failed|aborted -> queued`

Both sources must go through the same transition validator to keep lifecycle consistency.

### 2.2 Task Transition Policy

Baseline transitions:

1. `queued -> starting` (condition: host in `RUNNING` state)
2. `starting -> running|failed`
3. `running -> succeeded|failed`
4. `running -> aborted` (force-stop path)
5. `succeeded|failed|aborted -> queued` (rerun path)
6. `starting -> aborted` (force-stop path)
7. `running -> aborted` (per-task user abort path)

## 3. Host Lifecycle Model

Host states:

1. `NOT_RUN`
2. `RUNNING`
3. `DRAINING`
4. `STOPPING_FORCE`
5. `SHUTTING_DOWN`

Host state semantics:

1. `NOT_RUN`: host is initialized but not executing; waiting for start or shutdown command.
2. `RUNNING`: host is actively scheduling tasks; stays in this state even after all current tasks complete, allowing further rerun requests.
3. `DRAINING`: graceful-stop issued; no new tasks are admitted; waiting for in-flight tasks to finish.
4. `STOPPING_FORCE`: force-stop issued; in-flight tasks are being aborted.
5. `SHUTTING_DOWN`: process is terminating; entered only from `NOT_RUN`.

Host transition policy:

1. `NOT_RUN -> RUNNING` by explicit start command.
2. `RUNNING -> DRAINING` by graceful-stop command.
3. `RUNNING -> STOPPING_FORCE` by force-stop command.
4. `DRAINING -> STOPPING_FORCE` by force-stop command.
5. `DRAINING -> NOT_RUN` when no in-flight task remains.
6. `STOPPING_FORCE -> NOT_RUN` after force-stop handling is completed.
7. `NOT_RUN -> SHUTTING_DOWN` by shutdown command.
8. `SHUTTING_DOWN` ends host loop immediately (no in-flight tasks exist at this point).

## 4. Data Ownership

TaskManager owns:

1. full `TaskJob` snapshots
2. queue/running/completed counters
3. task-to-process mapping for active jobs
4. runtime timestamps for status and output activity
5. per-task log file path
6. runtime task submission acceptance (`append|replace`) with validation constraints
7. shutdown progression metadata (mode/timeout escalation state)

## 5. Interface to Other Modules

Inbound events:

1. task list provided at construction time
2. scheduler admission decisions (`Scheduler.pick_next_tasks`)
3. runner process lifecycle (`TaskRunner.start_task`, process wait, cleanup)
4. stdout/stderr stream lines from runner subprocess pipes

Outbound views:

1. host status line output (`[HOST] ...`)
2. per-task stream output (`[task_id][STDOUT|STDERR] ...`)
3. task snapshots through `TaskJob.to_dict()` and host summary builder

## 6. Consistency Rules

1. All status changes must pass transition validation.
2. Counters must be derivable from task snapshots.
3. Task snapshot updates must be atomic at module boundary.

## 7. Runtime Loop Mapping

1. `run()` drives the tick loop.
2. `_emit_status_if_due()` emits periodic host health snapshot.
3. `_try_schedule()` queries scheduler and starts selected tasks.
4. `_watch_task()` finalizes each task and applies terminal status.
5. `_advance_host_state()` moves host among `RUNNING|DRAINING|STOPPING_FORCE|IDLE|SHUTTING_DOWN`.
6. Host loop exits only when shutdown is requested and in-flight work reaches zero.

## 8. Lifecycle Requirement Mapping

1. Requirement 2.4 (lifecycle governance): owned here as state model + transition invariants.
2. Requirement 2.5 (automatic progression): consumed from Scheduler/Runner events and committed here.
3. Requirement 2.6 (manual intervention): executed via ControlPlane commands and committed here.
4. Requirement extension (rerun): `succeeded|failed` tasks can re-enter queue by command.
5. Requirement extension (resident execution): execution round completion transitions host to `IDLE` without process exit.
6. Requirement extension (runtime task-list submission): new task payloads can be accepted through control surface while host stays alive.

Related docs:

1. [design_scheduler.md](design_scheduler.md)
2. [design_control_plane.md](design_control_plane.md)
3. [design_monitor_api.md](design_monitor_api.md)
