"""Unit tests for Scheduler admission logic (config-pool mode)."""

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


def _ids(to_start: list[tuple[str, str]]) -> list[str]:
    return [task_id for task_id, _ in to_start]


def _assigned(to_start: list[tuple[str, str]]) -> list[str]:
    return [resource for _, resource in to_start]


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
    pool = {1: ["m1", "m2", "m3", "m4"]}
    to_start, _ = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable, get_resource_usage=_no_resources,
        get_task_config=lambda tid: 1,
        pick_free_resource=lambda config_id, claimed: next(
            (res for res in pool[config_id] if res not in claimed),
            None,
        ),
    )
    assert _ids(to_start) == ["a", "b", "c"]


def test_skip_non_runnable():
    """Rule 5: non-runnable IDs are skipped (consumed from queue), slot is not consumed."""
    sched = _make_scheduler(max_concurrency=2)
    queue = ["skip-me", "t1", "t2"]
    pool = {1: ["m1", "m2", "m3"]}
    to_start, _ = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=lambda tid: tid != "skip-me",
        get_resource_usage=_no_resources,
        get_task_config=lambda tid: 1,
        pick_free_resource=lambda config_id, claimed: next(
            (res for res in pool[config_id] if res not in claimed),
            None,
        ),
    )
    assert "skip-me" not in _ids(to_start)
    assert len(to_start) == 2


def test_no_free_pool_resource_goes_to_pending():
    """When no free resource is available in pool, task goes to pending."""
    sched = _make_scheduler(max_concurrency=2)
    queue = ["t1"]
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_config=lambda tid: 1,
        pick_free_resource=lambda config_id, claimed: None,
    )
    assert to_start == []
    assert to_pending == ["t1"]


def test_pool_pending_non_blocking():
    """Pending task does not consume slot; later task can still start."""
    sched = _make_scheduler(max_concurrency=2)
    queue = ["t1", "t2"]
    pool = {1: ["mx"], 2: ["my"]}
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_config=lambda tid: 1 if tid == "t1" else 2,
        pick_free_resource=lambda config_id, claimed: next(
            (res for res in pool[config_id] if res not in claimed and not (config_id == 1)),
            None,
        ),
    )
    assert _ids(to_start) == ["t2"]
    assert to_pending == ["t1"]


def test_pending_does_not_consume_slot():
    """Pending task must not reduce available slots for subsequent tasks."""
    sched = _make_scheduler(max_concurrency=1)
    queue = ["t1", "t2"]
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue, running_count=0, host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_config=lambda tid: 1 if tid == "t1" else 2,
        pick_free_resource=lambda config_id, claimed: None if config_id == 1 else "machine-t2",
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


def test_same_pool_single_resource_same_tick():
    """Two tasks sharing one pool resource in one tick: one starts, one pending."""
    sched = _make_scheduler(max_concurrency=4)
    queue = ["t1", "t2"]
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue,
        running_count=0,
        host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_config=lambda tid: 1,
        pick_free_resource=lambda config_id, claimed: None if "m2201" in claimed else "m2201",
    )
    assert _ids(to_start) == ["t1"]
    assert _assigned(to_start) == ["m2201"]
    assert to_pending == ["t2"]


def test_same_pool_three_tasks_same_tick():
    """Three tasks on one single-resource pool in one tick: one starts, two pending."""
    sched = _make_scheduler(max_concurrency=4)
    queue = ["t1", "t2", "t3"]
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue,
        running_count=0,
        host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_config=lambda tid: 1,
        pick_free_resource=lambda config_id, claimed: None if "shared-machine" in claimed else "shared-machine",
    )
    assert _ids(to_start) == ["t1"]
    assert to_pending == ["t2", "t3"]


def test_in_tick_claim_does_not_consume_slot():
    """In-tick claim must not consume concurrency slot for pending tasks."""
    sched = _make_scheduler(max_concurrency=2)
    queue = ["t1", "t2", "t3"]
    to_start, to_pending = sched.pick_next_tasks(
        queue=queue,
        running_count=0,
        host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_config=lambda tid: 1 if tid in {"t1", "t2"} else 2,
        pick_free_resource=lambda config_id, claimed: (
            None
            if (config_id == 1 and "machine-X" in claimed)
            else ("machine-X" if config_id == 1 else "machine-Y")
        ),
    )
    assert _ids(to_start) == ["t1", "t3"]
    assert to_pending == ["t2"]


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
        get_task_config=lambda tid: 1,
        pick_free_resource=lambda config_id, claimed: (
            "m1" if "m1" not in claimed else ("m2" if "m2" not in claimed else None)
        ),
    )
    assert _ids(to_start) == ["t1", "t2"], "all queued tasks must be admitted after load drops"
    assert to_pending == []

