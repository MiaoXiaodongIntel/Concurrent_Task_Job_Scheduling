"""Unit tests for task state machine transitions.

Covers design_task_manager.md §2.2 Task Transition Policy:
- STARTING -> FAILED by spawn failure (OSError from runner.start_task)
- STARTING -> ABORTED by force-stop command
- SUCCEEDED / FAILED / ABORTED -> QUEUED by rerun command
- Rerun clears all run metadata
- Rerun does not jump ahead of already-queued tasks
- Rerun rejected for non-terminal states (QUEUED, PENDING, RUNNING)
- Rerun rejected for unknown task_id
- Mixed rerun: terminal tasks accepted, non-terminal tasks rejected
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from task_manager import HostState, TaskJob, TaskManager, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(task_id: str = "t1", config_id: int = 1, priority: int = 1) -> TaskJob:
    return TaskJob(task_id=task_id, commands=["echo hi"], config_id=config_id, priority=priority)


def _make_manager(tmp_path: Path, tasks: list[TaskJob] | None = None) -> TaskManager:
    from scheduler import Scheduler
    from task_runner import TaskRunner

    scheduler = Scheduler(
        max_concurrency=4,
        max_cpu_percent=95.0,
        max_memory_percent=95.0,
        max_disk_active_percent=99.0,
    )
    runner = MagicMock(spec=TaskRunner)
    class _Cfg:
        def __init__(self, cid: int) -> None:
            self.id = cid
            self.name = f"cfg-{cid}"

    class _Res:
        def __init__(self, rid: int, name: str, config_id: int) -> None:
            self.id = rid
            self.name = name
            self.config_id = config_id
            self.properties = {}

    class _Registry:
        pass

    reg = _Registry()
    reg.configs = {1: _Cfg(1), 2: _Cfg(2)}
    reg.resources = {1: _Res(1, "machine-A", 1), 2: _Res(2, "machine-B", 2)}
    reg.resource_name_index = {"machine-A": 1, "machine-B": 2}
    reg.resources_by_config = {1: [1], 2: [2]}

    return TaskManager(
        tasks=tasks or [],
        scheduler=scheduler,
        runner=runner,
        log_dir=tmp_path / "logs",
        scheduler_tick=1.0,
        status_interval=60.0,
        registered_resources=["machine-A", "machine-B"],
        resource_registry=reg,
    )


def _inject_terminal_task(manager: TaskManager, task: TaskJob,
                          status: TaskStatus, exit_code: int = 0) -> None:
    """Inject a task directly into a terminal state, bypassing normal scheduling."""
    with manager._lock:
        manager.tasks[task.task_id] = task
        task.status = status
        task.exit_code = exit_code
        task.started_at = "2026-06-15T10:00:00"
        task.ended_at = "2026-06-15T10:00:05"
        task.pid = 1234
        if task.task_id in manager.queue:
            manager.queue.remove(task.task_id)


def _inject_starting_task(manager: TaskManager, task: TaskJob) -> None:
    """Inject a task into STARTING state with the resource lock held."""
    with manager._lock:
        manager.tasks[task.task_id] = task
        task.status = TaskStatus.STARTING
        task.assigned_resource = "machine-A"
        manager._resource_lock[task.assigned_resource] = task.task_id
        if task.task_id in manager.queue:
            manager.queue.remove(task.task_id)


# ---------------------------------------------------------------------------
# STARTING -> FAILED (spawn failure)
# ---------------------------------------------------------------------------

def test_spawn_failure_transitions_starting_to_failed(tmp_path):
    """When runner.start_task raises OSError, task transitions STARTING -> FAILED."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    manager.runner.start_task.side_effect = OSError("command not found")
    with manager._lock:
        manager.host_state = HostState.RUNNING

    manager._try_schedule()

    assert task.status == TaskStatus.FAILED
    assert task.exit_code == -1
    assert task.abort_reason is not None and "spawn_failed" in task.abort_reason
    assert task.ended_at is not None


