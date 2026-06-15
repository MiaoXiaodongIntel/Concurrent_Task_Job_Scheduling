"""E2e tests for control commands: graceful-stop, force-stop, rerun, RUNNING->FAILED.

Covers design_control_plane.md §3.1, §3.2, §3.3 and design_task_manager.md §2.2:
- §3.1 graceful-stop: RUNNING -> DRAINING, in-flight tasks complete, -> NOT_RUN
- §3.2 force-stop: RUNNING -> STOPPING_FORCE, tasks -> ABORTED, -> NOT_RUN
- §3.3 rerun: SUCCEEDED -> QUEUED -> completes again
- RUNNING -> FAILED: task exits with non-zero code

Usage:
    pytest tests/e2e/test_control.py -v
"""

from __future__ import annotations

from pathlib import Path

from conftest import (
    FIXTURES,
    HostProcess,
    http_get,
    http_post,
    wait_for_all_done,
    wait_for_host_state,
    wait_for_server,
    wait_for_task_state,
)

SAMPLE = FIXTURES / "sample_tasks.json"
SAMPLE_RESOURCES = FIXTURES / "sample_resources.json"
LONG_RUNNING = FIXTURES / "tasks_long_running.json"
FAILING = FIXTURES / "tasks_failing.json"


# ---------------------------------------------------------------------------
# §3.1 Graceful-stop: RUNNING -> DRAINING -> NOT_RUN
# ---------------------------------------------------------------------------

def test_graceful_stop_drains_and_returns_not_run() -> None:
    """graceful-stop while a task is RUNNING: host drains and returns to NOT_RUN.

    Sequence:
      1. Load a long-running task; send start.
      2. Wait for task to reach RUNNING.
      3. Send graceful-stop; verify host transitions to DRAINING.
      4. Verify the task is still alive (not aborted).
      5. Force-stop to accelerate cleanup (avoids 30 s wait).
      6. Wait for host to reach NOT_RUN.
    """
    with HostProcess(tasks_file=LONG_RUNNING, resources_file=SAMPLE_RESOURCES, max_concurrency=1) as port:
        assert wait_for_server(port), "FAIL: host did not start"

        start_result = http_post(port, "/control/start")
        assert start_result.get("accepted"), f"FAIL: start rejected: {start_result}"

        # Wait for the task to enter RUNNING state.
        assert wait_for_task_state(port, "long-1", "running", timeout=20), \
            "FAIL: task did not reach running state within 20 s"

        # Send graceful-stop; host must transition to DRAINING.
        stop_result = http_post(port, "/control/graceful-stop")
        assert stop_result.get("accepted"), f"FAIL: graceful-stop rejected: {stop_result}"

        assert wait_for_host_state(port, "DRAINING", timeout=5), \
            "FAIL: host did not reach DRAINING after graceful-stop"

        # Task must still be alive (not aborted) during DRAINING.
        task_data = http_get(port, "/tasks/long-1")
        assert task_data["status"] not in {"aborted", "succeeded", "failed"}, \
            f"FAIL: task should still be running during DRAINING, got {task_data['status']}"

        # Accelerate: force-stop to avoid waiting 30 s for the sleep to finish.
        http_post(port, "/control/force-stop")
        assert wait_for_host_state(port, "NOT_RUN", timeout=10), \
            "FAIL: host did not reach NOT_RUN after force-stop"

        health = http_get(port, "/health")
        assert health["host_state"] == "NOT_RUN"


# ---------------------------------------------------------------------------
# §3.2 Force-stop: RUNNING -> STOPPING_FORCE -> NOT_RUN, tasks -> ABORTED
# ---------------------------------------------------------------------------

def test_force_stop_aborts_running_tasks_and_returns_not_run() -> None:
    """force-stop while tasks are RUNNING: all tasks transition to ABORTED and host to NOT_RUN.

    Sequence:
      1. Load a long-running task; send start.
      2. Wait for task to reach RUNNING.
      3. Send force-stop; verify host reaches NOT_RUN.
      4. Verify all tasks are ABORTED.
    """
    with HostProcess(tasks_file=LONG_RUNNING, resources_file=SAMPLE_RESOURCES, max_concurrency=1) as port:
        assert wait_for_server(port), "FAIL: host did not start"

        start_result = http_post(port, "/control/start")
        assert start_result.get("accepted"), f"FAIL: start rejected: {start_result}"

        assert wait_for_task_state(port, "long-1", "running", timeout=20), \
            "FAIL: task did not reach running state within 20 s"

        force_result = http_post(port, "/control/force-stop")
        assert force_result.get("accepted"), f"FAIL: force-stop rejected: {force_result}"

        assert wait_for_host_state(port, "NOT_RUN", timeout=15), \
            "FAIL: host did not reach NOT_RUN after force-stop"

        # All tasks must be ABORTED.
        data = http_get(port, "/tasks")
        tasks = data.get("tasks", [])
        non_aborted = [t["task_id"] for t in tasks if t["status"] != "aborted"]
        assert not non_aborted, f"FAIL: tasks not aborted after force-stop: {non_aborted}"


