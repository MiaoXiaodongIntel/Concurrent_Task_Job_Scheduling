"""Unit tests for {ARTIFACT_DIR} placeholder expansion and artifact directory creation.

Covers design_task_manager.md §4.4 Per-Run Log and Artifact Paths:

- Case A: commands contain {ARTIFACT_DIR} + artifact_base_dir configured
    → artifact directory is created, placeholder is expanded in runner call,
      task.artifact_dir is set to the resolved path.
- Case B: commands do NOT contain {ARTIFACT_DIR} + artifact_base_dir configured
    → no artifact directory is created, commands passed to runner are unchanged,
      task.artifact_dir is None.
- Case C: commands contain {ARTIFACT_DIR} + artifact_base_dir is None
    → no artifact directory is created, placeholder is NOT expanded in runner call,
      task.artifact_dir is None.
- Case D: rerun increments run_index; the next run creates run_1/ instead of run_0/.
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

def _make_task(
    task_id: str = "t1",
    commands: list[str] | None = None,
    resource: str = "machine-A",
    priority: int = 1,
) -> TaskJob:
    return TaskJob(
        task_id=task_id,
        commands=commands or ["echo hi"],
        resource=resource,
        priority=priority,
    )


def _make_manager(
    tmp_path: Path,
    tasks: list[TaskJob] | None = None,
    artifact_base_dir: Path | None = None,
) -> TaskManager:
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
        tasks=tasks or [],
        scheduler=scheduler,
        runner=runner,
        log_dir=tmp_path / "logs",
        artifact_base_dir=artifact_base_dir,
        scheduler_tick=1.0,
        status_interval=60.0,
        registered_resources=["machine-A"],
    )


def _fake_handle(tmp_path: Path, pid: int = 1234) -> SimpleNamespace:
    """Minimal fake RunningTaskHandle whose process exits immediately."""
    proc = MagicMock()
    proc.pid = pid
    proc.stdout = MagicMock()
    proc.stdout.readline.return_value = ""   # causes stream reader to stop immediately
    proc.stderr = MagicMock()
    proc.stderr.readline.return_value = ""
    proc.wait.return_value = 0
    return SimpleNamespace(process=proc, script_path=tmp_path / "fake.ps1")


def _schedule_one(manager: TaskManager, handle: SimpleNamespace) -> None:
    """Set host RUNNING, attach mock handle, and trigger one scheduling tick."""
    manager.runner.start_task.return_value = handle
    with manager._lock:
        manager.host_state = HostState.RUNNING
    manager._try_schedule()


# ---------------------------------------------------------------------------
# Case A: {ARTIFACT_DIR} present + artifact_base_dir configured
# ---------------------------------------------------------------------------

def test_artifact_dir_created_when_placeholder_present(tmp_path):
    """{ARTIFACT_DIR} in command + base dir set → directory created, task.artifact_dir set."""
    artifact_base = tmp_path / "arts"
    task = _make_task(commands=["tool --out {ARTIFACT_DIR}"])
    manager = _make_manager(tmp_path, [task], artifact_base_dir=artifact_base)
    _schedule_one(manager, _fake_handle(tmp_path))

    expected = artifact_base / "t1" / "run_0"
    assert task.artifact_dir == str(expected)
    assert expected.exists()


def test_placeholder_expanded_in_command_passed_to_runner(tmp_path):
    """{ARTIFACT_DIR} in command + base dir set → runner receives expanded command."""
    artifact_base = tmp_path / "arts"
    task = _make_task(commands=["tool --out {ARTIFACT_DIR}"])
    manager = _make_manager(tmp_path, [task], artifact_base_dir=artifact_base)
    _schedule_one(manager, _fake_handle(tmp_path))

    called_commands: list[str] = manager.runner.start_task.call_args[0][0]
    expected_dir = str(artifact_base / "t1" / "run_0")
    assert "{ARTIFACT_DIR}" not in called_commands[0]
    assert expected_dir in called_commands[0]


# ---------------------------------------------------------------------------
# Case B: no {ARTIFACT_DIR} in commands + artifact_base_dir configured
# ---------------------------------------------------------------------------

def test_artifact_dir_not_created_when_no_placeholder(tmp_path):
    """No {ARTIFACT_DIR} in commands → no artifact directory, task.artifact_dir is None."""
    artifact_base = tmp_path / "arts"
    task = _make_task(commands=["echo hello"])
    manager = _make_manager(tmp_path, [task], artifact_base_dir=artifact_base)
    _schedule_one(manager, _fake_handle(tmp_path))

    assert task.artifact_dir is None
    assert not artifact_base.exists()


def test_commands_unchanged_when_no_placeholder(tmp_path):
    """No {ARTIFACT_DIR} in commands → runner receives original command unchanged."""
    artifact_base = tmp_path / "arts"
    original_cmd = "echo hello"
    task = _make_task(commands=[original_cmd])
    manager = _make_manager(tmp_path, [task], artifact_base_dir=artifact_base)
    _schedule_one(manager, _fake_handle(tmp_path))

    called_commands: list[str] = manager.runner.start_task.call_args[0][0]
    assert called_commands[0] == original_cmd


# ---------------------------------------------------------------------------
# Case C: {ARTIFACT_DIR} present + artifact_base_dir is None
# ---------------------------------------------------------------------------

def test_artifact_dir_not_created_when_base_dir_is_none(tmp_path):
    """{ARTIFACT_DIR} in command but base dir is None → no directory, task.artifact_dir is None."""
    task = _make_task(commands=["tool --out {ARTIFACT_DIR}"])
    manager = _make_manager(tmp_path, [task], artifact_base_dir=None)
    _schedule_one(manager, _fake_handle(tmp_path))

    assert task.artifact_dir is None


def test_placeholder_not_expanded_when_base_dir_is_none(tmp_path):
    """{ARTIFACT_DIR} in command but base dir is None → runner receives unexpanded command."""
    task = _make_task(commands=["tool --out {ARTIFACT_DIR}"])
    manager = _make_manager(tmp_path, [task], artifact_base_dir=None)
    _schedule_one(manager, _fake_handle(tmp_path))

    called_commands: list[str] = manager.runner.start_task.call_args[0][0]
    assert "{ARTIFACT_DIR}" in called_commands[0]


# ---------------------------------------------------------------------------
# Case D: rerun increments run_index → next run uses run_1/ not run_0/
# ---------------------------------------------------------------------------

def test_artifact_dir_uses_run_index_after_rerun(tmp_path):
    """After rerun, run_index is 1; artifact dir is run_1/, not run_0/."""
    artifact_base = tmp_path / "arts"
    task = _make_task(commands=["tool --out {ARTIFACT_DIR}"])
    manager = _make_manager(tmp_path, [task], artifact_base_dir=artifact_base)

    # Simulate first run completing (inject terminal state directly).
    with manager._lock:
        task.status = TaskStatus.SUCCEEDED
        task.exit_code = 0
        task.started_at = "2026-06-16T10:00:00+08:00"
        task.ended_at = "2026-06-16T10:00:05+08:00"
        task.log_path = str(tmp_path / "logs" / "t1" / "run_0.log")
        task.artifact_dir = str(artifact_base / "t1" / "run_0")
        if "t1" in manager.queue:
            manager.queue.remove("t1")

    # Rerun archives run 0 and sets run_index = 1.
    manager.rerun(["t1"])

    assert task.run_index == 1
    assert len(task.run_history) == 1
    assert task.run_history[0].run_index == 0

    # Now start run 1.
    _schedule_one(manager, _fake_handle(tmp_path))

    expected = artifact_base / "t1" / "run_1"
    assert task.artifact_dir == str(expected)
    assert expected.exists()
    # run_0/ was never created in this test (we injected state), but run_1/ must exist.
    assert not (artifact_base / "t1" / "run_0").exists()


def test_system_log_uses_run_index(tmp_path):
    """System log path is logs/<task_id>/run_<N>.log, where N = run_index at start time."""
    task = _make_task(commands=["echo hi"])
    manager = _make_manager(tmp_path, [task])
    _schedule_one(manager, _fake_handle(tmp_path))

    expected_log = tmp_path / "logs" / "t1" / "run_0.log"
    assert task.log_path == str(expected_log)
