"""Unit tests for resource_registry.load_resource_registry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resource_registry import load_resource_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(tmp_path: Path, data: object, name: str = "resource_registry.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_load_registry_success(tmp_path):
    data = {
        "resources": [
            {"config_id": 1, "name": "machine-A", "properties": {"ip": "10.0.0.1"}},
            {"config_id": 1, "name": "machine-B", "properties": {"ip": "10.0.0.2"}},
            {"config_id": 2, "name": "machine-C", "properties": {"ip": "10.0.0.3"}},
        ]
    }
    called: list[int] = []

    def fetch_name(config_id: int) -> str:
        called.append(config_id)
        return f"config-{config_id}"

    registry = load_resource_registry(_write_json(tmp_path, data), fetch_name)

    assert list(registry.resources.keys()) == [1, 2, 3]
    assert registry.resources[1].name == "machine-A"
    assert registry.resources[1].config_id == 1
    assert registry.resources_by_config == {1: [1, 2], 2: [3]}
    assert registry.resource_name_index == {
        "machine-A": 1,
        "machine-B": 2,
        "machine-C": 3,
    }
    assert registry.configs[1].name == "config-1"
    assert registry.configs[2].name == "config-2"
    assert called == [1, 2]


def test_load_registry_config_names_fetched_once_per_config(tmp_path):
    data = {
        "resources": [
            {"config_id": 42, "name": "machine-A", "properties": {}},
            {"config_id": 42, "name": "machine-B", "properties": {}},
        ]
    }
    called: list[int] = []

    def fetch_name(config_id: int) -> str:
        called.append(config_id)
        return "cfg-42"

    registry = load_resource_registry(_write_json(tmp_path, data), fetch_name)

    assert registry.configs == {42: registry.configs[42]}
    assert registry.configs[42].name == "cfg-42"
    assert called == [42]


def test_load_registry_duplicate_resource_name(tmp_path):
    data = {
        "resources": [
            {"config_id": 1, "name": "machine-A", "properties": {}},
            {"config_id": 2, "name": "machine-A", "properties": {}},
        ]
    }
    with pytest.raises(ValueError, match="duplicate resource name"):
        load_resource_registry(_write_json(tmp_path, data), lambda cid: f"config-{cid}")


def test_load_registry_empty_resources_list(tmp_path):
    data = {"resources": []}
    with pytest.raises(ValueError, match="at least one resource"):
        load_resource_registry(_write_json(tmp_path, data), lambda cid: f"config-{cid}")


def test_load_registry_missing_resources_key(tmp_path):
    data = {"configs": []}
    with pytest.raises(ValueError, match="'resources' list"):
        load_resource_registry(_write_json(tmp_path, data), lambda cid: f"config-{cid}")
