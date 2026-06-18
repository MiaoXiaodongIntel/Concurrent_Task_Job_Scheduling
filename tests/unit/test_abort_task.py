"""Unit tests for TaskManager.abort_task (per-task user abort).

Covers design_control_plane.md §3.6 and design_task_manager.md §2.2:
- abort a running task → accepted, status=aborted, abort_reason=user_abort
- abort a pending task → accepted, status=aborted, no process to terminate
- abort a non-running/non-pending task → rejected with task_not_abortable
- abort a non-existent task → rejected with task_not_found
- subprocess terminate() is called on the running process
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from task_manager import HostState, TaskJob, TaskManager, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(task_id: str = "t1", config_id: int = 1, priority: int = 1) -> TaskJob:
    return TaskJob(task_id=task_id, commands=["echo hi"], config_id=config_id, priority=priority)


def _make_manager(tmp_path: Path, tasks: list[TaskJob] | None = None,
                  resources: list[str] | None = None) -> TaskManager:
    """Return a minimal TaskManager with no real scheduler/runner activity."""
    from scheduler import Scheduler
    from task_runner import TaskRunner

    scheduler = Scheduler(
        max_concurrency=2,
        max_cpu_percent=90.0,
        max_memory_percent=90.0,
        max_disk_active_percent=95.0,
    )
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


def _fake_handle(pid: int = 9999) -> SimpleNamespace:
    """Minimal fake RunningTaskHandle with a mock process."""
    proc = MagicMock()
    proc.pid = pid
    handle = SimpleNamespace(process=proc, script_path=Path("fake.ps1"))
    return handle


def _add_running_task(manager: TaskManager, task: TaskJob) -> SimpleNamespace:
    """Inject a task into RUNNING state with a fake handle."""
    with manager._lock:
        manager.tasks[task.task_id] = task
        task.status = TaskStatus.RUNNING
        handle = _fake_handle()
        manager._running_handles[task.task_id] = handle  # type: ignore[assignment]
    return handle


def _add_pending_task(manager: TaskManager, task: TaskJob, blocked_by: str = "other-task") -> None:
    """Inject a task into PENDING state."""
    with manager._lock:
        manager.tasks[task.task_id] = task
        task.status = TaskStatus.PENDING
        task.blocked_by = blocked_by
        pending_list = manager._pending_by_config.setdefault(task.config_id, [])
        pending_list.append(task.task_id)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_abort_running_task_accepted(tmp_path):
    """abort_task on a RUNNING task is accepted and transitions to aborted."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    handle = _add_running_task(manager, task)

    result = manager.abort_task("t1")

    assert result["accepted"] is True
    assert result["reason_code"] == "accepted"
    assert task.status == TaskStatus.ABORTED
    assert task.abort_reason == "user_abort"
    assert task.ended_at is not None


def test_abort_running_task_calls_terminate(tmp_path):
    """abort_task must call process.terminate() on the subprocess."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    handle = _add_running_task(manager, task)

    manager.abort_task("t1")

    handle.process.terminate.assert_called_once()


def test_abort_pending_task_accepted(tmp_path):
    """abort_task on a PENDING task is accepted, transitions to aborted, removed from pending index."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    _add_pending_task(manager, task)

    result = manager.abort_task("t1")

    assert result["accepted"] is True
    assert result["reason_code"] == "accepted"
    assert task.status == TaskStatus.ABORTED
    assert task.abort_reason == "user_abort"
    assert task.blocked_by is None
    assert task.task_id not in manager._pending_by_config.get(1, [])


def test_abort_pending_task_no_process_terminate(tmp_path):
    """abort_task on PENDING must NOT call process.terminate() (no process exists)."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    _add_pending_task(manager, task)

    # Ensure no running handle exists for this task.
    with manager._lock:
        assert task.task_id not in manager._running_handles

    result = manager.abort_task("t1")
    assert result["accepted"] is True


def test_abort_non_running_task_rejected(tmp_path):
    """abort_task on a QUEUED task is rejected with task_not_abortable."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    # task stays in QUEUED (default)

    result = manager.abort_task("t1")

    assert result["accepted"] is False
    assert result["reason_code"] == "task_not_abortable"
    assert task.status == TaskStatus.QUEUED  # unchanged


def test_abort_nonexistent_task_rejected(tmp_path):
    """abort_task on an unknown task_id is rejected with task_not_found."""
    manager = _make_manager(tmp_path, [])

    result = manager.abort_task("no-such-task")

    assert result["accepted"] is False
    assert result["reason_code"] == "task_not_found"


def test_abort_task_does_not_change_host_state(tmp_path):
    """abort_task must not affect host state."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    manager.host_state = HostState.RUNNING
    _add_running_task(manager, task)

    manager.abort_task("t1")

    assert manager.host_state == HostState.RUNNING


def test_abort_task_sibling_unaffected(tmp_path):
    """Aborting one task must not change the status of other tasks."""
    t1 = _make_task("t1", 1, 1)
    t2 = _make_task("t2", 2, 2)
    manager = _make_manager(tmp_path, [t1, t2])
    _add_running_task(manager, t1)
    # t2 stays QUEUED

    manager.abort_task("t1")

    assert t2.status == TaskStatus.QUEUED


def test_abort_aborted_task_rejected(tmp_path):
    """abort_task on an already-aborted task is rejected with task_not_abortable."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    with manager._lock:
        manager.tasks["t1"] = task
        task.status = TaskStatus.ABORTED

    result = manager.abort_task("t1")

    assert result["accepted"] is False
    assert result["reason_code"] == "task_not_abortable"


def test_control_abort_task_dispatch(tmp_path):
    """control('abort_task') dispatches correctly to abort_task()."""
    task = _make_task()
    manager = _make_manager(tmp_path, [task])
    _add_running_task(manager, task)

    result = manager.control("abort_task", task_ids=["t1"])

    assert result["accepted"] is True
    assert result["command"] == "abort_task"
    assert "t1" in result["affected_task_ids"]


def test_control_abort_task_no_ids(tmp_path):
    """control('abort_task') with empty task_ids returns task_not_found."""
    manager = _make_manager(tmp_path, [])

    result = manager.control("abort_task", task_ids=[])

    assert result["accepted"] is False
    assert result["reason_code"] == "task_not_found"

    result = manager.control("abort_task", task_ids=[])

    assert result["accepted"] is False
    assert result["reason_code"] == "task_not_found"
