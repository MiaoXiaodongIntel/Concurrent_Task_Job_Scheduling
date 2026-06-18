"""Unit tests for task definition loading (task_host.load_tasks).

Covers config-pool-only task contract:
- Both accepted file formats (top-level list, dict with tasks key)
- Validation: duplicate task_id, empty commands, missing commands
- Validation: missing/invalid/unregistered config_id
- Auto-generated task_id when missing or empty
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from task_host import load_tasks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(tmp_path: Path, data: object, name: str = "tasks.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


REGISTERED_CONFIG_IDS = {1, 2}

VALID_TASK = {"task_id": "c1", "config_id": 1, "priority": 1, "commands": ["echo hi"]}


# ---------------------------------------------------------------------------
# Task loading tests
# ---------------------------------------------------------------------------

def test_list_format(tmp_path):
    """Top-level list is accepted."""
    data = [VALID_TASK]
    tasks = load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)
    assert len(tasks) == 1
    assert tasks[0].task_id == "c1"
    assert tasks[0].config_id == 1
    assert tasks[0].priority == 1


def test_dict_with_tasks_key(tmp_path):
    """Object containing tasks key is accepted."""
    data = {"tasks": [VALID_TASK]}
    tasks = load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)
    assert len(tasks) == 1


def test_auto_generated_task_id(tmp_path):
    """task_id is auto-generated when missing."""
    data = [{"config_id": 1, "priority": 1, "commands": ["echo hi"]}]
    tasks = load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)
    assert tasks[0].task_id.startswith("job-")


def test_duplicate_task_id_raises(tmp_path):
    """Duplicate task_id raises ValueError."""
    task = {"task_id": "dup", "config_id": 1, "priority": 1, "commands": ["echo 1"]}
    data = [task, {**task, "commands": ["echo 2"]}]
    with pytest.raises(ValueError, match="duplicate"):
        load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)


def test_empty_commands_raises(tmp_path):
    """Empty commands list raises ValueError."""
    data = [{"task_id": "t1", "config_id": 1, "priority": 1, "commands": []}]
    with pytest.raises(ValueError):
        load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)


def test_missing_config_id_raises(tmp_path):
    """Missing config_id field raises ValueError."""
    data = [{"task_id": "t1", "priority": 1, "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="config_id"):
        load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)


def test_non_positive_config_id_raises(tmp_path):
    """Non-positive config_id raises ValueError."""
    data = [{"task_id": "t1", "config_id": 0, "priority": 1, "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="non-positive"):
        load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)


def test_missing_priority_raises(tmp_path):
    """Missing priority field raises ValueError."""
    data = [{"task_id": "t1", "config_id": 1, "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="priority"):
        load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)


def test_invalid_priority_raises(tmp_path):
    """Non-positive priority raises ValueError."""
    data = [{"task_id": "t1", "config_id": 1, "priority": 0, "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="priority"):
        load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)


def test_invalid_top_level_raises(tmp_path):
    """Neither list nor dict-with-tasks raises ValueError."""
    data = "not valid"
    with pytest.raises(ValueError):
        load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)


def test_config_id_task_is_accepted(tmp_path):
    """A task with config_id is accepted."""
    data = [VALID_TASK]
    tasks = load_tasks(
        _write_json(tmp_path, data),
        registered_config_ids=REGISTERED_CONFIG_IDS,
    )
    assert len(tasks) == 1
    assert tasks[0].task_id == "c1"
    assert tasks[0].config_id == 1


def test_unregistered_config_id_raises(tmp_path):
    """Config_id not in registry raises ValueError."""
    data = [{"task_id": "c1", "config_id": 9, "priority": 1, "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="unregistered config_id"):
        load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)


def test_missing_config_id_raises_strictly(tmp_path):
    """Task must provide config_id in config-pool-only mode."""
    data = [{"task_id": "t1", "priority": 1, "commands": ["echo hi"]}]
    with pytest.raises(ValueError, match="config_id"):
        load_tasks(_write_json(tmp_path, data), registered_config_ids=REGISTERED_CONFIG_IDS)

