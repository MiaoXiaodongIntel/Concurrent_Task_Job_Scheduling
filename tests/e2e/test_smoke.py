"""Minimal smoke test: start task_host with sample tasks, send start, verify all succeed.

Covers design_arch.md capability 1 + 5: multi-task concurrency, all tasks succeed.

Usage:
    pytest tests/e2e/test_smoke.py -v
"""

from __future__ import annotations

from pathlib import Path

from conftest import FIXTURES, HostProcess, http_get, http_post, wait_for_all_done, wait_for_server

SAMPLE = FIXTURES / "sample_tasks.json"
SAMPLE_RESOURCES = FIXTURES / "sample_resources.json"


def test_start_and_complete() -> None:
    with HostProcess(tasks_file=SAMPLE, resources_file=SAMPLE_RESOURCES, max_concurrency=2) as port:
        # 1. Server ready
        assert wait_for_server(port, timeout=15), "FAIL: task_host did not start within 15 s"
        print("  [OK] server up")

        # 2. Verify tasks loaded (all queued)
        data = http_get(port, "/tasks")
        tasks = data.get("tasks", [])
        assert len(tasks) == 2, f"FAIL: expected 2 tasks, got {len(tasks)}"
        assert all(t["status"] == "queued" for t in tasks), \
            f"FAIL: tasks not all queued: {[t['status'] for t in tasks]}"
        print(f"  [OK] {len(tasks)} tasks loaded, all queued")

        # 3. Send start
        result = http_post(port, "/control/start")
        assert result.get("accepted"), f"FAIL: start rejected: {result}"
        print("  [OK] start accepted")

        # 4. Wait for all tasks to reach a terminal state
        tasks = wait_for_all_done(port, timeout=30)
        assert tasks is not None, "FAIL: tasks did not complete within 30 s"
        print("  [OK] all tasks reached terminal state")

        # 5. Assert all tasks succeeded
        failed = [t["task_id"] for t in tasks if t["status"] != "succeeded"]
        assert not failed, f"FAIL: tasks not succeeded: {failed}"
        print(f"  [OK] all {len(tasks)} tasks succeeded")


if __name__ == "__main__":
    print("Running smoke test ...")
    test_start_and_complete()
    print("PASS")
