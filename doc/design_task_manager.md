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

Terminal states:

1. `succeeded`
2. `failed`
3. `aborted`

## 3. Host Lifecycle Model

Host states:

1. `RUNNING`
2. `STOPPED`

## 4. Data Ownership

TaskManager owns:

1. full `TaskJob` snapshots
2. queue/running/completed counters
3. task-to-process mapping for active jobs
4. runtime timestamps for status and output activity
5. per-task log file path

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
5. `_all_done()` terminates host loop when all tasks are terminal.

Related docs:

1. [design_scheduler.md](design_scheduler.md)
2. [design_control_plane.md](design_control_plane.md)
3. [design_monitor_api.md](design_monitor_api.md)
