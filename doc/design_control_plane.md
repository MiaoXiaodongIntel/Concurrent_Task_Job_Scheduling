# ControlPlane Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

## 1. Responsibility

ControlPlane handles host-level control requests and coordinates stop behavior.

It is the owner of manual lifecycle intervention semantics (requirement 2.6), while TaskManager remains the final state-commit authority.

## 2. Control Commands

1. `start`
2. `graceful_stop`
3. `force_stop`
4. `rerun`
5. `submit_tasks`
6. `shutdown`
7. `abort_task`
8. `load_resources`

## 3. Behavior Contract

### 3.0 Start/Resume

1. If host is `NOT_RUN` or `IDLE`, transition host state to `RUNNING`.
2. Scheduler admission is enabled again.
3. Existing `queued` tasks remain eligible for future admission.

### 3.1 Graceful Stop

1. Transition host state to `DRAINING`.
2. Stop admitting new tasks.
3. Wait for running tasks to finish.
4. When all in-flight tasks (`starting` and `running`) reach zero, batch-convert all `pending` tasks to `queued` (they will be scheduled in the next `start` round).
5. Transition host state to `NOT_RUN`.
6. Keep all not-yet-admitted tasks in `queued` state.

Lifecycle intent:

1. Preserve in-flight tasks whenever possible.
2. Prevent new automatic admissions during drain.
3. Do not lose pending tasks: they are recovered to `queued` before entering `NOT_RUN`.

### 3.2 Force Stop

1. If host is `RUNNING` or `DRAINING`, transition host state to `STOPPING_FORCE` immediately.
2. Keep queued tasks in `queued` state for future resume.
3. Mark tasks currently in `pending` as `aborted` with `abort_reason = "force_stop"` and clear `blocked_by`.
4. Terminate running task processes.
5. Mark terminated tasks as `aborted`.
6. Mark tasks currently in `starting` as `aborted` when startup cannot complete due to force-stop.
7. Clear all resource locks and pending index entries.
8. Transition host state to `NOT_RUN`.

Lifecycle intent:

1. Enforce human-triggered interruption.
2. Drive affected tasks (`pending`, `starting`, `running`) to `aborted` through TaskManager transition commit.
3. `pending` tasks are aborted synchronously (no process to terminate); `starting`/`running` tasks require process termination.

### 3.3 Rerun

1. Accept target task IDs where current status is `succeeded`, `failed`, or `aborted`.
2. Reject task IDs where status is `pending` (task has not run; rerun is not applicable).
3. Reset selected task attempt metadata as needed by TaskManager policy.
4. Transition selected tasks back to `queued`, appended to the tail of the queue sorted by (priority, created_at).
5. If host is `RUNNING`, rerun tasks become immediately eligible for admission in the next tick.
6. If host is `NOT_RUN`, rerun tasks remain `queued` until next `start`.

### 3.4 Runtime Task Submission

1. `append` mode appends validated tasks to the existing queue and task set, inserting each task at the correct position by (priority, created_at).
2. `replace` mode replaces the task set only when there is no in-flight task and no `pending` task.
3. If in-flight tasks (`starting` or `running`) or `pending` tasks exist, `replace` must be rejected with `reason_code: inflight_exists`.
4. Submitted tasks must pass the extended validation contract:
   - `config_id` field is required and must be a positive integer matching a registered config in the loaded registry.
   - `priority` field is required and must be a positive integer.
   - All existing validations (unique task_id, non-empty commands) still apply.

### 3.5 Shutdown

1. `shutdown` transitions host state to `SHUTTING_DOWN`.
2. Default shutdown mode is `drain`.
3. In `drain` mode, host stops new admissions and exits after in-flight tasks complete.
4. In `force` mode, host aborts in-flight tasks and exits after process cleanup.
5. Optional timeout can escalate `drain` to forced termination.

### 3.6 Per-Task Abort

1. Accept a single `task_id`.
2. Reject if task does not exist (`task_not_found`).
3. Reject if task status is neither `running` nor `pending` (`task_not_abortable`).
4. If task is `running`: terminate the task's subprocess immediately.
5. If task is `pending`: remove from `pending_by_config` index; no process to terminate.
6. In both cases: transition task status to `aborted` with `abort_reason = "user_abort"`.
7. Host state is not changed; other tasks continue unaffected.
8. Resource lock is not held by `pending` tasks, so no lock release is needed for the pending abort path.

Lifecycle intent:

1. Allow targeted interruption of a single in-progress or waiting task without affecting host or sibling tasks.
2. Aborted task may be rerun by the user after abort.

### 3.7 Load Registry

1. Accept a registry object `{ "resources": [...] }` where each entry declares `config_id`, `name`, and `properties`.
2. Reject if host state is not `NOT_RUN` (`reason_code: invalid_host_state`).
3. Reject if the registry has already been loaded (`reason_code: already_loaded`).
4. Reject if the provided resources list is empty.
5. Registry is immutable after loading; a second `load_registry` call is always rejected.

## 4. Interface with Other Modules

1. Receives command requests from MonitorAPI or CLI.
2. Drives host/task state updates through TaskManager.
3. Uses TaskRunner process metadata to terminate running jobs.

## 5. Requirement Boundary

1. Requirement 2.4 (lifecycle governance): shared with TaskManager; this module does not redefine state invariants.
2. Requirement 2.5 (automatic progression): not owned here.
3. Requirement 2.6 (manual intervention): owned here, including start/stop/rerun/submit/shutdown command semantics and abort policy.

Related docs:

1. [design_task_manager.md](design_task_manager.md)
2. [design_task_runner.md](design_task_runner.md)
3. [design_monitor_api.md](design_monitor_api.md)
