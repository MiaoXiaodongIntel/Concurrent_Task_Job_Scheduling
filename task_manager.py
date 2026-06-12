from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from scheduler import Scheduler
from task_runner import RunningTaskHandle, TaskRunner


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class HostState(str, Enum):
    NOT_RUN = "NOT_RUN"
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"
    STOPPING_FORCE = "STOPPING_FORCE"
    STOPPED = "STOPPED"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.STARTING},
    TaskStatus.STARTING: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.ABORTED},
    TaskStatus.RUNNING: {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED},
    TaskStatus.SUCCEEDED: {TaskStatus.QUEUED},
    TaskStatus.FAILED: {TaskStatus.QUEUED},
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
        auto_start: bool,
    ) -> None:
        self.tasks: dict[str, TaskJob] = {task.task_id: task for task in tasks}
        self.queue: list[str] = [task.task_id for task in tasks]
        self.scheduler = scheduler
        self.runner = runner
        self.log_dir = log_dir
        self.scheduler_tick = max(0.2, scheduler_tick)
        self.status_interval = max(0.5, status_interval)

        self.host_state = HostState.RUNNING if auto_start else HostState.NOT_RUN
        self._lock = threading.RLock()
        self._running_handles: dict[str, RunningTaskHandle] = {}
        self._log_files: dict[str, TextIO] = {}
        self._reader_threads: dict[str, list[threading.Thread]] = {}
        self._last_status_emit_monotonic = 0.0

    def start(self) -> bool:
        with self._lock:
            if self.host_state not in {HostState.NOT_RUN, HostState.STOPPED}:
                return False
            self.host_state = HostState.RUNNING
        print("[HOST] start accepted -> state=RUNNING")
        return True

    def graceful_stop(self) -> bool:
        with self._lock:
            if self.host_state != HostState.RUNNING:
                return False
            self.host_state = HostState.DRAINING
        print("[HOST] graceful_stop accepted -> state=DRAINING")
        return True

    def force_stop(self) -> bool:
        with self._lock:
            if self.host_state not in {HostState.RUNNING, HostState.DRAINING}:
                return False
            self.host_state = HostState.STOPPING_FORCE

            # Mark startup-phase tasks as aborted immediately.
            for task in self.tasks.values():
                if task.status == TaskStatus.STARTING:
                    self._set_task_status(task, TaskStatus.ABORTED)
                    task.abort_reason = "force_stop_during_starting"
                    task.ended_at = now_iso()

            handles = list(self._running_handles.items())
            for task_id, handle in handles:
                task = self.tasks[task_id]
                if task.status == TaskStatus.RUNNING:
                    self._set_task_status(task, TaskStatus.ABORTED)
                    task.abort_reason = "force_stop"
                    task.ended_at = now_iso()
                task.pid = handle.process.pid

        for task_id, handle in handles:
            try:
                handle.process.terminate()
            except OSError:
                pass
            print(f"[HOST] force_stop terminating task={task_id} pid={handle.process.pid}")

        print("[HOST] force_stop accepted -> state=STOPPING_FORCE")
        return True

    def rerun(self, task_ids: list[str]) -> tuple[list[str], list[str]]:
        accepted: list[str] = []
        rejected: list[str] = []
        with self._lock:
            for task_id in task_ids:
                task = self.tasks.get(task_id)
                if task is None:
                    rejected.append(task_id)
                    continue
                if task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED}:
                    rejected.append(task_id)
                    continue

                self._set_task_status(task, TaskStatus.QUEUED)
                task.started_at = None
                task.ended_at = None
                task.pid = None
                task.exit_code = None
                task.abort_reason = None
                task.last_output_ts = None
                if task.task_id not in self.queue:
                    self.queue.append(task.task_id)
                accepted.append(task.task_id)

        if accepted:
            print(f"[HOST] rerun accepted tasks={accepted}")
        if rejected:
            print(f"[HOST] rerun rejected tasks={rejected}")
        return accepted, rejected

    def _set_task_status(self, task: TaskJob, new_status: TaskStatus) -> None:
        if task.status == new_status:
            return
        allowed = ALLOWED_TRANSITIONS[task.status]
        if new_status not in allowed:
            raise RuntimeError(
                f"Invalid status transition: {task.task_id} {task.status.value} -> {new_status.value}"
            )
        task.status = new_status

    def control(self, command: str, task_ids: list[str] | None = None) -> dict[str, Any]:
        cmd = command.strip().lower()
        if cmd == "start":
            ok = self.start()
            return {"accepted": ok, "command": "start", "affected_task_ids": []}
        if cmd == "graceful_stop":
            ok = self.graceful_stop()
            return {"accepted": ok, "command": "graceful_stop", "affected_task_ids": []}
        if cmd == "force_stop":
            ok = self.force_stop()
            return {"accepted": ok, "command": "force_stop", "affected_task_ids": []}
        if cmd == "rerun":
            accepted, rejected = self.rerun(task_ids or [])
            return {
                "accepted": bool(accepted),
                "command": "rerun",
                "affected_task_ids": accepted,
                "rejected_task_ids": rejected,
            }
        return {
            "accepted": False,
            "command": cmd,
            "affected_task_ids": [],
            "message": f"Unknown command: {command}",
        }

    def snapshot_health(self) -> dict[str, Any]:
        with self._lock:
            queued = sum(1 for t in self.tasks.values() if t.status == TaskStatus.QUEUED)
            starting = sum(1 for t in self.tasks.values() if t.status == TaskStatus.STARTING)
            running = sum(1 for t in self.tasks.values() if t.status == TaskStatus.RUNNING)
            completed = sum(
                1
                for t in self.tasks.values()
                if t.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED}
            )
            total = len(self.tasks)
            return {
                "host_state": self.host_state.value,
                "queued_count": queued,
                "starting_count": starting,
                "running_count": running,
                "completed_count": completed,
                "total_count": total,
                "last_status_ts": now_iso(),
            }

    def snapshot_metrics(self) -> dict[str, Any]:
        with self._lock:
            succeeded = sum(1 for t in self.tasks.values() if t.status == TaskStatus.SUCCEEDED)
            failed = sum(1 for t in self.tasks.values() if t.status == TaskStatus.FAILED)
            aborted = sum(1 for t in self.tasks.values() if t.status == TaskStatus.ABORTED)
            inflight = sum(
                1
                for t in self.tasks.values()
                if t.status in {TaskStatus.STARTING, TaskStatus.RUNNING}
            )
            return {
                **self.snapshot_health(),
                "succeeded_count": succeeded,
                "failed_count": failed,
                "aborted_count": aborted,
                "inflight_count": inflight,
                "queue_depth": len(self.queue),
            }

    def snapshot_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self.tasks[task_id].to_dict()
                for task_id in sorted(self.tasks.keys())
            ]

    def snapshot_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                return None
            return task.to_dict()

    def read_task_logs(self, task_id: str, cursor: int = 0, limit: int = 200) -> dict[str, Any] | None:
        if limit < 1:
            limit = 1
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                return None
            log_path = task.log_path

        lines: list[str] = []
        if log_path and Path(log_path).exists():
            with Path(log_path).open("r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
                start = max(0, cursor)
                end = min(len(all_lines), start + limit)
                lines = [line.rstrip("\n") for line in all_lines[start:end]]
                next_cursor = end
                eof = end >= len(all_lines)
        else:
            next_cursor = 0
            eof = True

        return {
            "task_id": task_id,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "eof": eof,
            "lines": lines,
        }

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
            if task.status == TaskStatus.ABORTED:
                task.exit_code = exit_code
                if task.ended_at is None:
                    task.ended_at = now_iso()
            else:
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
        with self._lock:
            if self.host_state != HostState.RUNNING:
                return
            task = self.tasks[task_id]
            if task.status != TaskStatus.QUEUED:
                return
            self._set_task_status(task, TaskStatus.STARTING)
            task.started_at = now_iso()
            task.ended_at = None
            task.exit_code = None
            task.abort_reason = None
            task.pid = None

        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / f"{task.task_id}.log"
        task.log_path = str(log_path)
        log_file = log_path.open("a", encoding="utf-8")

        try:
            handle = self.runner.start_task(task.commands)
        except OSError as exc:
            with self._lock:
                task.ended_at = now_iso()
                task.exit_code = -1
                task.abort_reason = f"spawn_failed: {exc}"
                self._set_task_status(task, TaskStatus.FAILED)
            log_file.write(f"{now_iso()} [SYSTEM] spawn failed: {exc}\n")
            log_file.close()
            print(f"[TASK] {task_id} failed to start: {exc}")
            return

        with self._lock:
            task = self.tasks[task_id]
            task.pid = handle.process.pid
            if self.host_state != HostState.RUNNING:
                if task.status == TaskStatus.STARTING:
                    self._set_task_status(task, TaskStatus.ABORTED)
                task.abort_reason = "host_not_running_after_start"
                task.ended_at = now_iso()
                log_file.write(f"{now_iso()} [SYSTEM] aborted before running\n")
                log_file.flush()
                log_file.close()
                self.runner.cleanup(handle)
                try:
                    handle.process.terminate()
                except OSError:
                    pass
                return

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
            running_count = sum(
                1
                for t in self.tasks.values()
                if t.status in {TaskStatus.STARTING, TaskStatus.RUNNING}
            )
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

    def _inflight_count(self) -> int:
        with self._lock:
            return sum(
                1
                for t in self.tasks.values()
                if t.status in {TaskStatus.STARTING, TaskStatus.RUNNING}
            )

    def _advance_host_state(self) -> None:
        with self._lock:
            if self.host_state == HostState.DRAINING and self._inflight_count() == 0:
                self.host_state = HostState.STOPPED
                print("[HOST] draining complete -> state=STOPPED")
            elif self.host_state == HostState.STOPPING_FORCE and self._inflight_count() == 0:
                self.host_state = HostState.STOPPED
                print("[HOST] force stop complete -> state=STOPPED")

    def run(self) -> int:
        print(
            f"[HOST] Starting TaskManager with {len(self.tasks)} tasks, "
            f"max_concurrency={self.scheduler.max_concurrency} initial_state={self.host_state.value}"
        )

        while True:
            self._emit_status_if_due()
            self._try_schedule()
            self._advance_host_state()

            if self._all_done():
                with self._lock:
                    self.host_state = HostState.STOPPED
                break

            time.sleep(self.scheduler_tick)

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
