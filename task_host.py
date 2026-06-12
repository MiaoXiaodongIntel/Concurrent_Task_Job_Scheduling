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
from pathlib import Path

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
    parser.add_argument("--tasks-file", required=True, help="Path to JSON task definition file")
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=2,
        help="Maximum concurrent running task jobs (default: 2)",
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
    return parser.parse_args()


def build_summary(manager: TaskManager) -> dict[str, object]:
    tasks = [task.to_dict() for task in sorted(manager.tasks.values(), key=lambda t: t.task_id)]
    return {
        "host_state": manager.host_state.value,
        "queue_size": sum(1 for t in manager.tasks.values() if t.status == TaskStatus.QUEUED),
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

    tasks_file = Path(args.tasks_file).resolve()
    log_dir = Path(args.log_dir).resolve()

    try:
        tasks = load_tasks(tasks_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Failed to load tasks file: {exc}", file=sys.stderr)
        return 2

    scheduler = Scheduler(max_concurrency=args.max_concurrency)
    runner = TaskRunner()
    manager = TaskManager(
        tasks=tasks,
        scheduler=scheduler,
        runner=runner,
        log_dir=log_dir,
        scheduler_tick=args.scheduler_tick,
        status_interval=args.status_interval,
    )

    exit_code = manager.run()

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
