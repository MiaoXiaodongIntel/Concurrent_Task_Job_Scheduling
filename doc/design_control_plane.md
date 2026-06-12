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

## 3. Behavior Contract

### 3.0 Start/Resume

1. If host is `NOT_RUN` or `STOPPED`, transition host state to `RUNNING`.
2. Scheduler admission is enabled again.
3. Existing `queued` tasks remain eligible for future admission.

### 3.1 Graceful Stop

1. Transition host state to `DRAINING`.
2. Stop admitting new tasks.
3. Wait for running tasks to finish.
4. Transition host state to `STOPPED`.
5. Keep all not-yet-admitted tasks in `queued` state.

Lifecycle intent:

1. Preserve in-flight tasks whenever possible.
2. Prevent new automatic admissions during drain.

### 3.2 Force Stop

1. If host is `RUNNING` or `DRAINING`, transition host state to `STOPPING_FORCE` immediately.
2. Keep queued tasks in `queued` state for future resume.
3. Terminate running task processes.
4. Mark terminated tasks as `aborted`.
5. Mark tasks currently in `starting` as `aborted` when startup cannot complete due to force-stop.
6. Transition host state to `STOPPED`.

Lifecycle intent:

1. Enforce human-triggered interruption.
2. Drive affected tasks to `aborted` through TaskManager transition commit.

### 3.3 Rerun

1. Accept target task IDs where current status is `succeeded` or `failed`.
2. Reset selected task attempt metadata as needed by TaskManager policy.
3. Transition selected tasks back to `queued`.
4. If host is `RUNNING`, rerun tasks become immediately eligible for admission.
5. If host is `NOT_RUN` or `STOPPED`, rerun tasks remain `queued` until next `start`.

## 4. Interface with Other Modules

1. Receives command requests from MonitorAPI or CLI.
2. Drives host/task state updates through TaskManager.
3. Uses TaskRunner process metadata to terminate running jobs.

## 5. Requirement Boundary

1. Requirement 2.4 (lifecycle governance): shared with TaskManager; this module does not redefine state invariants.
2. Requirement 2.5 (automatic progression): not owned here.
3. Requirement 2.6 (manual intervention): owned here, including start/stop/rerun command semantics and abort policy.

Related docs:

1. [design_task_manager.md](design_task_manager.md)
2. [design_task_runner.md](design_task_runner.md)
3. [design_monitor_api.md](design_monitor_api.md)
