"""Unit tests for Scheduler admission logic.

Covers design_scheduler.md §4 (Concrete Admission Policy):
- Rule 1: host not running → empty result
- Rule 2: resource threshold exceeded → empty result
- Rule 3/4: enforce max_concurrency cap, priority-sorted order
- Rule 5: skip non-runnable task IDs
- Rule 6: resource conflict → to_pending (non-blocking; slot is not consumed)
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


def _free_resource(resource: str) -> bool:
    return True


def _occupied_resource(resource: str) -> bool:
    return False


def _get_resource(task_id: str) -> str:
    return f"machine-{task_id}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_host_not_running_returns_empty():
    """Rule 1: host_running=False → no tasks admitted."""
    sched = _make_scheduler()
    queue = ["t1", "t2"]
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=False,
        is_runnable=_always_runnable, get_resource_usage=_no_resources,
    )
    assert to_start == []
    assert to_pending == []


def test_concurrency_cap():
    """Rule 3: running_count already at max → no new tasks."""
    sched = _make_scheduler(max_concurrency=2)
    queue = ["t1", "t2"]
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=2, host_running=True,
        is_runnable=_always_runnable, get_resource_usage=_no_resources,
    )
    assert to_start == []


def test_priority_order():
    """Rule 4: tasks are selected in queue order (priority pre-sorted by TaskManager)."""
    sched = _make_scheduler(max_concurrency=3)
    queue = ["a", "b", "c", "d"]
    to_start, _ = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable, get_resource_usage=_no_resources,
    )
    assert to_start == ["a", "b", "c"]


def test_skip_non_runnable():
    """Rule 5: non-runnable IDs are skipped (consumed from queue), slot is not consumed."""
    sched = _make_scheduler(max_concurrency=2)
    queue = ["skip-me", "t1", "t2"]
    to_start, _ = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=lambda tid: tid != "skip-me",
        get_resource_usage=_no_resources,
    )
    assert "skip-me" not in to_start
    assert len(to_start) == 2


def test_resource_conflict_goes_to_pending():
    """Rule 6: tasks with occupied resource go to to_pending, not to_start."""
    sched = _make_scheduler(max_concurrency=2)
    queue = ["t1"]
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_resource=_get_resource,
        is_resource_free=_occupied_resource,
    )
    assert to_start == []
    assert to_pending == ["t1"]


def test_resource_conflict_non_blocking():
    """Rule 6 + non-blocking: conflicting task does not block a free-resource task."""
    sched = _make_scheduler(max_concurrency=2)
    # t1 has occupied resource, t2 has free resource
    queue = ["t1", "t2"]
    occupied_resources = {"machine-t1"}
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_resource=lambda tid: f"machine-{tid}",
        is_resource_free=lambda res: res not in occupied_resources,
    )
    assert "t1" not in to_start
    assert "t1" in to_pending
    assert "t2" in to_start


def test_resource_conflict_does_not_consume_slot():
    """Pending task must not reduce available slots for subsequent tasks."""
    sched = _make_scheduler(max_concurrency=1)
    # t1: resource occupied (→ pending), t2: resource free (→ should still start)
    queue = ["t1", "t2"]
    occupied = {"machine-t1"}
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_resource=lambda tid: f"machine-{tid}",
        is_resource_free=lambda res: res not in occupied,
    )
    assert to_start == ["t2"]
    assert to_pending == ["t1"]


def test_resource_threshold_exceeded_suspends_all():
    """Rule 2: host resource threshold exceeded → both to_start and to_pending empty."""
    from scheduler import ResourceUsage
    sched = _make_scheduler(max_cpu_percent=50.0)
    queue = ["t1"]
    high_cpu = lambda: ResourceUsage(cpu_percent=90.0, memory_percent=10.0, disk_active_percent=10.0)
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=high_cpu,
    )
    assert to_start == []
    assert to_pending == []

