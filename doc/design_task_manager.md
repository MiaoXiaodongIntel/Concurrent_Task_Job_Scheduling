# TaskManager and State Machine Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

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
2. `DRAINING`
3. `STOPPING_FORCE`
4. `STOPPED`

## 4. Data Ownership

TaskManager owns:

1. full `TaskJob` snapshots
2. queue/running/completed counters
3. task-to-process mapping for active jobs
4. runtime timestamps for status and output activity

## 5. Interface to Other Modules

Inbound events:

1. task submission
2. runner start/stream/end events
3. scheduler admission decisions
4. control-plane stop actions

Outbound views:

1. current queue and runnable set
2. active running task list
3. task snapshots for MonitorAPI

## 6. Consistency Rules

1. All status changes must pass transition validation.
2. Counters must be derivable from task snapshots.
3. Task snapshot updates must be atomic at module boundary.

Related docs:

1. [design_scheduler.md](design_scheduler.md)
2. [design_control_plane.md](design_control_plane.md)
3. [design_monitor_api.md](design_monitor_api.md)
