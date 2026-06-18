"""Unit tests for tools/task_builder.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import task_builder


class _FakeClient:
    def __init__(self, query_data=None, parents=None):
        self._query_data = query_data or []
        self._parents = parents or {}
        self.get_article_calls: list[int] = []

    def get_query(self, query_id: int):
        return {"ok": True, "data": self._query_data}

    def get_article(self, article_id: int):
        self.get_article_calls.append(article_id)
        payload = self._parents.get(str(article_id), {})
        return {"ok": True, "data": payload}


def test_slugify_task_id():
    assert task_builder.slugify_task_id("PMSS_RESET_TEST_046 - Reset with Stress") == (
        "PMSS_RESET_TEST_046_Reset_with_Stress"
    )


def test_build_tasks_uses_parent_priority_and_kayak_prefix(monkeypatch):
    monkeypatch.setenv("KAYAK_PATH", r"C:\infra\kayak_submit")

    child_articles = [
        {
            "id": "16026982460",
            "title": "PMSS_RESET_TEST_046 - Reset with Stress",
            "parent_id": "16016983699",
            task_builder.CONFIG_ID_FIELD: "22016260197",
            task_builder.AUTOMATION_FRAMEWORKS_FIELD: "Kayak",
            task_builder.COMMAND_LINE_FIELD: "python harness_main.py --test PMSS_RESET_STRESS --loops=50",
        }
    ]
    parent_articles = {
        "16016983699": {
            "id": "16016983699",
            "priority": "7",
        }
    }

    tasks, unmatched = task_builder.build_tasks_from_articles(
        child_articles=child_articles,
        parent_articles=parent_articles,
        known_config_ids={22016260197},
        default_priority=100,
    )

    assert unmatched == []
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "PMSS_RESET_TEST_046_Reset_with_Stress"
    assert tasks[0]["config_id"] == 22016260197
    assert tasks[0]["priority"] == 7
    assert tasks[0]["commands"] == [
        r"cd C:\infra\kayak_submit",
        r"Powershell.exe -NonInteractive -File src\kayak\scripts\setup_kayak.ps1",
        "python harness_main.py --test PMSS_RESET_STRESS --loops=50",
    ]


def test_get_pre_commands_kayak_raises_when_env_missing(monkeypatch):
    monkeypatch.delenv("KAYAK_PATH", raising=False)
    with pytest.raises(ValueError, match="KAYAK_PATH"):
        task_builder.get_pre_commands("Kayak")


def test_get_pre_commands_strips_quoted_env_value(monkeypatch):
    monkeypatch.setenv("KAYAK_PATH", '"C:\\kayak_submit"')
    commands = task_builder.get_pre_commands("Kayak")
    assert commands[0] == r"cd C:\kayak_submit"


def test_build_tasks_priority_falls_back_to_default_when_parent_priority_missing():
    child_articles = [
        {
            "id": "16026982460",
            "title": "Simple Task",
            "parent_id": "16016983699",
            task_builder.CONFIG_ID_FIELD: "22016260197",
            task_builder.AUTOMATION_FRAMEWORKS_FIELD: "CRAuto",
            task_builder.COMMAND_LINE_FIELD: "run_task.bat",
        }
    ]
    parent_articles = {
        "16016983699": {
            "id": "16016983699",
            "priority": None,
        }
    }

    tasks, unmatched = task_builder.build_tasks_from_articles(
        child_articles=child_articles,
        parent_articles=parent_articles,
        known_config_ids={22016260197},
        default_priority=123,
    )

    assert unmatched == []
    assert tasks[0]["priority"] == 123
    assert tasks[0]["commands"] == ["run_task.bat"]


def test_build_tasks_collects_unmatched_configs():
    child_articles = [
        {
            "id": "16026982460",
            "title": "Task A",
            "parent_id": "16016983699",
            task_builder.CONFIG_ID_FIELD: "999999",
            task_builder.AUTOMATION_FRAMEWORKS_FIELD: "Kayak",
            task_builder.COMMAND_LINE_FIELD: "run_a",
        },
        {
            "id": "16026982461",
            "title": "Task B",
            "parent_id": "16016983699",
            task_builder.CONFIG_ID_FIELD: "888888",
            task_builder.AUTOMATION_FRAMEWORKS_FIELD: "Kayak",
            task_builder.COMMAND_LINE_FIELD: "run_b",
        },
    ]

    tasks, unmatched = task_builder.build_tasks_from_articles(
        child_articles=child_articles,
        parent_articles={},
        known_config_ids={22016260197},
        default_priority=100,
    )

    assert tasks == []
    assert unmatched == [
        {
            "article_id": "16026982460",
            "title": "Task A",
            "config_id": 999999,
        },
        {
            "article_id": "16026982461",
            "title": "Task B",
            "config_id": 888888,
        },
    ]


def test_fetch_parent_articles_deduplicates_parent_ids():
    child_articles = [
        {"id": "1", "parent_id": "100"},
        {"id": "2", "parent_id": "100"},
        {"id": "3", "parent_id": "200"},
    ]
    client = _FakeClient(
        parents={
            "100": [{"id": "100", "priority": "5"}],
            "200": [{"id": "200", "priority": "8"}],
        }
    )

    parents = task_builder.fetch_parent_articles(client, child_articles)

    assert client.get_article_calls == [100, 200]
    assert parents["100"]["priority"] == "5"
    assert parents["200"]["priority"] == "8"


def test_main_when_unmatched_does_not_write_output(tmp_path: Path, monkeypatch, capsys):
    registry = {
        "resources": [
            {
                "config_id": 1,
                "name": "machine-A",
                "properties": {"ip": "10.0.0.1"},
            }
        ]
    }
    registry_path = tmp_path / "resource_registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    output_path = tmp_path / "out" / "tasks.json"

    query_data = [
        {
            "id": "16026982460",
            "title": "Task Unmatched",
            "parent_id": "16016983699",
            task_builder.CONFIG_ID_FIELD: "22016260197",
            task_builder.AUTOMATION_FRAMEWORKS_FIELD: "Kayak",
            task_builder.COMMAND_LINE_FIELD: "python run.py",
        }
    ]

    fake_client = _FakeClient(query_data=query_data, parents={})
    monkeypatch.setattr(task_builder, "HsdesClient", lambda: fake_client)

    rc = task_builder.main(
        [
            "--registry",
            str(registry_path),
            "--query-id",
            "123",
            "--output",
            str(output_path),
        ]
    )

    assert rc == 2
    assert not output_path.exists()
    out = capsys.readouterr().out
    assert "config_id not in resource registry" in out
    assert "Task Unmatched" in out
