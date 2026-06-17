"""Unit tests for task definition loading (task_host.load_tasks and load_resources_file).

Covers design_task_host.md §3 (Task Definition Contract):
- Both accepted file formats (top-level list, dict with tasks key)
- Validation: duplicate task_id, empty commands, missing commands
- Validation: missing resource, unregistered resource, missing priority, invalid priority
- Auto-generated task_id when missing or empty
- Resources file loading and validation
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from task_host import load_tasks, load_resources_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(tmp_path: Path, data: object, name: str = "tasks.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


REGISTERED_RESOURCES = {"machine-A", "machine-B"}
REGISTERED_CONFIG_IDS = {1, 2}

VALID_TASK = {"task_id": "t1", "resource": "machine-A", "priority": 1, "commands": ["echo hi"]}
VALID_CONFIG_TASK = {"task_id": "c1", "config_id": 1, "priority": 1, "commands": ["echo hi"]}


# ---------------------------------------------------------------------------
# Task loading tests
# ---------------------------------------------------------------------------

def test_list_format(tmp_path):
    """Top-level list is accepted."""
    data = [VALID_TASK]
    tasks = load_tasks(_write_json(tmp_path, data), registered_resources=REGISTERED_RESOURCES)
    assert len(tasks) == 1
    assert tasks[0].task_id == "t1"
    assert tasks[0].resource == "machine-A"
    assert tasks[0].priority == 1


def test_dict_with_tasks_key(tmp_path):
    """Object containing tasks key is accepted."""
    data = {"tasks": [VALID_TASK]}
    tasks = load_tasks(_write_json(tmp_path, data), registered_resources=REGISTERED_RESOURCES)
    assert len(tasks) == 1


def test_auto_generated_task_id(tmp_path):
    """task_id is auto-generated when missing."""
    data = [{"resource": "machine-A", "priority": 1, "commands": ["echo hi"]}]
    tasks = load_tasks(_write_json(tmp_path, data), registered_resources=REGISTERED_RESOURCES)
    assert tasks[0].task_id.startswith("job-")


def test_duplicate_task_id_raises(tmp_path):
    """Duplicate task_id raises ValueError."""
    task = {"task_id": "dup", "resource": "machine-A", "priority": 1, "commands": ["echo 1"]}
    data = [task, {**task, "commands": ["echo 2"]}]
    with pytest.raises(ValueError, match="duplicate"):
        load_tasks(_write_json(tmp_path, data), registered_resources=REGISTERED_RESOURCES)


def test_empty_commands_raises(tmp_path):
    """Empty commands list raises ValueError."""
    data = [{"task_id": "t1", "resource": "machine-A", "priority": 1, "commands": []}]
    with pytest.raises(ValueError):
        load_tasks(_write_json(tmp_path, data), registered_resources=REGISTERED_RESOURCES)


def test_missing_resource_raises(tmp_path):
    """Missing resource field raises ValueError."""
    data = [{"task_id": "t1", "priority": 1, "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="resource"):
        load_tasks(_write_json(tmp_path, data), registered_resources=REGISTERED_RESOURCES)


def test_unregistered_resource_raises(tmp_path):
    """Resource not in registry raises ValueError."""
    data = [{"task_id": "t1", "resource": "machine-Z", "priority": 1, "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="unregistered"):
        load_tasks(_write_json(tmp_path, data), registered_resources=REGISTERED_RESOURCES)


def test_missing_priority_raises(tmp_path):
    """Missing priority field raises ValueError."""
    data = [{"task_id": "t1", "resource": "machine-A", "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="priority"):
        load_tasks(_write_json(tmp_path, data), registered_resources=REGISTERED_RESOURCES)


def test_invalid_priority_raises(tmp_path):
    """Non-positive priority raises ValueError."""
    data = [{"task_id": "t1", "resource": "machine-A", "priority": 0, "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="priority"):
        load_tasks(_write_json(tmp_path, data), registered_resources=REGISTERED_RESOURCES)


def test_invalid_top_level_raises(tmp_path):
    """Neither list nor dict-with-tasks raises ValueError."""
    data = "not valid"
    with pytest.raises(ValueError):
        load_tasks(_write_json(tmp_path, data), registered_resources=REGISTERED_RESOURCES)


def test_config_id_task_is_accepted(tmp_path):
    """A task with config_id (without resource) is accepted."""
    data = [VALID_CONFIG_TASK]
    tasks = load_tasks(
        _write_json(tmp_path, data),
        registered_config_ids=REGISTERED_CONFIG_IDS,
    )
    assert len(tasks) == 1
    assert tasks[0].task_id == "c1"
    assert tasks[0].config_id == 1
    assert tasks[0].resource == ""


def test_unregistered_config_id_raises(tmp_path):
    """Config_id not in registry raises ValueError."""
    data = [{"task_id": "c1", "config_id": 9, "priority": 1, "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="unregistered config_id"):
        load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)


def test_missing_resource_and_config_id_raises(tmp_path):
    """Task must provide either resource or config_id."""
    data = [{"task_id": "t1", "priority": 1, "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="resource.*config_id"):
        load_tasks(_write_json(tmp_path, data), registered_resources=REGISTERED_RESOURCES)


# ---------------------------------------------------------------------------
# Resources file loading tests
# ---------------------------------------------------------------------------

def test_load_resources_file_valid(tmp_path):
    """Valid resources file is loaded correctly."""
    data = {"resources": ["machine-A", "machine-B"]}
    result = load_resources_file(_write_json(tmp_path, data, "resources.json"))
    assert result == ["machine-A", "machine-B"]


def test_load_resources_file_deduplicates(tmp_path):
    """Duplicate resource entries are deduplicated (first occurrence kept)."""
    data = {"resources": ["machine-A", "machine-B", "machine-A"]}
    result = load_resources_file(_write_json(tmp_path, data, "resources.json"))
    assert result == ["machine-A", "machine-B"]


def test_load_resources_file_empty_raises(tmp_path):
    """Empty resources list raises ValueError."""
    data = {"resources": []}
    with pytest.raises(ValueError, match="empty"):
        load_resources_file(_write_json(tmp_path, data, "resources.json"))


def test_load_resources_file_wrong_format_raises(tmp_path):
    """Resources file without 'resources' key raises ValueError."""
    data = {"machines": ["machine-A"]}
    with pytest.raises(ValueError):
        load_resources_file(_write_json(tmp_path, data, "resources.json"))

