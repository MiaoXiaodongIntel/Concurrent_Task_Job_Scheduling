"""Unit tests for task definition loading (task_host.load_tasks).

Covers design_task_host.md §3 (Task Definition Contract):
- Both accepted file formats (top-level list, dict with tasks key)
- Validation: duplicate task_id, empty commands, missing commands
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

def _write_json(tmp_path: Path, data: object) -> Path:
    p = tmp_path / "tasks.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_list_format(tmp_path):
    """Top-level list is accepted."""
    data = [{"task_id": "t1", "commands": ["echo hi"]}]
    tasks = load_tasks(_write_json(tmp_path, data))
    assert len(tasks) == 1
    assert tasks[0].task_id == "t1"


def test_dict_with_tasks_key(tmp_path):
    """Object containing tasks key is accepted."""
    data = {"tasks": [{"task_id": "t1", "commands": ["echo hi"]}]}
    tasks = load_tasks(_write_json(tmp_path, data))
    assert len(tasks) == 1


def test_auto_generated_task_id(tmp_path):
    """task_id is auto-generated when missing."""
    data = [{"commands": ["echo hi"]}]
    tasks = load_tasks(_write_json(tmp_path, data))
    assert tasks[0].task_id.startswith("job-")


def test_duplicate_task_id_raises(tmp_path):
    """Duplicate task_id raises ValueError."""
    data = [
        {"task_id": "dup", "commands": ["echo 1"]},
        {"task_id": "dup", "commands": ["echo 2"]},
    ]
    with pytest.raises(ValueError, match="duplicate"):
        load_tasks(_write_json(tmp_path, data))


def test_empty_commands_raises(tmp_path):
    """Empty commands list raises ValueError."""
    data = [{"task_id": "t1", "commands": []}]
    with pytest.raises(ValueError):
        load_tasks(_write_json(tmp_path, data))


def test_invalid_top_level_raises(tmp_path):
    """Neither list nor dict-with-tasks raises ValueError."""
    data = "not valid"
    with pytest.raises(ValueError):
        load_tasks(_write_json(tmp_path, data))
