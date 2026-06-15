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
    - to_start: task IDs admitted to the starting phase (resource is free)
    - to_pending: task IDs blocked by resource conflict (resource is occupied)
    """

    def __init__(
        self,
        max_concurrency: int,
        max_cpu_percent: float = 75.0,
        max_memory_percent: float = 75.0,
        max_disk_active_percent: float = 80.0,
    ) -> None:
        self.max_concurrency = max(1, max_concurrency)
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
    ) -> tuple[list[str], list[str]]:
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

        available_slots = self.max_concurrency - running_count
        if available_slots <= 0:
            return [], []

        to_start: list[str] = []
        to_pending: list[str] = []

        while queue:
            next_id = queue.pop(0)

            if not is_runnable(next_id):
                # Non-runnable: skip without consuming a slot or going to pending.
                continue

            if get_task_resource is not None and is_resource_free is not None:
                resource = get_task_resource(next_id)
                if not is_resource_free(resource):
                    # Resource conflict: send to pending, do NOT consume a slot.
                    to_pending.append(next_id)
                    continue

            to_start.append(next_id)
            available_slots -= 1
            if available_slots <= 0:
                break

        return to_start, to_pending
