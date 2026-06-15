"""Unit tests for HOST state machine transitions.

Covers design_task_manager.md §3 Host Lifecycle Model:
- NOT_RUN -> RUNNING by start command
- RUNNING -> DRAINING by graceful-stop command
- RUNNING -> STOPPING_FORCE by force-stop command
- DRAINING -> STOPPING_FORCE by force-stop command (escalate)
- STOPPING_FORCE -> NOT_RUN automatically when no in-flight task remains
- NOT_RUN -> SHUTTING_DOWN by shutdown command
- Invalid transitions are rejected
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

def _make_manager(tmp_path: Path) -> TaskManager:
    from scheduler import Scheduler
    from task_runner import TaskRunner

    scheduler = Scheduler(
        max_concurrency=4,
        max_cpu_percent=95.0,
        max_memory_percent=95.0,
        max_disk_active_percent=99.0,
    )
    runner = MagicMock(spec=TaskRunner)
    return TaskManager(
        tasks=[],
        scheduler=scheduler,
        runner=runner,
        log_dir=tmp_path / "logs",
        scheduler_tick=1.0,
        status_interval=60.0,
        registered_resources=["machine-A"],
    )


def _inject_running_task(manager: TaskManager, task_id: str = "t1") -> None:
    """Inject a fake RUNNING task so _inflight_count() > 0."""
    task = TaskJob(task_id=task_id, commands=["echo hi"], resource="machine-A", priority=1)
    proc = MagicMock()
    proc.pid = 9999
    with manager._lock:
        manager.tasks[task_id] = task
        task.status = TaskStatus.RUNNING
        manager._running_handles[task_id] = SimpleNamespace(  # type: ignore[assignment]
            process=proc, script_path=Path("fake.ps1")
        )


# ---------------------------------------------------------------------------
# NOT_RUN -> RUNNING
# ---------------------------------------------------------------------------

def test_start_transitions_not_run_to_running(tmp_path):
    """start() accepted in NOT_RUN -> host transitions to RUNNING."""
    manager = _make_manager(tmp_path)
    assert manager.host_state == HostState.NOT_RUN

    result = manager.start()

    assert result is True
    assert manager.host_state == HostState.RUNNING


def test_start_rejected_when_already_running(tmp_path):
    """start() rejected when host is already RUNNING."""
    manager = _make_manager(tmp_path)
    manager.start()

    result = manager.start()

    assert result is False
    assert manager.host_state == HostState.RUNNING


def test_start_rejected_after_shutdown_requested(tmp_path):
    """start() rejected once shutdown has been requested."""
    manager = _make_manager(tmp_path)
    manager.shutdown()

    result = manager.start()

    assert result is False


# ---------------------------------------------------------------------------
# RUNNING -> DRAINING
# ---------------------------------------------------------------------------

def test_graceful_stop_transitions_running_to_draining(tmp_path):
    """graceful_stop() accepted in RUNNING -> host transitions to DRAINING."""
    manager = _make_manager(tmp_path)
    manager.start()

    result = manager.graceful_stop()

    assert result is True
    assert manager.host_state == HostState.DRAINING


def test_graceful_stop_rejected_when_not_running(tmp_path):
    """graceful_stop() rejected when host is NOT_RUN."""
    manager = _make_manager(tmp_path)

    result = manager.graceful_stop()

    assert result is False
    assert manager.host_state == HostState.NOT_RUN


def test_graceful_stop_rejected_when_already_draining(tmp_path):
    """graceful_stop() rejected when host is already DRAINING."""
    manager = _make_manager(tmp_path)
    manager.start()
    manager.graceful_stop()

    result = manager.graceful_stop()

    assert result is False
    assert manager.host_state == HostState.DRAINING


# ---------------------------------------------------------------------------
# RUNNING -> STOPPING_FORCE
# ---------------------------------------------------------------------------

def test_force_stop_transitions_running_to_stopping_force(tmp_path):
    """force_stop() accepted in RUNNING -> host transitions to STOPPING_FORCE."""
    manager = _make_manager(tmp_path)
    manager.start()

    result = manager.force_stop()

    assert result is True
    assert manager.host_state == HostState.STOPPING_FORCE


def test_force_stop_rejected_when_not_running(tmp_path):
    """force_stop() rejected when host is NOT_RUN."""
    manager = _make_manager(tmp_path)

    result = manager.force_stop()

    assert result is False
    assert manager.host_state == HostState.NOT_RUN


def test_force_stop_rejected_when_already_stopping_force(tmp_path):
    """force_stop() rejected when host is already STOPPING_FORCE."""
    manager = _make_manager(tmp_path)
    manager.start()
    manager.force_stop()

    result = manager.force_stop()

    assert result is False
    assert manager.host_state == HostState.STOPPING_FORCE


# ---------------------------------------------------------------------------
# DRAINING -> STOPPING_FORCE (escalate)
# ---------------------------------------------------------------------------

def test_force_stop_escalates_draining_to_stopping_force(tmp_path):
    """force_stop() accepted in DRAINING -> host transitions to STOPPING_FORCE."""
    manager = _make_manager(tmp_path)
    manager.start()
    manager.graceful_stop()
    assert manager.host_state == HostState.DRAINING

    result = manager.force_stop()

    assert result is True
    assert manager.host_state == HostState.STOPPING_FORCE


# ---------------------------------------------------------------------------
# STOPPING_FORCE -> NOT_RUN (automatic)
# ---------------------------------------------------------------------------

def test_stopping_force_auto_transitions_to_not_run_when_no_inflight(tmp_path):
    """_advance_host_state transitions STOPPING_FORCE -> NOT_RUN when inflight == 0."""
    manager = _make_manager(tmp_path)
    manager.start()
    manager.force_stop()
    assert manager.host_state == HostState.STOPPING_FORCE

    manager._advance_host_state()

    assert manager.host_state == HostState.NOT_RUN


def test_stopping_force_stays_while_inflight_tasks_exist(tmp_path):
    """STOPPING_FORCE is not left while a RUNNING task handle still exists."""
    manager = _make_manager(tmp_path)
    manager.start()
    manager.force_stop()
    _inject_running_task(manager, "t1")

    manager._advance_host_state()

    assert manager.host_state == HostState.STOPPING_FORCE


# ---------------------------------------------------------------------------
# NOT_RUN -> SHUTTING_DOWN
# ---------------------------------------------------------------------------

def test_shutdown_transitions_not_run_to_shutting_down(tmp_path):
    """shutdown() accepted in NOT_RUN -> host transitions to SHUTTING_DOWN."""
    manager = _make_manager(tmp_path)

    ok, reason = manager.shutdown()

    assert ok is True
    assert reason == "accepted"
    assert manager.host_state == HostState.SHUTTING_DOWN


def test_shutdown_rejected_when_running(tmp_path):
    """shutdown() rejected when host is RUNNING (must stop first)."""
    manager = _make_manager(tmp_path)
    manager.start()

    ok, reason = manager.shutdown()

    assert ok is False
    assert reason == "host_not_in_not_run"
    assert manager.host_state == HostState.RUNNING


def test_shutdown_rejected_when_already_requested(tmp_path):
    """shutdown() rejected if already requested."""
    manager = _make_manager(tmp_path)
    manager.shutdown()

    ok, reason = manager.shutdown()

    assert ok is False
    assert reason == "shutdown_already_requested"
