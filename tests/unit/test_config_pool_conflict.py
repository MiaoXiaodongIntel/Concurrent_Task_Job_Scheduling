"""Unit tests for config-pool pending/release behavior in TaskManager (Step 6)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from resource_registry import ConfigEntry, ResourceEntry, ResourceRegistry
from task_manager import HostState, TaskJob, TaskManager, TaskStatus


def _make_task(task_id: str, *, config_id: int, priority: int = 1) -> TaskJob:
    return TaskJob(task_id=task_id, commands=["echo hi"], resource="", config_id=config_id, priority=priority)


def _make_registry(resources: list[tuple[int, str]]) -> ResourceRegistry:
    # resources: list[(config_id, resource_name)]
    resource_entries: dict[int, ResourceEntry] = {}
    resource_name_index: dict[str, int] = {}
    resources_by_config: dict[int, list[int]] = {}
    configs: dict[int, ConfigEntry] = {}

    next_id = 1
    for config_id, resource_name in resources:
        resource_entries[next_id] = ResourceEntry(
            id=next_id,
            name=resource_name,
            properties={"ip": f"10.0.0.{next_id}"},
            config_id=config_id,
        )
        resource_name_index[resource_name] = next_id
        resources_by_config.setdefault(config_id, []).append(next_id)
        configs.setdefault(config_id, ConfigEntry(id=config_id, name=f"cfg-{config_id}"))
        next_id += 1

    return ResourceRegistry(
        configs=configs,
        resources=resource_entries,
        resource_name_index=resource_name_index,
        resources_by_config=resources_by_config,
    )


def _make_manager(tmp_path: Path, tasks: list[TaskJob]) -> TaskManager:
    from scheduler import Scheduler
    from task_runner import TaskRunner

    scheduler = Scheduler(max_concurrency=4, max_cpu_percent=95.0,
                          max_memory_percent=95.0, max_disk_active_percent=99.0)
    runner = MagicMock(spec=TaskRunner)
    return TaskManager(
        tasks=tasks,
        scheduler=scheduler,
        runner=runner,
        log_dir=tmp_path / "logs",
        scheduler_tick=1.0,
        status_interval=60.0,
        registered_resources=["resource-x", "resource-y"],
    )


def test_two_tasks_two_resources_run_concurrently(tmp_path):
    t1 = _make_task("task-A", config_id=1, priority=1)
    t2 = _make_task("task-B", config_id=1, priority=2)
    manager = _make_manager(tmp_path, [t1, t2])
    manager._resource_registry = _make_registry([(1, "resource-x"), (1, "resource-y")])

    def fake_start(task_id: str, assigned_resource: str = "") -> None:
        with manager._lock:
            task = manager.tasks[task_id]
            if task.status != TaskStatus.QUEUED:
                return
            task.assigned_resource = assigned_resource
            manager._set_task_status(task, TaskStatus.STARTING)
            manager._resource_lock[assigned_resource] = task_id

    manager._start_task = fake_start  # type: ignore[method-assign]

    with manager._lock:
        manager.host_state = HostState.RUNNING

    manager._try_schedule()

    assert manager.tasks["task-A"].status == TaskStatus.STARTING
    assert manager.tasks["task-B"].status == TaskStatus.STARTING
    assert manager.tasks["task-A"].assigned_resource != manager.tasks["task-B"].assigned_resource
    assert manager._pending_by_config.get(1, []) == []


def test_two_tasks_one_resource_second_goes_pending(tmp_path):
    t1 = _make_task("task-A", config_id=1, priority=1)
    t2 = _make_task("task-B", config_id=1, priority=2)
    manager = _make_manager(tmp_path, [t1, t2])
    manager._resource_registry = _make_registry([(1, "resource-x")])

    def fake_start(task_id: str, assigned_resource: str = "") -> None:
        with manager._lock:
            task = manager.tasks[task_id]
            if task.status != TaskStatus.QUEUED:
                return
            task.assigned_resource = assigned_resource
            manager._set_task_status(task, TaskStatus.STARTING)
            manager._resource_lock[assigned_resource] = task_id

    manager._start_task = fake_start  # type: ignore[method-assign]

    with manager._lock:
        manager.host_state = HostState.RUNNING

    manager._try_schedule()

    assert manager.tasks["task-A"].status == TaskStatus.STARTING
    assert manager.tasks["task-A"].assigned_resource == "resource-x"
    assert manager.tasks["task-B"].status == TaskStatus.PENDING
    assert manager._pending_by_config.get(1, []) == ["task-B"]


def test_release_resource_wakes_pending_task(tmp_path):
    t1 = _make_task("task-A", config_id=1, priority=1)
    t2 = _make_task("task-B", config_id=1, priority=2)
    manager = _make_manager(tmp_path, [t1, t2])
    manager._resource_registry = _make_registry([(1, "resource-x")])

    with manager._lock:
        manager.tasks["task-A"].assigned_resource = "resource-x"
        manager.tasks["task-A"].status = TaskStatus.RUNNING

        manager.tasks["task-B"].status = TaskStatus.PENDING
        manager.tasks["task-B"].blocked_by = "task-A"
        manager._pending_by_config[1] = ["task-B"]

        manager._resource_lock["resource-x"] = "task-A"
        manager._resource_lock.pop("resource-x", None)
        manager._wake_pending_for_released_resource("resource-x")

    assert manager.tasks["task-B"].status == TaskStatus.QUEUED
    assert manager.tasks["task-B"].blocked_by is None
    assert "task-B" in manager.queue


def test_pending_task_aborted_by_force_stop(tmp_path):
    t1 = _make_task("task-A", config_id=1, priority=1)
    manager = _make_manager(tmp_path, [t1])

    with manager._lock:
        manager.host_state = HostState.RUNNING
        manager.tasks["task-A"].status = TaskStatus.PENDING
        manager.tasks["task-A"].blocked_by = "holder"
        manager._pending_by_config[1] = ["task-A"]

    manager.force_stop()

    assert manager.tasks["task-A"].status == TaskStatus.ABORTED
    assert manager.tasks["task-A"].blocked_by is None
    assert manager._pending_by_config == {}


def test_priority_order_in_config_pool(tmp_path):
    t1 = _make_task("task-p1", config_id=1, priority=1)
    t2 = _make_task("task-p3", config_id=1, priority=3)
    t3 = _make_task("task-p2", config_id=1, priority=2)
    manager = _make_manager(tmp_path, [t1, t2, t3])
    manager._resource_registry = _make_registry([(1, "resource-x")])

    with manager._lock:
        manager.tasks["task-p1"].status = TaskStatus.PENDING
        manager.tasks["task-p2"].status = TaskStatus.PENDING
        manager.tasks["task-p3"].status = TaskStatus.PENDING

        manager._insert_pending_by_config_sorted(1, "task-p3")
        manager._insert_pending_by_config_sorted(1, "task-p1")
        manager._insert_pending_by_config_sorted(1, "task-p2")

        manager._wake_pending_for_released_resource("resource-x")

    assert manager.tasks["task-p1"].status == TaskStatus.QUEUED
    assert manager.tasks["task-p2"].status == TaskStatus.PENDING
    assert manager.tasks["task-p3"].status == TaskStatus.PENDING
