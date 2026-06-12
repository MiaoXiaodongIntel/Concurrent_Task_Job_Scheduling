# ControlPlane Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

## 1. Responsibility

ControlPlane handles host-level control requests and coordinates stop behavior.

## 2. Control Commands

1. `graceful_stop`
2. `force_stop`

## 3. Behavior Contract

### 3.1 Graceful Stop

1. Transition host state to `DRAINING`.
2. Stop admitting new tasks.
3. Wait for running tasks to finish.
4. Transition host state to `STOPPED`.

### 3.2 Force Stop

1. Transition host state to `STOPPING_FORCE`.
2. Mark queued tasks as `aborted`.
3. Terminate running task processes.
4. Mark terminated tasks as `aborted`.
5. Transition host state to `STOPPED`.

## 4. Interface with Other Modules

1. Receives command requests from MonitorAPI or CLI.
2. Drives host/task state updates through TaskManager.
3. Uses TaskRunner process metadata to terminate running jobs.

Related docs:

1. [design_task_manager.md](design_task_manager.md)
2. [design_task_runner.md](design_task_runner.md)
3. [design_monitor_api.md](design_monitor_api.md)
