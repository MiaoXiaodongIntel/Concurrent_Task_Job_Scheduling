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


def _ids(to_start: list[tuple[str, str]]) -> list[str]:
    return [task_id for task_id, _ in to_start]


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
    assert _ids(to_start) == ["a", "b", "c"]


def test_skip_non_runnable():
    """Rule 5: non-runnable IDs are skipped (consumed from queue), slot is not consumed."""
    sched = _make_scheduler(max_concurrency=2)
    queue = ["skip-me", "t1", "t2"]
    to_start, _ = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=lambda tid: tid != "skip-me",
        get_resource_usage=_no_resources,
    )
    assert "skip-me" not in _ids(to_start)
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
    assert "t1" not in _ids(to_start)
    assert "t1" in to_pending
    assert "t2" in _ids(to_start)


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
    assert _ids(to_start) == ["t2"]
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


def test_same_resource_two_tasks_same_tick_lock_empty():
    """Two tasks sharing the same resource, resource lock empty (no task yet running).

    Before the fix, pick_next_tasks saw the resource as free for both candidates
    in one tick and returned both in to_start, causing double-admission onto the
    same machine.  After the fix, claimed_in_tick tracks in-tick claims, so only
    the first task is admitted and the second goes to to_pending.
    """
    sched = _make_scheduler(max_concurrency=4)
    queue = ["tc1-m2201", "tc2-m2201"]
    resource_map = {"tc1-m2201": "m2201", "tc2-m2201": "m2201"}
    # is_resource_free returns True for everything — simulates an empty _resource_lock
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue,
        running_count=0,
        host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_resource=lambda t: resource_map[t],
        is_resource_free=lambda r: True,
    )
    assert _ids(to_start) == ["tc1-m2201"], "only the first task on m2201 should start"
    assert to_pending == ["tc2-m2201"], "second task on m2201 must be pending, not started"


def test_same_resource_three_tasks_same_tick():
    """Three tasks on the same machine in one tick: only one starts, two go pending."""
    sched = _make_scheduler(max_concurrency=4)
    queue = ["t1", "t2", "t3"]
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue,
        running_count=0,
        host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_resource=lambda t: "shared-machine",
        is_resource_free=lambda r: True,
    )
    assert _ids(to_start) == ["t1"]
    assert to_pending == ["t2", "t3"]


def test_same_resource_in_tick_claim_does_not_consume_slot():
    """In-tick resource claim must not consume a concurrency slot for pending tasks.

    Setup: 2 slots, queue=[t1, t2, t3], t1+t2 share machine-X, t3 has machine-Y.
    - t1 claims slot + machine-X → to_start (available_slots: 2→1)
    - t2 conflicts on machine-X (in-tick claim) → to_pending; slot NOT decremented (still 1)
    - t3 on machine-Y gets the remaining slot → to_start (available_slots: 1→0)
    If pending consumed a slot, t3 would NOT start. Verifies the non-blocking invariant
    for in-tick claims (same guarantee as for externally-locked resources).
    """
    sched = _make_scheduler(max_concurrency=2)
    queue = ["t1", "t2", "t3"]
    resource_map = {"t1": "machine-X", "t2": "machine-X", "t3": "machine-Y"}
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue,
        running_count=0,
        host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_resource=lambda t: resource_map[t],
        is_resource_free=lambda r: True,
    )
    assert _ids(to_start) == ["t1", "t3"]    # t3 gets the slot freed by t2's pending
    assert to_pending == ["t2"]        # machine-X in-tick conflict → pending, no slot consumed


def test_resource_threshold_queue_not_mutated():
    """Rule 2: when host resource threshold is exceeded, queue must not be mutated.

    If pick_next_tasks pops items from the queue before the threshold check, tasks
    would be silently lost. This verifies the queue is identical after the call.
    """
    from scheduler import ResourceUsage
    sched = _make_scheduler(max_cpu_percent=50.0)
    queue = ["t1", "t2", "t3"]
    high_cpu = lambda: ResourceUsage(cpu_percent=90.0, memory_percent=10.0, disk_active_percent=10.0)
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=high_cpu,
    )
    assert to_start == []
    assert to_pending == []
    assert queue == ["t1", "t2", "t3"], "queue must be untouched when threshold is exceeded"


def test_resource_threshold_tasks_start_after_load_drops():
    """Rule 2: tasks blocked by high load resume normally once the threshold clears.

    Simulates two consecutive ticks:
    - Tick 1: CPU over threshold → nothing admitted, queue unchanged.
    - Tick 2: CPU back to normal → tasks are admitted from the same queue.
    """
    from scheduler import ResourceUsage
    sched = _make_scheduler(max_concurrency=4, max_cpu_percent=75.0)
    queue = ["t1", "t2"]

    # Tick 1 – high CPU load.
    high_cpu = lambda: ResourceUsage(cpu_percent=90.0, memory_percent=10.0, disk_active_percent=10.0)
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=high_cpu,
    )
    assert to_start == []
    assert to_pending == []
    assert queue == ["t1", "t2"], "queue must survive the high-load tick intact"

    # Tick 2 – load drops below threshold; same queue reference is reused.
    normal_cpu = lambda: ResourceUsage(cpu_percent=30.0, memory_percent=10.0, disk_active_percent=10.0)
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=normal_cpu,
    )
    assert _ids(to_start) == ["t1", "t2"], "all queued tasks must be admitted after load drops"
    assert to_pending == []

