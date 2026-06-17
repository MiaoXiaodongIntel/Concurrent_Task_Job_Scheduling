"""Resource registry data model and loader.

This module parses the flat resource_registry.json format and resolves config names
from an external source (HSD-ES) during load time.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class ConfigEntry:
    """One config metadata entry resolved from an external source."""

    id: int
    name: str


@dataclass
class ResourceEntry:
    """One physical resource in the registry."""

    id: int
    name: str
    properties: dict[str, str]
    config_id: int


@dataclass
class ResourceRegistry:
    """In-memory indices for config-pool scheduling."""

    configs: dict[int, ConfigEntry]
    resources: dict[int, ResourceEntry]
    resource_name_index: dict[str, int]
    resources_by_config: dict[int, list[int]]


def load_resource_registry(
    registry_file: Path,
    fetch_config_name: Callable[[int], str],
) -> ResourceRegistry:
    """Load resource registry JSON and resolve config names.

    The input format is:
    {
      "resources": [
        {"config_id": 101, "name": "machine-A", "properties": {...}},
        ...
      ]
    }
    """

    raw = json.loads(registry_file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("resources"), list):
        raise ValueError("registry file must be an object containing a 'resources' list")

    resources_raw = raw["resources"]
    if not resources_raw:
        raise ValueError("registry must contain at least one resource")

    resource_counter = itertools.count(1)
    resources: dict[int, ResourceEntry] = {}
    resource_name_index: dict[str, int] = {}
    resources_by_config: dict[int, list[int]] = {}
    seen_names: set[str] = set()

    for item in resources_raw:
        if not isinstance(item, dict):
            raise ValueError("each resource entry must be an object")

        name_raw = item.get("name", "")
        if not isinstance(name_raw, str):
            raise ValueError("resource name must be a string")
        name = name_raw.strip()
        if not name:
            raise ValueError("resource name must be non-empty")
        if name in seen_names:
            raise ValueError(f"duplicate resource name: {name!r}")
        seen_names.add(name)

        if "config_id" not in item:
            raise ValueError(f"resource {name!r} missing required field 'config_id'")
        try:
            config_id = int(item["config_id"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"resource {name!r} has invalid config_id") from exc
        if config_id <= 0:
            raise ValueError(f"resource {name!r} has non-positive config_id: {config_id}")

        properties_raw = item.get("properties", {})
        if not isinstance(properties_raw, dict):
            raise ValueError(f"resource {name!r} field 'properties' must be an object")

        properties: dict[str, str] = {}
        for key, value in properties_raw.items():
            properties[str(key)] = str(value)

        resource_id = next(resource_counter)
        entry = ResourceEntry(
            id=resource_id,
            name=name,
            properties=properties,
            config_id=config_id,
        )
        resources[resource_id] = entry
        resource_name_index[name] = resource_id
        resources_by_config.setdefault(config_id, []).append(resource_id)

    configs: dict[int, ConfigEntry] = {}
    for config_id in sorted(resources_by_config):
        config_name = fetch_config_name(config_id)
        if not isinstance(config_name, str) or not config_name.strip():
            raise ValueError(f"config name fetch returned invalid value for config_id={config_id}")
        configs[config_id] = ConfigEntry(id=config_id, name=config_name.strip())

    return ResourceRegistry(
        configs=configs,
        resources=resources,
        resource_name_index=resource_name_index,
        resources_by_config=resources_by_config,
    )
