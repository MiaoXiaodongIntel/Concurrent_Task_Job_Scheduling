# Scheduler Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

Implementation file: [../scheduler.py](../scheduler.py)

## 1. Responsibility

`Scheduler` controls admission from queued task IDs to start candidates.

## 2. Inputs

1. `queue: list[str]` (mutable FIFO task_id queue)
2. `running_count: int`
3. `host_running: bool` (`true` only when host state is `RUNNING`)
4. `is_runnable: Callable[[str], bool]`
5. `get_resource_usage: Callable[[], ResourceUsage | None]` (optional, provided by TaskManager), including `cpu_percent`, `memory_percent`, and `disk_active_percent`

## 3. Outputs

1. `list[str]` of selected task IDs for current scheduling tick

## 4. Concrete Admission Policy (Current Implementation)

1. If host is not running: return empty list.
2. If resource snapshot is available and any threshold is exceeded (`cpu`, `memory`, `disk_active_time`): return empty list for this tick.
3. Enforce hard cap `max_concurrency`.
4. Select from queue in FIFO order.
5. Skip non-runnable task IDs using `is_runnable`.
6. Stop selecting when available slots are exhausted.

## 5. Side Effects and Constraints

1. `pick_next_tasks` consumes from input `queue` by popping selected/skipped IDs.
2. Scheduler does not modify task state directly.
3. Resource guardrails suspend admission only; they do not mutate task state.

## 6. Configurable Knobs

1. `max_concurrency`
2. `max_cpu_percent`
3. `max_memory_percent`
4. `max_disk_active_percent`

GUI-friendly defaults in current implementation:

1. `max_cpu_percent=75`
2. `max_memory_percent=75`
3. `max_disk_active_percent=80`

### 6.1 Threshold Range and Max-Value Rationale

Threshold knobs are normalized in implementation to `1.0..100.0`.

1. Lower bound `1.0`: avoids degenerate configuration that permanently blocks admission.
2. Upper bound `100.0`: all three signals are percentages and cannot exceed 100 by definition.

Technical rationale for max setting:

1. Allowing values above `100` has no physical meaning and can hide misconfiguration.
2. Clamping at `100` keeps scheduler behavior deterministic across API/CLI callers.

Simple tuning guidance for GUI-friendly hosts:

1. Keep `cpu` and `memory` thresholds at `75` by default.
2. Keep `disk_active_time` threshold at `80` by default.
3. If the host has no interactive GUI requirement, values can be raised gradually, but values above `85` increase responsiveness risk.

Scheduling tick interval is configured in `TaskManager`, not inside `Scheduler`.

When host is `NOT_RUN`, `DRAINING`, `STOPPING_FORCE`, `IDLE`, or `SHUTTING_DOWN`, TaskManager passes `host_running=false` and scheduler admission is suspended.

## 7. Interface with TaskManager

1. `TaskManager._try_schedule()` calls `Scheduler.pick_next_tasks(...)`.
2. Returned task IDs are started by `TaskManager._start_task(...)`.
3. Status transitions remain exclusively in `TaskManager`.

Related docs:

1. [design_task_manager.md](design_task_manager.md)
2. [design_monitor_api.md](design_monitor_api.md)
