"""Unit tests for TaskManager command template rendering (Step 5)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from resource_registry import ConfigEntry, ResourceEntry, ResourceRegistry
from task_manager import TaskJob, TaskManager


def _make_manager(tmp_path: Path) -> TaskManager:
    from scheduler import Scheduler
    from task_runner import TaskRunner

    scheduler = Scheduler(max_concurrency=2, max_cpu_percent=95.0,
                          max_memory_percent=95.0, max_disk_active_percent=99.0)
    runner = MagicMock(spec=TaskRunner)
    return TaskManager(
        tasks=[TaskJob(task_id="t1", commands=["echo ok"], resource="machine-A", priority=1)],
        scheduler=scheduler,
        runner=runner,
        log_dir=tmp_path / "logs",
        scheduler_tick=1.0,
        status_interval=60.0,
        registered_resources=["machine-A"],
    )


def _sample_registry() -> ResourceRegistry:
    return ResourceRegistry(
        configs={1: ConfigEntry(id=1, name="config-1")},
        resources={
            1: ResourceEntry(
                id=1,
                name="machine-A",
                properties={"ip": "10.0.0.1", "os": "ubuntu22", "cpu": "spr"},
                config_id=1,
            )
        },
        resource_name_index={"machine-A": 1},
        resources_by_config={1: [1]},
    )


def test_render_resource_name(tmp_path):
    manager = _make_manager(tmp_path)
    manager._resource_registry = _sample_registry()

    out = manager._render_commands(["echo {resource.name}"], "machine-A")

    assert out == ["echo machine-A"]


def test_render_resource_property(tmp_path):
    manager = _make_manager(tmp_path)
    manager._resource_registry = _sample_registry()

    out = manager._render_commands(["ssh {resource.properties.ip}"], "machine-A")

    assert out == ["ssh 10.0.0.1"]


def test_render_multiple_placeholders(tmp_path):
    manager = _make_manager(tmp_path)
    manager._resource_registry = _sample_registry()

    out = manager._render_commands(
        ["run --host {resource.name} --ip {resource.properties.ip} --cpu {resource.properties.cpu}"],
        "machine-A",
    )

    assert out == ["run --host machine-A --ip 10.0.0.1 --cpu spr"]


def test_render_unknown_placeholder_raises(tmp_path):
    manager = _make_manager(tmp_path)
    manager._resource_registry = _sample_registry()

    with pytest.raises(ValueError, match="Unknown placeholder"):
        manager._render_commands(["echo {resource.properties.not_exist}"], "machine-A")


def test_render_no_registry_returns_unchanged(tmp_path):
    manager = _make_manager(tmp_path)

    commands = ["echo {resource.name}", "echo plain"]
    out = manager._render_commands(commands, "machine-A")

    assert out == commands
    assert out is not commands
