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
import sys
import threading
from pathlib import Path

from monitor_api import MonitorServer
from scheduler import Scheduler
from task_manager import TaskJob, TaskManager, TaskStatus
from task_runner import TaskRunner


def load_tasks(tasks_file: Path) -> list[TaskJob]:
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

        tasks.append(TaskJob(task_id=task_id, commands=commands))

    if not tasks:
        raise ValueError("No tasks found in tasks file")
    return tasks


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
        "--max-concurrency",
        type=int,
        default=2,
        help="Maximum concurrent running task jobs (default: 2)",
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

    tasks: list[TaskJob] = []
    if args.tasks_file:
        tasks_file = Path(args.tasks_file).resolve()
        try:
            tasks = load_tasks(tasks_file)
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
        scheduler_tick=args.scheduler_tick,
        status_interval=args.status_interval,
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

    print("[HOST] Resident mode enabled. The process stays alive in IDLE until shutdown is requested.")

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
