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

## 3. Outputs

1. `list[str]` of selected task IDs for current scheduling tick

## 4. Concrete Admission Policy (Current Implementation)

1. If host is not running: return empty list.
2. Enforce hard cap `max_concurrency`.
3. Select from queue in FIFO order.
4. Skip non-runnable task IDs using `is_runnable`.
5. Stop selecting when available slots are exhausted.

## 5. Side Effects and Constraints

1. `pick_next_tasks` consumes from input `queue` by popping selected/skipped IDs.
2. Scheduler does not modify task state directly.
3. Current implementation has no resource-threshold guardrails.

## 6. Configurable Knobs

1. `max_concurrency`

Scheduling tick interval is configured in `TaskManager`, not inside `Scheduler`.

When host is `NOT_RUN`, `DRAINING`, `STOPPING_FORCE`, or `STOPPED`, TaskManager passes `host_running=false` and scheduler admission is suspended.

## 7. Interface with TaskManager

1. `TaskManager._try_schedule()` calls `Scheduler.pick_next_tasks(...)`.
2. Returned task IDs are started by `TaskManager._start_task(...)`.
3. Status transitions remain exclusively in `TaskManager`.

Related docs:

1. [design_task_manager.md](design_task_manager.md)
2. [design_monitor_api.md](design_monitor_api.md)
