#!/usr/bin/env python3
"""Host process for concurrent task jobs.

This file is the composition root that wires:
- TaskManager (lifecycle and state machine)
- Scheduler (admission decisions)
- TaskRunner (single task execution)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path
from typing import Callable

from monitor_api import MonitorServer
from resource_registry import ResourceRegistry, load_resource_registry
from scheduler import Scheduler
from task_manager import TaskJob, TaskManager, TaskStatus
from task_runner import TaskRunner


def load_tasks(
    tasks_file: Path,
    registered_config_ids: set[int] | None = None,
) -> list[TaskJob]:
    raw = json.loads(tasks_file.read_text(encoding="utf-8"))

    task_items: list[dict[str, object]]
    if isinstance(raw, list):
        task_items = raw
    elif isinstance(raw, dict) and isinstance(raw.get("tasks"), list):
        task_items = raw["tasks"]
    else:
        raise ValueError("tasks file must be a list, or an object containing a 'tasks' list")

    tasks: list[TaskJob] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(task_items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"task index {idx} is not an object")

        maybe_id = item.get("task_id")
        task_id = str(maybe_id).strip() if maybe_id is not None else f"job-{idx:03d}"
        if not task_id:
            task_id = f"job-{idx:03d}"
        if task_id in seen_ids:
            raise ValueError(f"duplicate task_id: {task_id}")
        seen_ids.add(task_id)

        commands_raw = item.get("commands")
        if not isinstance(commands_raw, list) or not commands_raw:
            raise ValueError(f"task {task_id} must include non-empty 'commands' list")

        commands: list[str] = []
        for command in commands_raw:
            if not isinstance(command, str) or not command.strip():
                raise ValueError(f"task {task_id} has invalid command: {command!r}")
            commands.append(command)

        config_id_raw = item.get("config_id", 0)
        config_id = 0
        if config_id_raw not in (None, ""):
            try:
                config_id = int(config_id_raw)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"task {task_id} has invalid config_id: {config_id_raw!r}") from exc
            if config_id <= 0:
                raise ValueError(f"task {task_id} has non-positive config_id: {config_id}")

        if config_id <= 0:
            raise ValueError(f"task {task_id} must include a positive 'config_id'")
        if registered_config_ids is not None and config_id not in registered_config_ids:
            raise ValueError(f"task {task_id} references unregistered config_id: {config_id}")

        priority_raw = item.get("priority")
        if priority_raw is None:
            raise ValueError(f"task {task_id} must have a 'priority' integer field")
        try:
            priority = int(priority_raw)  # type: ignore[arg-type]
            if priority < 1:
                raise ValueError("priority must be positive")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"task {task_id} 'priority' must be a positive integer: {exc}"
            ) from exc

        tasks.append(
            TaskJob(
                task_id=task_id,
                commands=commands,
                resource="",
                config_id=config_id,
                priority=priority,
            )
        )

    if not tasks:
        raise ValueError("No tasks found in tasks file")
    return tasks

def _build_config_name_fetcher() -> Callable[[int], str]:
    """Build a config-name fetcher with local fallback when HSD is unavailable."""

    cache: dict[int, str] = {}

    username = os.getenv("HSDES_USERNAME")
    token = os.getenv("HSDES_TOKEN")
    client = None
    if username and token:
        try:
            from lib.hsdes_client import HsdesClient

            client = HsdesClient(username=username, token=token)
        except Exception:
            client = None

    def fetch_config_name(config_id: int) -> str:
        if config_id in cache:
            return cache[config_id]

        if client is not None:
            try:
                result = client.get_article(config_id, fields=["id", "title", "name"])
                if result.get("ok"):
                    data = result.get("data")
                    row = data[0] if isinstance(data, list) and data else data
                    if isinstance(row, dict):
                        maybe_name = row.get("name") or row.get("title")
                        if isinstance(maybe_name, str) and maybe_name.strip():
                            cache[config_id] = maybe_name.strip()
                            return cache[config_id]
            except Exception:
                pass

        # Local/test fallback: keep host runnable when HSD is unavailable.
        cache[config_id] = f"config-{config_id}"
        return cache[config_id]

    return fetch_config_name


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Host scheduler: concurrent task jobs with state machine and real-time logs"
    )
    parser.add_argument(
        "--tasks-file",
        default="",
        help="Optional path to JSON task definition file. If omitted, host starts with empty tasks and waits for submit.",
    )
    parser.add_argument(
        "--registry-file",
        default="",
        help="Path to JSON resource_registry file (config_id-based pools).",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=None,
        help="Maximum concurrent running task jobs. If omitted, no concurrency cap is applied and admission is limited only by the CPU/memory/disk thresholds.",
    )
    parser.add_argument(
        "--max-cpu-percent",
        type=float,
        default=75.0,
        help="Suspend new admission when host CPU usage reaches this threshold (default: 75)",
    )
    parser.add_argument(
        "--max-memory-percent",
        type=float,
        default=75.0,
        help="Suspend new admission when host memory usage reaches this threshold (default: 75)",
    )
    parser.add_argument(
        "--max-disk-active-percent",
        type=float,
        default=80.0,
        help="Suspend new admission when host disk active time reaches this threshold (default: 80)",
    )
    parser.add_argument(
        "--scheduler-tick",
        type=float,
        default=0.5,
        help="Scheduler tick interval in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--status-interval",
        type=float,
        default=2.0,
        help="Host status print interval in seconds (default: 2.0)",
    )
    parser.add_argument(
        "--log-dir",
        default="logs",
        help="Directory for per-task log files (default: logs)",
    )
    parser.add_argument(
        "--artifact-base-dir",
        default="task_artifacts",
        help=(
            "Base directory for tool-specific artifact output (e.g. Kayak log dirs). "
            "When a task command contains {ARTIFACT_DIR}, it is expanded to "
            "<artifact-base-dir>/<task_id>/run_<N>/ and the directory is created. "
            "Commands without {ARTIFACT_DIR} are unaffected (default: task_artifacts)."
        ),
    )
    parser.add_argument(
        "--summary-json",
        default="",
        help="Optional path to write final host/task summary as JSON",
    )
    parser.add_argument(
        "--monitor-host",
        default="127.0.0.1",
        help="Monitor API bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--monitor-port",
        type=int,
        default=8765,
        help="Monitor API port (default: 8765)",
    )
    parser.add_argument(
        "--interactive-cli",
        action="store_true",
        help="Enable interactive stdin command loop for CLI usage",
    )
    return parser.parse_args()


def _command_loop(manager: TaskManager) -> None:
    print(
        "[CTRL] Command loop ready. Supported: "
        "start, graceful_stop, force_stop, rerun <task_id...>, shutdown [drain|force]"
    )
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue

        parts = line.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in {"start", "graceful_stop", "force_stop", "rerun", "shutdown"}:
            if cmd == "rerun" and not args:
                print("[CTRL] rerun ignored: provide at least one task_id")
                continue
            if cmd == "shutdown":
                mode = args[0].strip().lower() if args else "drain"
                result = manager.control(cmd, options={"mode": mode})
                print(f"[CTRL] shutdown {'accepted' if result.get('accepted') else 'ignored'}")
                continue

            result = manager.control(cmd, args)
            if cmd == "rerun":
                print(
                    "[CTRL] rerun "
                    f"accepted={result.get('affected_task_ids', [])} "
                    f"rejected={result.get('rejected_task_ids', [])}"
                )
            else:
                print(f"[CTRL] {cmd} {'accepted' if result.get('accepted') else 'ignored'}")
            continue

        print(f"[CTRL] unknown command: {line}")


def build_summary(manager: TaskManager) -> dict[str, object]:
    tasks = [task.to_dict() for task in sorted(manager.tasks.values(), key=lambda t: t.task_id)]
    return {
        "host_state": manager.host_state.value,
        "queue_size": sum(1 for t in manager.tasks.values() if t.status == TaskStatus.QUEUED),
        "pending_count": sum(1 for t in manager.tasks.values() if t.status == TaskStatus.PENDING),
        "starting_count": sum(1 for t in manager.tasks.values() if t.status == TaskStatus.STARTING),
        "running_count": sum(1 for t in manager.tasks.values() if t.status == TaskStatus.RUNNING),
        "completed_count": sum(
            1
            for t in manager.tasks.values()
            if t.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED}
        ),
        "tasks": tasks,
    }


def main() -> int:
    args = parse_args()

    log_dir = Path(args.log_dir).resolve()
    artifact_base_dir = Path(args.artifact_base_dir).resolve()

    resource_registry: ResourceRegistry | None = None
    tasks: list[TaskJob] = []

    if args.tasks_file and not args.registry_file:
        print(
            "Error: --registry-file is required when --tasks-file is provided.",
            file=sys.stderr,
        )
        return 2

    if args.registry_file:
        registry_file = Path(args.registry_file).resolve()
        try:
            resource_registry = load_resource_registry(
                registry_file,
                fetch_config_name=_build_config_name_fetcher(),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Failed to load resource registry file: {exc}", file=sys.stderr)
            return 2

    if args.tasks_file:
        tasks_file = Path(args.tasks_file).resolve()
        try:
            tasks = load_tasks(
                tasks_file,
                registered_config_ids=(set(resource_registry.configs.keys()) if resource_registry else None),
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Failed to load tasks file: {exc}", file=sys.stderr)
            return 2

    scheduler = Scheduler(
        max_concurrency=args.max_concurrency,
        max_cpu_percent=args.max_cpu_percent,
        max_memory_percent=args.max_memory_percent,
        max_disk_active_percent=args.max_disk_active_percent,
    )
    runner = TaskRunner()
    manager = TaskManager(
        tasks=tasks,
        scheduler=scheduler,
        runner=runner,
        log_dir=log_dir,
        artifact_base_dir=artifact_base_dir,
        scheduler_tick=args.scheduler_tick,
        status_interval=args.status_interval,
        registered_resources=[r.name for r in resource_registry.resources.values()] if resource_registry else None,
        resource_registry=resource_registry,
    )

    monitor = MonitorServer(manager=manager, host=args.monitor_host, port=args.monitor_port)
    monitor.start()

    if args.interactive_cli:
        if sys.stdin.isatty():
            command_thread = threading.Thread(target=_command_loop, args=(manager,), daemon=True)
            command_thread.start()
        else:
            print("[CTRL] interactive CLI requested but stdin is not a TTY; stdin command loop disabled.")

    if not args.tasks_file:
        print("[HOST] No --tasks-file provided. Start with empty task set; use POST /tasks/submit to add tasks.")

    print("[HOST] Initial state is NOT_RUN. Use POST /control/start to begin scheduling.")
    if args.interactive_cli and sys.stdin.isatty():
        print("[HOST] CLI mode is enabled. You can also type 'start' and press Enter.")

    print("[HOST] Resident mode enabled. The process stays alive in NOT_RUN after stop; use shutdown to exit.")

    try:
        exit_code = manager.run()
    finally:
        monitor.stop()

    if args.summary_json:
        summary_path = Path(args.summary_json).resolve()
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(build_summary(manager), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[HOST] Summary written: {summary_path}")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