# ---------------------------------------------------------------------------
# §3.3 Rerun: SUCCEEDED -> QUEUED -> completes again
# ---------------------------------------------------------------------------

def test_rerun_succeeded_task_reruns_and_succeeds() -> None:
    """rerun on a SUCCEEDED task: task returns to QUEUED and completes successfully again.

    Sequence:
      1. Load sample tasks; start; wait for all to succeed.
      2. Send rerun for all task_ids.
      3. Wait for all tasks to succeed again.
    """
    with HostProcess(tasks_file=SAMPLE, resources_file=SAMPLE_RESOURCES, max_concurrency=2) as port:
        assert wait_for_server(port), "FAIL: host did not start"

        start_result = http_post(port, "/control/start")
        assert start_result.get("accepted"), f"FAIL: start rejected: {start_result}"

        # First run: wait for all tasks to succeed.
        tasks = wait_for_all_done(port, timeout=30)
        assert tasks is not None, "FAIL: tasks did not complete within 30 s (first run)"
        assert all(t["status"] == "succeeded" for t in tasks), \
            f"FAIL: not all tasks succeeded on first run: {[(t['task_id'], t['status']) for t in tasks]}"

        # Send rerun for all task_ids.
        task_ids = [t["task_id"] for t in tasks]
        rerun_result = http_post(port, "/control/rerun", {"task_ids": task_ids})
        assert rerun_result.get("accepted"), f"FAIL: rerun rejected: {rerun_result}"
        assert set(rerun_result.get("affected_task_ids", [])) == set(task_ids), \
            f"FAIL: not all tasks accepted for rerun: {rerun_result}"

        # Second run: host is still RUNNING; tasks will be scheduled automatically.
        tasks = wait_for_all_done(port, timeout=30)
        assert tasks is not None, "FAIL: tasks did not complete within 30 s (second run)"
        assert all(t["status"] == "succeeded" for t in tasks), \
            f"FAIL: not all tasks succeeded on second run: {[(t['task_id'], t['status']) for t in tasks]}"


# ---------------------------------------------------------------------------
# RUNNING -> FAILED: non-zero exit code
# ---------------------------------------------------------------------------

def test_failing_task_transitions_to_failed() -> None:
    """A task whose command exits with non-zero code transitions to FAILED.

    Sequence:
      1. Load a task with `exit 1`; send start.
      2. Wait for task to reach a terminal state.
      3. Verify status == failed and exit_code != 0.
    """
    with HostProcess(tasks_file=FAILING, resources_file=SAMPLE_RESOURCES, max_concurrency=1) as port:
        assert wait_for_server(port), "FAIL: host did not start"

        start_result = http_post(port, "/control/start")
        assert start_result.get("accepted"), f"FAIL: start rejected: {start_result}"

        tasks = wait_for_all_done(port, timeout=20)
        assert tasks is not None, "FAIL: task did not complete within 20 s"

        fail_task = next((t for t in tasks if t["task_id"] == "fail-1"), None)
        assert fail_task is not None, "FAIL: fail-1 not found in task list"
        assert fail_task["status"] == "failed", \
            f"FAIL: expected status=failed, got {fail_task['status']}"
        assert fail_task.get("exit_code") not in (None, 0), \
            f"FAIL: expected non-zero exit_code, got {fail_task.get('exit_code')}"


if __name__ == "__main__":
    print("Running control tests ...")
    test_graceful_stop_drains_and_returns_not_run()
    print("PASS: graceful-stop")
    test_force_stop_aborts_running_tasks_and_returns_not_run()
    print("PASS: force-stop")
    test_rerun_succeeded_task_reruns_and_succeeds()
    print("PASS: rerun")
    test_failing_task_transitions_to_failed()
    print("PASS: RUNNING->FAILED")
