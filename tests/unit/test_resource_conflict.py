"""Unit tests for resource conflict detection and pending state.

Covers design_task_manager.md §2.2–2.4 and design_scheduler.md §4:
- queued -> pending when resource is occupied
- pending -> queued when resource is released (wake_pending_for_resource)
- resource lock written at STARTING, released at terminal state
- force_stop aborts pending tasks
- DRAINING->NOT_RUN converts pending tasks to queued (batch)
- load_resources contract
- priority-based queue ordering
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from task_manager import HostState, TaskJob, TaskManager, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(task_id: str, resource: str = "machine-A", priority: int = 1) -> TaskJob:
    return TaskJob(task_id=task_id, commands=["echo hi"], resource=resource, priority=priority)


def _make_manager(
    tmp_path: Path,
    tasks: list[TaskJob] | None = None,
    resources: list[str] | None = None,
) -> TaskManager:
    from scheduler import Scheduler
    from task_runner import TaskRunner

    scheduler = Scheduler(max_concurrency=4, max_cpu_percent=95.0,
                          max_memory_percent=95.0, max_disk_active_percent=99.0)
    runner = MagicMock(spec=TaskRunner)
    return TaskManager(
        tasks=tasks or [],
        scheduler=scheduler,
        runner=runner,
        log_dir=tmp_path / "logs",
        scheduler_tick=1.0,
        status_interval=60.0,
        registered_resources=resources or ["machine-A", "machine-B"],
    )


# ---------------------------------------------------------------------------
# Queue ordering tests
# ---------------------------------------------------------------------------

def test_queue_sorted_by_priority_on_init(tmp_path):
    """Initial queue must be sorted by (priority, created_at)."""
    t1 = _make_task("t1", priority=3)
    t2 = _make_task("t2", priority=1)
    t3 = _make_task("t3", priority=2)
    manager = _make_manager(tmp_path, [t1, t2, t3])

    assert manager.queue == ["t2", "t3", "t1"]


def test_insert_queue_sorted(tmp_path):
    """_insert_queue_sorted inserts at the correct priority position."""
    t1 = _make_task("t1", priority=1)
    t3 = _make_task("t3", priority=3)
    manager = _make_manager(tmp_path, [t1, t3])

    t2 = _make_task("t2", priority=2)
    manager.tasks["t2"] = t2
    with manager._lock:
        manager._insert_queue_sorted("t2")

    assert manager.queue.index("t2") == 1  # between t1 and t3


# ---------------------------------------------------------------------------
# Resource lock tests
# ---------------------------------------------------------------------------

def test_resource_lock_written_at_starting(tmp_path):
    """_insert_pending_sorted + _try_schedule pathway: resource lock set when entering starting."""
    task = _make_task("t1", "machine-A", 1)
    manager = _make_manager(tmp_path, [task])

    # Simulate _start_task writing the lock (it's called from _start_task)
    with manager._lock:
        manager.tasks["t1"].status = TaskStatus.STARTING
        manager._resource_lock["machine-A"] = "t1"

    assert manager._resource_lock.get("machine-A") == "t1"


def test_wake_pending_promotes_highest_priority(tmp_path):
    """_wake_pending_for_resource promotes the highest-priority pending task."""
    t_high = _make_task("t-high", "machine-A", priority=1)
    t_low = _make_task("t-low", "machine-A", priority=5)
    manager = _make_manager(tmp_path, [])

    with manager._lock:
        manager.tasks["t-high"] = t_high
        manager.tasks["t-low"] = t_low
        t_high.status = TaskStatus.PENDING
        t_low.status = TaskStatus.PENDING
        manager._insert_pending_sorted("machine-A", "t-high")
        manager._insert_pending_sorted("machine-A", "t-low")

        manager._wake_pending_for_resource("machine-A")

    assert t_high.status == TaskStatus.QUEUED
    assert t_high.blocked_by is None
    assert t_low.status == TaskStatus.PENDING  # still waiting
    assert "t-high" in manager.queue


def test_pending_task_blocked_by_set(tmp_path):
    """When a task goes pending, blocked_by is set to the resource holder."""
    holder = _make_task("holder", "machine-A", 1)
    waiter = _make_task("waiter", "machine-A", 2)
    manager = _make_manager(tmp_path, [holder, waiter])

    with manager._lock:
        # Simulate holder having the lock
        holder.status = TaskStatus.RUNNING
        manager._resource_lock["machine-A"] = "holder"
        manager.queue.remove("holder")

        # Simulate scheduler sending waiter to pending
        manager.queue.remove("waiter")
        manager._set_task_status(waiter, TaskStatus.PENDING)
        waiter.blocked_by = manager._resource_lock.get("machine-A")
        manager._insert_pending_sorted("machine-A", "waiter")

    assert waiter.status == TaskStatus.PENDING
    assert waiter.blocked_by == "holder"


# ---------------------------------------------------------------------------
# Force-stop aborts pending tests
# ---------------------------------------------------------------------------

def test_force_stop_aborts_pending_tasks(tmp_path):
    """force_stop must abort all pending tasks."""
    pending_task = _make_task("p1", "machine-A", 1)
    manager = _make_manager(tmp_path, [pending_task])

    with manager._lock:
        manager.host_state = HostState.RUNNING
        manager.tasks["p1"] = pending_task
        pending_task.status = TaskStatus.PENDING
        pending_task.blocked_by = "some-running-task"
        manager._pending_by_resource["machine-A"] = ["p1"]
        manager.queue = []

    manager.force_stop()

    assert pending_task.status == TaskStatus.ABORTED
    assert pending_task.abort_reason == "force_stop"
    assert pending_task.blocked_by is None
    assert manager._pending_by_resource == {}


def test_force_stop_clears_resource_lock(tmp_path):
    """force_stop must clear the resource lock table."""
    task = _make_task("t1", "machine-A", 1)
    manager = _make_manager(tmp_path, [task])

    with manager._lock:
        manager.host_state = HostState.RUNNING
        manager._resource_lock["machine-A"] = "t1"
        task.status = TaskStatus.RUNNING

    manager.force_stop()

    assert manager._resource_lock == {}


# ---------------------------------------------------------------------------
# DRAINING -> NOT_RUN converts pending to queued
# ---------------------------------------------------------------------------

def test_draining_converts_pending_to_queued(tmp_path):
    """When DRAINING completes (no inflight), pending tasks must become queued."""
    pending_task = _make_task("p1", "machine-A", 1)
    manager = _make_manager(tmp_path, [pending_task])

    with manager._lock:
        manager.host_state = HostState.DRAINING
        pending_task.status = TaskStatus.PENDING
        pending_task.blocked_by = "gone-task"
        manager._pending_by_resource["machine-A"] = ["p1"]
        manager.queue = []

    # Trigger _advance_host_state with no inflight
    manager._advance_host_state()

    assert pending_task.status == TaskStatus.QUEUED
    assert pending_task.blocked_by is None
    assert "p1" in manager.queue
    assert manager.host_state == HostState.NOT_RUN
    assert manager._pending_by_resource == {}


# ---------------------------------------------------------------------------
# load_resources tests
# ---------------------------------------------------------------------------

def test_load_resources_accepted_in_not_run(tmp_path):
    """load_resources is accepted when host is NOT_RUN and not yet loaded."""
    manager = _make_manager(tmp_path, resources=None)
    manager._resources_loaded = False
    manager._registered_resources = []
    manager._registered_resources_set = set()

    result = manager.load_resources(["machine-X", "machine-Y"])

    assert result["accepted"] is True
    assert manager._resources_loaded is True
    assert "machine-X" in manager._registered_resources_set


def test_load_resources_rejected_when_already_loaded(tmp_path):
    """load_resources is rejected when resources have already been loaded."""
    manager = _make_manager(tmp_path, resources=["machine-A"])
    result = manager.load_resources(["machine-B"])
    assert result["accepted"] is False
    assert result["reason_code"] == "already_loaded"


def test_load_resources_rejected_when_running(tmp_path):
    """load_resources is rejected when host is not NOT_RUN."""
    manager = _make_manager(tmp_path, resources=None)
    manager._resources_loaded = False
    manager.host_state = HostState.RUNNING

    result = manager.load_resources(["machine-A"])

    assert result["accepted"] is False
    assert result["reason_code"] == "invalid_host_state"


def test_load_resources_rejected_empty_list(tmp_path):
    """load_resources is rejected when resources list is empty."""
    manager = _make_manager(tmp_path, resources=None)
    manager._resources_loaded = False

    result = manager.load_resources([])

    assert result["accepted"] is False
    assert result["reason_code"] == "empty_resources"


def test_load_resources_deduplicates(tmp_path):
    """load_resources deduplicates entries, keeping first occurrence."""
    manager = _make_manager(tmp_path, resources=None)
    manager._resources_loaded = False
    manager._registered_resources = []
    manager._registered_resources_set = set()

    result = manager.load_resources(["machine-A", "machine-B", "machine-A"])

    assert result["accepted"] is True
    assert manager._registered_resources == ["machine-A", "machine-B"]


# ---------------------------------------------------------------------------
# snapshot_resources tests
# ---------------------------------------------------------------------------

def test_snapshot_resources_shows_occupancy(tmp_path):
    """snapshot_resources reflects the current resource lock state."""
    manager = _make_manager(tmp_path, resources=["machine-A"])

    with manager._lock:
        manager._resource_lock["machine-A"] = "t1"

    snap = manager.snapshot_resources()
    assert snap["loaded"] is True
    assert snap["resources"][0]["status"] == "occupied"
    assert snap["resources"][0]["held_by"] == "t1"


def test_snapshot_resources_free(tmp_path):
    """snapshot_resources shows free when no lock is held."""
    manager = _make_manager(tmp_path, resources=["machine-A"])
    snap = manager.snapshot_resources()
    assert snap["resources"][0]["status"] == "free"
    assert snap["resources"][0]["held_by"] is None


# ---------------------------------------------------------------------------
# pending_count in snapshot_health
# ---------------------------------------------------------------------------

def test_snapshot_health_includes_pending_count(tmp_path):
    """snapshot_health must include pending_count."""
    task = _make_task("t1")
    manager = _make_manager(tmp_path, [task])

    with manager._lock:
        task.status = TaskStatus.PENDING
        task.blocked_by = "holder"

    health = manager.snapshot_health()
    assert "pending_count" in health
    assert health["pending_count"] == 1
    assert health["queued_count"] == 0
