from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ResourceUsage:
    cpu_percent: float
    memory_percent: float
    disk_active_percent: float


class Scheduler:
    """Admission controller for queued tasks."""

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
    ) -> list[str]:
        if not host_running:
            return []

        if get_resource_usage is not None:
            usage = get_resource_usage()
            if usage is not None and (
                usage.cpu_percent >= self.max_cpu_percent
                or usage.memory_percent >= self.max_memory_percent
                or usage.disk_active_percent >= self.max_disk_active_percent
            ):
                return []

        available_slots = self.max_concurrency - running_count
        if available_slots <= 0:
            return []

        selected: list[str] = []
        while available_slots > 0 and queue:
            next_id = queue.pop(0)
            if not is_runnable(next_id):
                continue
            selected.append(next_id)
            available_slots -= 1

        return selected