def test_spawn_failure_releases_resource_lock(tmp_path):
    """Resource lock must be released after spawn failure (design_task_manager.md §2.4)."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    manager.runner.start_task.side_effect = OSError("command not found")
    with manager._lock:
        manager.host_state = HostState.RUNNING

    manager._try_schedule()

    assert task.assigned_resource not in manager._resource_lock


def test_spawn_failure_wakes_pending_task(tmp_path):
    """A pending task blocked on the spawn-failed resource must be promoted to queued."""
    t_fail = _make_task("t-fail", config_id=1, priority=1)
    t_wait = _make_task("t-wait", config_id=1, priority=2)
    manager = _make_manager(tmp_path, [t_fail])
    manager.runner.start_task.side_effect = OSError("command not found")

    # Manually inject the pending task (simulating it arrived while t-fail held the lock).
    with manager._lock:
        manager.tasks["t-wait"] = t_wait
        t_wait.status = TaskStatus.PENDING
        t_wait.blocked_by = "t-fail"
        manager._insert_pending_by_config_sorted(1, "t-wait")
        manager.host_state = HostState.RUNNING

    manager._try_schedule()

    assert t_fail.status == TaskStatus.FAILED
    assert t_wait.status == TaskStatus.QUEUED
    assert t_wait.blocked_by is None
    assert "t-wait" in manager.queue


# ---------------------------------------------------------------------------
# STARTING -> ABORTED (force-stop command)
# ---------------------------------------------------------------------------

def test_starting_task_aborted_by_force_stop(tmp_path):
    """force_stop() transitions a STARTING task to ABORTED."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    _inject_starting_task(manager, task)
    with manager._lock:
        manager.host_state = HostState.RUNNING

    manager.force_stop()

    assert task.status == TaskStatus.ABORTED
    assert task.abort_reason == "force_stop_during_starting"
    assert task.ended_at is not None


