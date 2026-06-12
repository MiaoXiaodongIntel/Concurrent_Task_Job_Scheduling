from __future__ import annotations

from typing import Callable


class Scheduler:
    """Admission controller for queued tasks."""

    def __init__(self, max_concurrency: int) -> None:
        self.max_concurrency = max(1, max_concurrency)

    def pick_next_tasks(
        self,
        queue: list[str],
        running_count: int,
        host_running: bool,
        is_runnable: Callable[[str], bool],
    ) -> list[str]:
        if not host_running:
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
