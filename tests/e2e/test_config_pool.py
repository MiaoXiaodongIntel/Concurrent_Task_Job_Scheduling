"""E2E test for config-pool scheduling with resource registry wiring (Step 7)."""

from __future__ import annotations

from conftest import FIXTURES, HostProcess, http_get, http_post, wait_for_all_done, wait_for_server

SAMPLE_TASKS_WITH_CONFIG = FIXTURES / "sample_tasks_with_config.json"
SAMPLE_RESOURCE_REGISTRY = FIXTURES / "sample_resource_registry.json"


def test_config_pool_start_and_complete() -> None:
    with HostProcess(
        tasks_file=SAMPLE_TASKS_WITH_CONFIG,
        registry_file=SAMPLE_RESOURCE_REGISTRY,
        max_concurrency=2,
    ) as port:
        assert wait_for_server(port, timeout=15), "FAIL: task_host did not start within 15 s"

        data = http_get(port, "/tasks")
        tasks = data.get("tasks", [])
        assert len(tasks) == 2, f"FAIL: expected 2 tasks, got {len(tasks)}"
        assert all(t["status"] == "queued" for t in tasks), "FAIL: expected all tasks queued before start"

        result = http_post(port, "/control/start")
        assert result.get("accepted"), f"FAIL: start rejected: {result}"

        tasks = wait_for_all_done(port, timeout=30)
        assert tasks is not None, "FAIL: tasks did not complete within timeout"

        failed = [t["task_id"] for t in tasks if t["status"] != "succeeded"]
        assert not failed, f"FAIL: tasks not succeeded: {failed}"

        assigned_resources = [t.get("assigned_resource") for t in tasks]
        assert all(isinstance(r, str) and r for r in assigned_resources), "FAIL: assigned_resource missing"
        assert len(set(assigned_resources)) == 2, "FAIL: expected two different assigned resources"


def test_registry_loaded_via_post() -> None:
    """Load registry and tasks entirely via HTTP (no startup files)."""
    import json

    registry_data = json.loads(SAMPLE_RESOURCE_REGISTRY.read_text(encoding="utf-8"))
    tasks_data = json.loads(SAMPLE_TASKS_WITH_CONFIG.read_text(encoding="utf-8"))

    # Start host with neither tasks nor registry files — host stays NOT_RUN
    with HostProcess(max_concurrency=2) as port:
        assert wait_for_server(port, timeout=15), "FAIL: task_host did not start within 15 s"

        health = http_get(port, "/health")
        assert health["host_state"] == "NOT_RUN"

        result = http_post(port, "/registry", registry_data)
        assert result.get("accepted"), f"FAIL: POST /registry rejected: {result}"

        result = http_post(port, "/tasks/submit", {"tasks": tasks_data["tasks"], "submit_mode": "append"})
        assert result.get("accepted"), f"FAIL: POST /tasks/submit rejected: {result}"

        result = http_post(port, "/control/start")
        assert result.get("accepted"), f"FAIL: start rejected: {result}"

        tasks = wait_for_all_done(port, timeout=30)
        assert tasks is not None, "FAIL: tasks did not complete within timeout"

        failed = [t["task_id"] for t in tasks if t["status"] != "succeeded"]
        assert not failed, f"FAIL: tasks not succeeded: {failed}"

        assigned_resources = [t.get("assigned_resource") for t in tasks]
        assert all(isinstance(r, str) and r for r in assigned_resources), "FAIL: assigned_resource missing after POST /registry"
