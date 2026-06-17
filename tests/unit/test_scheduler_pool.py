"""Unit tests for Scheduler config-pool dispatch path (Step 4)."""

from __future__ import annotations

from scheduler import Scheduler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scheduler(**kwargs) -> Scheduler:
    defaults = dict(max_concurrency=3, max_cpu_percent=75.0,
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

def test_pool_two_tasks_two_resources_both_admitted():
    sched = _make_scheduler(max_concurrency=4)
    queue = ["task-A", "task-B"]

    pool = {1: ["resource-x", "resource-y"]}

    def get_task_config(task_id: str) -> int:
        return 1

    def pick_free_resource(config_id: int, claimed: set[str]) -> str | None:
        for resource in pool.get(config_id, []):
            if resource not in claimed:
                return resource
        return None

    to_start, to_pending = sched.pick_next_tasks(
        queue=queue,
        running_count=0,
        host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_config=get_task_config,
        pick_free_resource=pick_free_resource,
    )

    assert _ids(to_start) == ["task-A", "task-B"]
    assert _assigned(to_start) == ["resource-x", "resource-y"]
    assert to_pending == []


def test_pool_two_tasks_one_resource_second_pending():
    sched = _make_scheduler(max_concurrency=4)
    queue = ["task-A", "task-B"]

    pool = {1: ["resource-x"]}

    def get_task_config(task_id: str) -> int:
        return 1

    def pick_free_resource(config_id: int, claimed: set[str]) -> str | None:
        for resource in pool.get(config_id, []):
            if resource not in claimed:
                return resource
        return None

    to_start, to_pending = sched.pick_next_tasks(
        queue=queue,
        running_count=0,
        host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_config=get_task_config,
        pick_free_resource=pick_free_resource,
    )

    assert _ids(to_start) == ["task-A"]
    assert _assigned(to_start) == ["resource-x"]
    assert to_pending == ["task-B"]


def test_pool_claimed_in_tick_prevents_double_assign():
    sched = _make_scheduler(max_concurrency=4)
    queue = ["task-A", "task-B", "task-C"]

    # picker always returns the same preferred resource if available,
    # claimed_in_tick should force only one admission on this resource.
    def get_task_config(task_id: str) -> int:
        return 9

    def pick_free_resource(config_id: int, claimed: set[str]) -> str | None:
        preferred = "resource-only"
        if preferred in claimed:
            return None
        return preferred

    to_start, to_pending = sched.pick_next_tasks(
        queue=queue,
        running_count=0,
        host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_config=get_task_config,
        pick_free_resource=pick_free_resource,
    )

    assert _ids(to_start) == ["task-A"]
    assert _assigned(to_start) == ["resource-only"]
    assert to_pending == ["task-B", "task-C"]


def test_pool_fallback_to_old_path_when_no_config_callback():
    sched = _make_scheduler(max_concurrency=2)
    queue = ["t1", "t2"]

    to_start, to_pending = sched.pick_next_tasks(
        queue=queue,
        running_count=0,
        host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_resource=lambda task_id: f"machine-{task_id}",
        is_resource_free=lambda resource: resource != "machine-t1",
        # no get_task_config/pick_free_resource -> old path
    )

    assert _ids(to_start) == ["t2"]
    assert _assigned(to_start) == ["machine-t2"]
    assert to_pending == ["t1"]


def test_pool_zero_config_id_falls_back_to_old_path():
    sched = _make_scheduler(max_concurrency=2)
    queue = ["t1"]

    to_start, to_pending = sched.pick_next_tasks(
        queue=queue,
        running_count=0,
        host_running=True,
        is_runnable=_always_runnable,
        get_resource_usage=_no_resources,
        get_task_resource=lambda task_id: "machine-t1",
        is_resource_free=lambda resource: True,
        get_task_config=lambda task_id: 0,
        pick_free_resource=lambda config_id, claimed: "resource-from-pool",
    )

    assert _ids(to_start) == ["t1"]
    assert _assigned(to_start) == ["machine-t1"]
    assert to_pending == []
