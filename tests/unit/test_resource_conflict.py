"""Unit tests for config-pool conflict and pending behavior in TaskManager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from task_manager import HostState, TaskJob, TaskManager, TaskStatus


def _make_task(task_id: str, config_id: int = 1, priority: int = 1) -> TaskJob:
    return TaskJob(task_id=task_id, commands=["echo hi"], resource="", config_id=config_id, priority=priority)


def _make_registry() -> object:
    class _Cfg:
        def __init__(self, cid: int, name: str) -> None:
            self.id = cid
            self.name = name

    class _Res:
        def __init__(self, rid: int, name: str, config_id: int) -> None:
            self.id = rid
            self.name = name
            self.config_id = config_id
            self.properties = {"ip": f"10.0.0.{rid}"}

    class _Registry:
        pass

    reg = _Registry()
    reg.configs = {1: _Cfg(1, "cfg-1")}
    reg.resources = {1: _Res(1, "machine-A", 1), 2: _Res(2, "machine-B", 1)}
    reg.resource_name_index = {"machine-A": 1, "machine-B": 2}
    reg.resources_by_config = {1: [1, 2]}
    return reg


def _make_manager(tmp_path: Path, tasks: list[TaskJob] | None = None) -> TaskManager:
    from scheduler import Scheduler
    from task_runner import TaskRunner

    scheduler = Scheduler(max_concurrency=4, max_cpu_percent=95.0,
                          max_memory_percent=95.0, max_disk_active_percent=99.0)
    runner = MagicMock(spec=TaskRunner)
    manager = TaskManager(
        tasks=tasks or [],
        scheduler=scheduler,
        runner=runner,
        log_dir=tmp_path / "logs",
        scheduler_tick=1.0,
        status_interval=60.0,
        registered_resources=["machine-A", "machine-B"],
        resource_registry=_make_registry(),
    )
    return manager


def test_force_stop_aborts_pending_tasks(tmp_path: Path) -> None:
    pending_task = _make_task("p1", config_id=1, priority=1)
    manager = _make_manager(tmp_path, [pending_task])

    with manager._lock:
        manager.host_state = HostState.RUNNING
        pending_task.status = TaskStatus.PENDING
        pending_task.blocked_by = "holder"
        manager._pending_by_config[1] = ["p1"]
        manager.queue = []

    manager.force_stop()

    assert pending_task.status == TaskStatus.ABORTED
    assert pending_task.abort_reason == "force_stop"
    assert pending_task.blocked_by is None
    assert manager._pending_by_config == {}


def test_draining_converts_pending_to_queued(tmp_path: Path) -> None:
    pending_task = _make_task("p1", config_id=1, priority=1)
    manager = _make_manager(tmp_path, [pending_task])

    with manager._lock:
        manager.host_state = HostState.DRAINING
        pending_task.status = TaskStatus.PENDING
        pending_task.blocked_by = "holder"
        manager._pending_by_config[1] = ["p1"]
        manager.queue = []

    manager._advance_host_state()

    assert pending_task.status == TaskStatus.QUEUED
    assert pending_task.blocked_by is None
    assert "p1" in manager.queue
    assert manager.host_state == HostState.NOT_RUN
    assert manager._pending_by_config == {}


def test_wake_pending_for_released_resource_promotes_config_waiter(tmp_path: Path) -> None:
    t1 = _make_task("t1", config_id=1, priority=1)
    manager = _make_manager(tmp_path, [t1])

    with manager._lock:
        t1.status = TaskStatus.PENDING
        t1.blocked_by = "holder"
        manager._pending_by_config[1] = ["t1"]
        manager.queue = []
        manager._wake_pending_for_released_resource("machine-A")

    assert t1.status == TaskStatus.QUEUED
    assert t1.blocked_by is None
    assert manager.queue == ["t1"]


def test_snapshot_resources_shows_occupancy(tmp_path: Path) -> None:
    manager = _make_manager(tmp_path, [_make_task("t1")])

    with manager._lock:
        manager._resource_lock["machine-A"] = "t1"

    snap = manager.snapshot_resources()
    machine_a = next(item for item in snap["resources"] if item["resource"] == "machine-A")
    assert machine_a["status"] == "occupied"
    assert machine_a["held_by"] == "t1"


def test_snapshot_health_includes_pending_count(tmp_path: Path) -> None:
    task = _make_task("t1")
    manager = _make_manager(tmp_path, [task])

    with manager._lock:
        task.status = TaskStatus.PENDING
        task.blocked_by = "holder"

    health = manager.snapshot_health()
    assert health["pending_count"] == 1
    assert health["queued_count"] == 0
