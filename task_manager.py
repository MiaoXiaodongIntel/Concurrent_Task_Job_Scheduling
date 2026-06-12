from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TextIO

from scheduler import Scheduler
from task_runner import RunningTaskHandle, TaskRunner


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class HostState(str, Enum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.STARTING, TaskStatus.ABORTED},
    TaskStatus.STARTING: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.ABORTED},
    TaskStatus.RUNNING: {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.ABORTED: set(),
}


@dataclass
class TaskJob:
    task_id: str
    commands: list[str]
    status: TaskStatus = TaskStatus.QUEUED
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    ended_at: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    abort_reason: str | None = None
    last_output_ts: str | None = None
    log_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "commands": self.commands,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "abort_reason": self.abort_reason,
            "last_output_ts": self.last_output_ts,
            "log_path": self.log_path,
        }


class TaskManager:
    def __init__(
        self,
        tasks: list[TaskJob],
        scheduler: Scheduler,
        runner: TaskRunner,
        log_dir: Path,
        scheduler_tick: float,
        status_interval: float,
    ) -> None:
        self.tasks: dict[str, TaskJob] = {task.task_id: task for task in tasks}
        self.queue: list[str] = [task.task_id for task in tasks]
        self.scheduler = scheduler
        self.runner = runner
        self.log_dir = log_dir
        self.scheduler_tick = max(0.2, scheduler_tick)
        self.status_interval = max(0.5, status_interval)

        self.host_state = HostState.RUNNING
        self._lock = threading.RLock()
        self._running_handles: dict[str, RunningTaskHandle] = {}
        self._log_files: dict[str, TextIO] = {}
        self._reader_threads: dict[str, list[threading.Thread]] = {}
        self._last_status_emit_monotonic = 0.0

    def _set_task_status(self, task: TaskJob, new_status: TaskStatus) -> None:
        if task.status == new_status:
            return
        allowed = ALLOWED_TRANSITIONS[task.status]
        if new_status not in allowed:
            raise RuntimeError(
                f"Invalid status transition: {task.task_id} {task.status.value} -> {new_status.value}"
            )
        task.status = new_status

    def _emit_status_if_due(self) -> None:
        now_mono = time.monotonic()
        if (now_mono - self._last_status_emit_monotonic) < self.status_interval:
            return

        with self._lock:
            queued = sum(1 for t in self.tasks.values() if t.status == TaskStatus.QUEUED)
            starting = sum(1 for t in self.tasks.values() if t.status == TaskStatus.STARTING)
            running = sum(1 for t in self.tasks.values() if t.status == TaskStatus.RUNNING)
            done = sum(
                1
                for t in self.tasks.values()
                if t.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED}
            )

        print(
            "[HOST] "
            f"state={self.host_state.value} queued={queued} starting={starting} "
            f"running={running} completed={done}/{len(self.tasks)}"
        )
        self._last_status_emit_monotonic = now_mono

    def _write_task_log(self, task_id: str, line: str, stream_name: str) -> None:
        with self._lock:
            task = self.tasks[task_id]
            task.last_output_ts = now_iso()
            log_file = self._log_files[task_id]
            stamped = f"{task.last_output_ts} [{stream_name}] {line}"
            log_file.write(stamped)
            log_file.flush()

        print(f"[{task_id}][{stream_name}] {line}", end="")

    def _stream_reader(self, task_id: str, stream: TextIO, stream_name: str) -> None:
        try:
            for line in iter(stream.readline, ""):
                if not line:
                    break
                self._write_task_log(task_id, line, stream_name)
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _watch_task(self, task_id: str) -> None:
        with self._lock:
            handle = self._running_handles[task_id]

        exit_code = handle.process.wait()

        with self._lock:
            reader_threads = self._reader_threads.get(task_id, [])
        for reader in reader_threads:
            reader.join(timeout=2)

        self.runner.cleanup(handle)

        with self._lock:
            task = self.tasks[task_id]
            task.exit_code = exit_code
            task.ended_at = now_iso()
            self._set_task_status(
                task,
                TaskStatus.SUCCEEDED if exit_code == 0 else TaskStatus.FAILED,
            )

            self._running_handles.pop(task_id, None)
            self._reader_threads.pop(task_id, None)

            logf = self._log_files.pop(task_id, None)
            if logf is not None:
                logf.flush()
                logf.close()

        print(f"[TASK] {task_id} finished with exit_code={exit_code} status={task.status.value}")

    def _start_task(self, task_id: str) -> None:
        task = self.tasks[task_id]
        self._set_task_status(task, TaskStatus.STARTING)
        task.started_at = now_iso()

        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{task.task_id}.log"
        task.log_path = str(log_path)
        log_file = log_path.open("a", encoding="utf-8")

        try:
            handle = self.runner.start_task(task.commands)
        except OSError as exc:
            task.ended_at = now_iso()
            task.exit_code = -1
            task.abort_reason = f"spawn_failed: {exc}"
            self._set_task_status(task, TaskStatus.FAILED)
            log_file.write(f"{now_iso()} [SYSTEM] spawn failed: {exc}\n")
            log_file.close()
            print(f"[TASK] {task_id} failed to start: {exc}")
            return

        task.pid = handle.process.pid
        self._set_task_status(task, TaskStatus.RUNNING)
        self._running_handles[task_id] = handle
        self._log_files[task_id] = log_file

        stdout_thread = threading.Thread(
            target=self._stream_reader,
            args=(task_id, handle.process.stdout, "STDOUT"),
            daemon=True,
            name=f"reader-stdout-{task_id}",
        )
        stderr_thread = threading.Thread(
            target=self._stream_reader,
            args=(task_id, handle.process.stderr, "STDERR"),
            daemon=True,
            name=f"reader-stderr-{task_id}",
        )
        self._reader_threads[task_id] = [stdout_thread, stderr_thread]

        stdout_thread.start()
        stderr_thread.start()

        watcher = threading.Thread(
            target=self._watch_task,
            args=(task_id,),
            daemon=True,
            name=f"watcher-{task_id}",
        )
        watcher.start()

        print(f"[TASK] {task_id} started pid={handle.process.pid} log={task.log_path}")

    def _try_schedule(self) -> None:
        with self._lock:
            running_count = sum(1 for t in self.tasks.values() if t.status == TaskStatus.RUNNING)
            to_start = self.scheduler.pick_next_tasks(
                queue=self.queue,
                running_count=running_count,
                host_running=self.host_state == HostState.RUNNING,
                is_runnable=lambda task_id: self.tasks[task_id].status == TaskStatus.QUEUED,
            )

        for task_id in to_start:
            with self._lock:
                if self.host_state != HostState.RUNNING:
                    break
            self._start_task(task_id)

    def _all_done(self) -> bool:
        with self._lock:
            for task in self.tasks.values():
                if task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED}:
                    return False
        return True

    def run(self) -> int:
        print(
            f"[HOST] Starting TaskManager with {len(self.tasks)} tasks, "
            f"max_concurrency={self.scheduler.max_concurrency}"
        )

        while True:
            self._emit_status_if_due()
            self._try_schedule()

            if self._all_done():
                break

            time.sleep(self.scheduler_tick)

        with self._lock:
            self.host_state = HostState.STOPPED

        self._emit_status_if_due()

        failed = [t for t in self.tasks.values() if t.status == TaskStatus.FAILED]
        aborted = [t for t in self.tasks.values() if t.status == TaskStatus.ABORTED]

        print("[HOST] All tasks completed.")
        print(
            "[HOST] Summary: "
            f"succeeded={sum(1 for t in self.tasks.values() if t.status == TaskStatus.SUCCEEDED)} "
            f"failed={len(failed)} aborted={len(aborted)}"
        )

        self._print_task_table()
        return 1 if failed or aborted else 0

    def _print_task_table(self) -> None:
        print("[HOST] Task results:")
        for task in sorted(self.tasks.values(), key=lambda t: t.task_id):
            print(
                "  - "
                f"task_id={task.task_id} status={task.status.value} exit_code={task.exit_code} "
                f"pid={task.pid} log_path={task.log_path}"
            )