def test_starting_task_abort_clears_resource_lock(tmp_path):
    """force_stop() releases the resource lock held by the STARTING task."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    _inject_starting_task(manager, task)
    with manager._lock:
        manager.host_state = HostState.RUNNING

    manager.force_stop()

    assert manager._resource_lock == {}


# ---------------------------------------------------------------------------
# SUCCEEDED -> QUEUED (rerun command)
# ---------------------------------------------------------------------------

def test_rerun_succeeded_task_transitions_to_queued(tmp_path):
    """rerun() on a SUCCEEDED task transitions it to QUEUED and returns it as accepted."""
    task = _make_task()
    manager = _make_manager(tmp_path)
    _inject_terminal_task(manager, task, TaskStatus.SUCCEEDED, exit_code=0)

    accepted, rejected = manager.rerun(["t1"])

    assert "t1" in accepted
    assert rejected == []
    assert task.status == TaskStatus.QUEUED


def test_rerun_succeeded_clears_run_metadata(tmp_path):
    """rerun() clears started_at, ended_at, exit_code, pid, and abort_reason."""
    task = _make_task()
    manager = _make_manager(tmp_path)
    _inject_terminal_task(manager, task, TaskStatus.SUCCEEDED, exit_code=0)

    manager.rerun(["t1"])

    assert task.started_at is None
    assert task.ended_at is None
    assert task.exit_code is None
    assert task.pid is None
    assert task.abort_reason is None


def test_rerun_succeeded_task_appears_in_queue(tmp_path):
    """rerun() adds the task back into the manager queue."""
    task = _make_task()
    manager = _make_manager(tmp_path)
    _inject_terminal_task(manager, task, TaskStatus.SUCCEEDED)

    manager.rerun(["t1"])

    assert "t1" in manager.queue


# ---------------------------------------------------------------------------
# FAILED -> QUEUED (rerun command)
# ---------------------------------------------------------------------------

def test_rerun_failed_task_transitions_to_queued(tmp_path):
    """rerun() on a FAILED task transitions it to QUEUED."""
    task = _make_task()
    manager = _make_manager(tmp_path)
    _inject_terminal_task(manager, task, TaskStatus.FAILED, exit_code=1)

    accepted, rejected = manager.rerun(["t1"])

    assert "t1" in accepted
    assert rejected == []
    assert task.status == TaskStatus.QUEUED


# ---------------------------------------------------------------------------
# ABORTED -> QUEUED (rerun command)
# ---------------------------------------------------------------------------

def test_rerun_aborted_task_transitions_to_queued(tmp_path):
    """rerun() on an ABORTED task transitions it to QUEUED and clears abort_reason."""
    task = _make_task()
    manager = _make_manager(tmp_path)
    _inject_terminal_task(manager, task, TaskStatus.ABORTED)
    with manager._lock:
        task.abort_reason = "user_abort"

    accepted, rejected = manager.rerun(["t1"])

    assert "t1" in accepted
    assert task.status == TaskStatus.QUEUED
    assert task.abort_reason is None


# ---------------------------------------------------------------------------
# Rerun does not jump ahead of already-queued tasks
# ---------------------------------------------------------------------------

def test_rerun_appended_to_tail_behind_waiting_queued_task(tmp_path):
    """Rerun tasks are placed after tasks already waiting in the queue."""
    t_waiting = _make_task("t-wait", priority=1)
    t_done = _make_task("t-done", priority=1)
    manager = _make_manager(tmp_path, [t_waiting])
    _inject_terminal_task(manager, t_done, TaskStatus.SUCCEEDED)

    manager.rerun(["t-done"])

    assert manager.queue.index("t-wait") < manager.queue.index("t-done")


# ---------------------------------------------------------------------------
# Rerun rejected for non-terminal states
# ---------------------------------------------------------------------------

def test_rerun_rejected_for_queued_task(tmp_path):
    """rerun() rejected for a task in QUEUED state."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])

    accepted, rejected = manager.rerun(["t1"])

    assert "t1" in rejected
    assert accepted == []
    assert task.status == TaskStatus.QUEUED


def test_rerun_rejected_for_running_task(tmp_path):
    """rerun() rejected for a task in RUNNING state."""
    task = _make_task()
    manager = _make_manager(tmp_path)
    proc = MagicMock()
    proc.pid = 999
    with manager._lock:
        manager.tasks["t1"] = task
        task.status = TaskStatus.RUNNING
        manager._running_handles["t1"] = SimpleNamespace(  # type: ignore[assignment]
            process=proc, script_path=Path("x.ps1")
        )

    accepted, rejected = manager.rerun(["t1"])

    assert "t1" in rejected
    assert task.status == TaskStatus.RUNNING


def test_rerun_rejected_for_unknown_task_id(tmp_path):
    """rerun() on a non-existent task_id puts it in the rejected list."""
    manager = _make_manager(tmp_path)

    accepted, rejected = manager.rerun(["no-such-task"])

    assert "no-such-task" in rejected
    assert accepted == []


def test_rerun_partial_accept_and_reject(tmp_path):
    """rerun() with mixed list: terminal tasks accepted, non-terminal tasks rejected."""
    t_done = _make_task("t-done", config_id=1)
    t_running = _make_task("t-running", config_id=2)
    manager = _make_manager(tmp_path)
    _inject_terminal_task(manager, t_done, TaskStatus.SUCCEEDED)
    proc = MagicMock()
    proc.pid = 999
    with manager._lock:
        manager.tasks["t-running"] = t_running
        t_running.status = TaskStatus.RUNNING
        manager._running_handles["t-running"] = SimpleNamespace(  # type: ignore[assignment]
            process=proc, script_path=Path("x.ps1")
        )

    accepted, rejected = manager.rerun(["t-done", "t-running"])

    assert "t-done" in accepted
    assert "t-running" in rejected
