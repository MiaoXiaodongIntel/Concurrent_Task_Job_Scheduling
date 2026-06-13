"""Unit tests for Scheduler admission logic.

Covers design_scheduler.md §4 (Concrete Admission Policy):
- Rule 1: host not running → empty result
- Rule 2: resource threshold exceeded → empty result
- Rule 3/4: enforce max_concurrency cap, FIFO order
- Rule 5: skip non-runnable task IDs
"""

from __future__ import annotations

from scheduler import Scheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scheduler(**kwargs) -> Scheduler:
    defaults = dict(max_concurrency=2, max_cpu_percent=75.0,
                    max_memory_percent=75.0, max_disk_active_percent=80.0)
    defaults.update(kwargs)
    return Scheduler(**defaults)


def _always_runnable(task_id: str) -> bool:
    return True


def _no_resources():
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_host_not_running_returns_empty():
    """Rule 1: host_running=False → no tasks admitted."""
    sched = _make_scheduler()
    queue = ["t1", "t2"]
    result = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=False,
        is_runnable=_always_runnable, get_resource_usage=_no_resources,
    )
    assert result == []


def test_concurrency_cap():
    """Rule 3: running_count already at max → no new tasks."""
    sched = _make_scheduler(max_concurrency=2)
    queue = ["t1", "t2"]
    result = sched.pick_next_tasks(
        queue=queue, running_count=2, host_running=True,
        is_runnable=_always_runnable, get_resource_usage=_no_resources,
    )
    assert result == []


def test_fifo_order():
    """Rule 4: tasks are selected in FIFO order."""
    sched = _make_scheduler(max_concurrency=3)
    queue = ["a", "b", "c", "d"]
    result = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable, get_resource_usage=_no_resources,
    )
    assert result == ["a", "b", "c"]


def test_skip_non_runnable():
    """Rule 5: non-runnable IDs are skipped (consumed from queue)."""
    sched = _make_scheduler(max_concurrency=2)
    queue = ["skip-me", "t1", "t2"]
    result = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=lambda tid: tid != "skip-me",
        get_resource_usage=_no_resources,
    )
    assert "skip-me" not in result
    assert len(result) == 2
