# Scheduler Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

Implementation file: [../core/scheduler.py](../core/scheduler.py)

## 1. Responsibility

`Scheduler` controls admission from queued task IDs to start candidates.

## 2. Inputs

1. `queue: list[str]` (priority-sorted task_id queue, maintained by TaskManager; lower priority number = higher priority, stable by `created_at`)
2. `running_count: int`
3. `host_running: bool` (`true` only when host state is `RUNNING`)
4. `is_runnable: Callable[[str], bool]`
5. `get_resource_usage: Callable[[], ResourceUsage | None]` (optional, provided by TaskManager), including `cpu_percent`, `memory_percent`, and `disk_active_percent`
6. `get_task_config: Callable[[str], int]` — returns `config_id` for a given task_id
7. `pick_free_resource: Callable[[int, set[str]], str | None]` — picks one available resource name from the target config pool

## 3. Outputs

1. `tuple[list[tuple[str, str]], list[str]]` — `(to_start, to_pending)` for current scheduling tick
   - `to_start`: `(task_id, assigned_resource)` tuples admitted to `starting`
   - `to_pending`: task IDs blocked by resource conflict, to be moved to `pending`

## 4. Concrete Admission Policy (Current Implementation)

1. If host is not running: return `([], [])`.
2. If host resource snapshot is available and any host threshold is exceeded (`cpu`, `memory`, `disk_active_time`): return `([], [])` for this tick.
3. Enforce hard cap `max_concurrency` for `to_start` slots (`available_slots = max_concurrency - running_count`). When `max_concurrency` is `None`, no slot cap is applied (`available_slots` is unbounded) and admission is governed solely by the host thresholds in step 2.
4. Iterate queue in order (queue is pre-sorted by TaskManager in priority order):
   a. Pop next task_id.
   b. Check `is_runnable`: if `False`, skip (do not count against slots, do not pending).
   c. Call `pick_free_resource(config_id, claimed_in_tick)` for the task's `config_id`:
      - resource chosen: append `(task_id, assigned_resource)` to `to_start`, decrement `available_slots`.
      - no resource chosen (pool exhausted): append task_id to `to_pending` (does **not** consume a slot).
   d. Continue until `available_slots == 0` **or** queue is exhausted.
5. Return `(to_start, to_pending)`.

Key behavioral properties:
- Resource conflict does **not** block slots: a pending task frees the slot for the next task (Decision H).
- Scheduler maintains `claimed_in_tick` to prevent same-tick double assignment.

## 5. Side Effects and Constraints

1. `pick_next_tasks` consumes from input `queue` by popping selected/skipped/pending IDs.
2. Scheduler does not modify task state directly; TaskManager applies pending and starting transitions.
3. Host resource guardrails suspend admission only; they do not mutate task state.
4. Queue ordering is maintained by TaskManager (not Scheduler); Scheduler treats the queue as already sorted.

## 6. Configurable Knobs

1. `max_concurrency` (`None` = no concurrency cap; admission limited only by host thresholds)
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

When host is `NOT_RUN`, `DRAINING`, `STOPPING_FORCE`, or `SHUTTING_DOWN`, TaskManager passes `host_running=false` and scheduler admission is suspended.

## 7. Interface with TaskManager

1. `TaskManager._try_schedule()` calls `Scheduler.pick_next_tasks(...)`.
2. `to_start` tuples are started by `TaskManager._start_task(task_id, assigned_resource)`, which writes the resource lock when entering `starting`.
3. `to_pending` task IDs are moved to `pending` state by TaskManager, stored in `pending_by_resource`.
4. Status transitions remain exclusively in `TaskManager`.
5. Queue priority ordering is maintained by TaskManager; Scheduler receives an already-sorted queue.

Related docs:

1. [design_task_manager.md](design_task_manager.md)
2. [design_monitor_api.md](design_monitor_api.md)
