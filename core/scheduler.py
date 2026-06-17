from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ResourceUsage:
    cpu_percent: float
    memory_percent: float
    disk_active_percent: float


class Scheduler:
    """Admission controller for queued tasks.

    Returns (to_start, to_pending) per tick:
    - to_start: (task_id, assigned_resource) tuples admitted to starting
    - to_pending: task IDs blocked by resource conflict (resource is occupied)
    """

    def __init__(
        self,
        max_concurrency: int | None,
        max_cpu_percent: float = 75.0,
        max_memory_percent: float = 75.0,
        max_disk_active_percent: float = 80.0,
    ) -> None:
        self.max_concurrency = None if max_concurrency is None else max(1, max_concurrency)
        self.max_cpu_percent = min(100.0, max(1.0, float(max_cpu_percent)))
        self.max_memory_percent = min(100.0, max(1.0, float(max_memory_percent)))
        self.max_disk_active_percent = min(100.0, max(1.0, float(max_disk_active_percent)))

    def pick_next_tasks(
        self,
        queue: list[str],
        running_count: int,
        host_running: bool,
        is_runnable: Callable[[str], bool],
        get_resource_usage: Callable[[], ResourceUsage | None] | None = None,
        get_task_resource: Callable[[str], str] | None = None,
        is_resource_free: Callable[[str], bool] | None = None,
        get_task_config: Callable[[str], int] | None = None,
        pick_free_resource: Callable[[int, set[str]], str | None] | None = None,
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Return (to_start, to_pending) for the current scheduling tick.

        Queue is expected to be pre-sorted by (priority asc, created_at asc).
        Resource conflict detection is non-blocking: a pending task does not
        consume a concurrency slot; the scan continues to the next candidate.
        """
        if not host_running:
            return [], []

        if get_resource_usage is not None:
            usage = get_resource_usage()
            if usage is not None and (
                usage.cpu_percent >= self.max_cpu_percent
                or usage.memory_percent >= self.max_memory_percent
                or usage.disk_active_percent >= self.max_disk_active_percent
            ):
                return [], []

        # max_concurrency=None means no concurrency cap: admission is limited
        # only by the CPU/memory/disk host thresholds checked above.
        if self.max_concurrency is None:
            available_slots: int | None = None
        else:
            available_slots = self.max_concurrency - running_count
            if available_slots <= 0:
                return [], []

        to_start: list[tuple[str, str]] = []
        to_pending: list[str] = []
        # Resources claimed by earlier candidates within THIS tick. The real
        # resource lock is written by TaskManager only after pick_next_tasks
        # returns, so we must track in-tick claims here to prevent two tasks
        # sharing the same resource from both being admitted in one tick.
        claimed_in_tick: set[str] = set()

        while queue:
            next_id = queue.pop(0)

            if not is_runnable(next_id):
                # Non-runnable: skip without consuming a slot or going to pending.
                continue

            resource_for_task = ""

            # New path: config-pool dispatch. If config_id is non-zero and a free
            # resource can be chosen from the pool, admit with the chosen resource.
            # If no free resource exists, move to pending without consuming a slot.
            if get_task_config is not None and pick_free_resource is not None:
                config_id = get_task_config(next_id)
                if config_id:
                    picked = pick_free_resource(config_id, claimed_in_tick)
                    if picked is None:
                        to_pending.append(next_id)
                        continue
                    resource_for_task = picked
                    claimed_in_tick.add(resource_for_task)
                    to_start.append((next_id, resource_for_task))
                    if available_slots is not None:
                        available_slots -= 1
                        if available_slots <= 0:
                            break
                    continue

            # Old path: one task bound to one specific resource.
            if get_task_resource is not None and is_resource_free is not None:
                resource = get_task_resource(next_id)
                resource_for_task = resource
                if resource in claimed_in_tick or not is_resource_free(resource):
                    # Resource conflict: send to pending, do NOT consume a slot.
                    to_pending.append(next_id)
                    continue
                claimed_in_tick.add(resource)

            to_start.append((next_id, resource_for_task))
            if available_slots is not None:
                available_slots -= 1
                if available_slots <= 0:
                    break

        return to_start, to_pending
