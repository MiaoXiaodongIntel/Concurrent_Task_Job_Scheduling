from __future__ import annotations

import ctypes
import os
import random
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

from scheduler import ResourceUsage, Scheduler
from task_runner import RunningTaskHandle, TaskRunner


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class HostState(str, Enum):
    NOT_RUN = "NOT_RUN"
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"
    STOPPING_FORCE = "STOPPING_FORCE"
    SHUTTING_DOWN = "SHUTTING_DOWN"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.STARTING, TaskStatus.PENDING},
    TaskStatus.PENDING: {TaskStatus.QUEUED, TaskStatus.ABORTED},
    TaskStatus.STARTING: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.ABORTED},
    TaskStatus.RUNNING: {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED},
    TaskStatus.SUCCEEDED: {TaskStatus.QUEUED},
    TaskStatus.FAILED: {TaskStatus.QUEUED},
    TaskStatus.ABORTED: {TaskStatus.QUEUED},
}


@dataclass
class RunRecord:
    """Immutable snapshot of one completed execution of a TaskJob."""

    run_index: int
    started_at: str | None
    ended_at: str | None
    exit_code: int | None
    status: str          # terminal status value: succeeded / failed / aborted
    log_path: str | None # per-run system log (stdout/stderr captured by host)
    artifact_dir: str | None  # tool-specific artifact directory (e.g. Kayak log dir)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_index": self.run_index,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_code": self.exit_code,
            "status": self.status,
            "log_path": self.log_path,
            "artifact_dir": self.artifact_dir,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> "RunRecord":
        return cls(
            run_index=int(d["run_index"]),  # type: ignore[arg-type]
            started_at=d.get("started_at"),  # type: ignore[arg-type]
            ended_at=d.get("ended_at"),  # type: ignore[arg-type]
            exit_code=d.get("exit_code"),  # type: ignore[arg-type]
            status=str(d.get("status", "")),
            log_path=d.get("log_path"),  # type: ignore[arg-type]
            artifact_dir=d.get("artifact_dir"),  # type: ignore[arg-type]
        )


@dataclass
class TaskJob:
    task_id: str
    commands: list[str]
    resource: str = ""
    config_id: int = 0
    assigned_resource: str | None = None
    resolved_commands: list[str] | None = None
    priority: int = 100
    status: TaskStatus = TaskStatus.QUEUED
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    ended_at: str | None = None
    pid: int | None = None
    exit_code: int | None = None
    abort_reason: str | None = None
    last_output_ts: str | None = None
    log_path: str | None = None
    artifact_dir: str | None = None
    blocked_by: str | None = None
    run_index: int = 0
    run_history: list[RunRecord] = field(default_factory=list)

    def to_dict(self, include_history: bool = False) -> dict[str, object]:
        d: dict[str, object] = {
            "task_id": self.task_id,
            "resource": self.resource,
            "config_id": self.config_id,
            "assigned_resource": self.assigned_resource,
            "resolved_commands": self.resolved_commands,
            "priority": self.priority,
            "commands": self.commands,
            "status": self.status.value,
            "blocked_by": self.blocked_by,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "abort_reason": self.abort_reason,
            "last_output_ts": self.last_output_ts,
            "log_path": self.log_path,
            "artifact_dir": self.artifact_dir,
            "run_index": self.run_index,
        }
        if include_history:
            d["run_history"] = [r.to_dict() for r in self.run_history]
        return d


class _SystemResourceProbe:
    def __init__(self, base_path: Path) -> None:
        self._base_path = base_path
        self._last_cpu_sample: tuple[int, int] | None = None
        self._disk_active_percent = 0.0
        self._disk_lock = threading.Lock()

        if os.name == "nt":
            sampler = threading.Thread(
                target=self._disk_active_sampler_loop,
                daemon=True,
                name="disk-active-sampler",
            )
            sampler.start()

    def snapshot(self) -> ResourceUsage:
        return ResourceUsage(
            cpu_percent=self._get_cpu_percent(),
            memory_percent=self._get_memory_percent(),
            disk_active_percent=self._get_disk_active_percent(),
        )

    def _get_cpu_percent(self) -> float:
        if os.name != "nt":
            return 0.0

        class _FileTime(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", ctypes.c_uint32),
                ("dwHighDateTime", ctypes.c_uint32),
            ]

        idle = _FileTime()
        kernel = _FileTime()
        user = _FileTime()

        ok = ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return 0.0

        idle_ticks = (idle.dwHighDateTime << 32) + idle.dwLowDateTime
        kernel_ticks = (kernel.dwHighDateTime << 32) + kernel.dwLowDateTime
        user_ticks = (user.dwHighDateTime << 32) + user.dwLowDateTime
        total_ticks = kernel_ticks + user_ticks

        if self._last_cpu_sample is None:
            self._last_cpu_sample = (idle_ticks, total_ticks)
            return 0.0

        last_idle, last_total = self._last_cpu_sample
        self._last_cpu_sample = (idle_ticks, total_ticks)

        delta_total = total_ticks - last_total
        delta_idle = idle_ticks - last_idle
        if delta_total <= 0:
            return 0.0

        busy = 1.0 - (delta_idle / delta_total)
        return min(100.0, max(0.0, busy * 100.0))

    def _get_memory_percent(self) -> float:
        if os.name == "nt":
            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_uint32),
                    ("dwMemoryLoad", ctypes.c_uint32),
                    ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64),
                    ("ullTotalPageFile", ctypes.c_uint64),
                    ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64),
                    ("ullAvailVirtual", ctypes.c_uint64),
                    ("ullAvailExtendedVirtual", ctypes.c_uint64),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            if ok:
                return min(100.0, max(0.0, float(status.dwMemoryLoad)))

        return 0.0

    def _disk_active_sampler_loop(self) -> None:
        while True:
            active = self._read_disk_active_once()
            with self._disk_lock:
                self._disk_active_percent = active
            time.sleep(2.0)

    def _read_disk_active_once(self) -> float:
        if os.name != "nt":
            return 0.0

        try:
            result = subprocess.run(
                [
                    "typeperf",
                    r"\PhysicalDisk(_Total)\% Disk Time",
                    "-sc",
                    "1",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return 0.0

        if result.returncode != 0:
            return 0.0

        numeric = re.findall(r"[-+]?\d+(?:[\.,]\d+)?", result.stdout)
        if not numeric:
            return 0.0

        raw = numeric[-1].replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            return 0.0

        return min(100.0, max(0.0, value))

    def _get_disk_active_percent(self) -> float:
        with self._disk_lock:
            return self._disk_active_percent


class TaskManager:
    def __init__(
        self,
        tasks: list[TaskJob],
        scheduler: Scheduler,
        runner: TaskRunner,
        log_dir: Path,
        scheduler_tick: float,
        status_interval: float,
        registered_resources: list[str] | None = None,
        artifact_base_dir: Path | None = None,
        resource_registry: Any | None = None,
    ) -> None:
        self.tasks: dict[str, TaskJob] = {task.task_id: task for task in tasks}
        # Queue is sorted by (priority asc, created_at asc)
        self.queue: list[str] = sorted(
            [task.task_id for task in tasks],
            key=lambda tid: (self.tasks[tid].priority, self.tasks[tid].created_at),
        )
        self.scheduler = scheduler
        self.runner = runner
        self.log_dir = log_dir
        self.artifact_base_dir = artifact_base_dir
        self.scheduler_tick = max(0.2, scheduler_tick)
        self.status_interval = max(0.5, status_interval)

        self.host_state = HostState.NOT_RUN
        self._lock = threading.RLock()
        self._running_handles: dict[str, RunningTaskHandle] = {}
        self._log_files: dict[str, TextIO] = {}
        self._reader_threads: dict[str, list[threading.Thread]] = {}
        self._last_status_emit_monotonic = 0.0
        self._resource_probe = _SystemResourceProbe(base_path=log_dir)
        self._shutdown_requested = False
        self._shutdown_mode: str = "drain"
        self._shutdown_deadline_monotonic: float | None = None
        self._shutdown_force_applied = False

        # Resource registry (loaded once, immutable after loading)
        self._registered_resources: list[str] = list(registered_resources) if registered_resources else []
        self._resources_loaded: bool = bool(registered_resources)
        self._registered_resources_set: set[str] = set(self._registered_resources)

        # Resource conflict tracking
        self._resource_lock: dict[str, str] = {}  # resource_id -> task_id holding lock
        self._pending_by_resource: dict[str, list[str]] = {}  # resource_id -> [task_id, ...]
        self._pending_by_config: dict[int, list[str]] = {}  # config_id -> [task_id, ...]

        # Optional resource registry injected by task_host wiring step.
        self._resource_registry: Any | None = resource_registry

    @staticmethod
    def _build_task_from_payload(
        item: object,
        fallback_index: int,
        registered_resources: set[str] | None = None,
        registered_config_ids: set[int] | None = None,
    ) -> TaskJob:
        if not isinstance(item, dict):
            raise ValueError(f"task index {fallback_index} is not an object")

        maybe_id = item.get("task_id")
        task_id = str(maybe_id).strip() if maybe_id is not None else f"job-{fallback_index:03d}"
        if not task_id:
            task_id = f"job-{fallback_index:03d}"

        commands_raw = item.get("commands")
        if not isinstance(commands_raw, list) or not commands_raw:
            raise ValueError(f"task {task_id} must include non-empty 'commands' list")

        commands: list[str] = []
        for command in commands_raw:
            if not isinstance(command, str) or not command.strip():
                raise ValueError(f"task {task_id} has invalid command: {command!r}")
            commands.append(command)

        resource_raw = item.get("resource", "")
        resource = resource_raw.strip() if isinstance(resource_raw, str) else ""

        config_id_raw = item.get("config_id", 0)
        config_id = 0
        if config_id_raw not in (None, ""):
            try:
                config_id = int(config_id_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"task {task_id} has invalid config_id: {config_id_raw!r}") from exc
            if config_id < 0:
                raise ValueError(f"task {task_id} has non-positive config_id: {config_id}")

        if config_id > 0:
            if registered_config_ids is not None and config_id not in registered_config_ids:
                raise ValueError(f"task {task_id} references unregistered config_id: {config_id}")
        else:
            if not resource:
                raise ValueError(
                    f"task {task_id} must have either a non-empty 'resource' string field or a positive 'config_id'"
                )
            if registered_resources is not None and resource not in registered_resources:
                raise ValueError(
                    f"task {task_id} references unregistered resource: {resource!r}"
                )

        priority_raw = item.get("priority")
        if priority_raw is None:
            raise ValueError(f"task {task_id} must have a 'priority' integer field")
        try:
            priority = int(priority_raw)
            if priority < 1:
                raise ValueError("priority must be positive")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"task {task_id} 'priority' must be a positive integer: {exc}"
            ) from exc

        return TaskJob(
            task_id=task_id,
            commands=commands,
            resource=resource,
            config_id=config_id,
            priority=priority,
        )

    @staticmethod
    def _parse_task_payload(
        tasks_payload: object,
        registered_resources: set[str] | None = None,
        registered_config_ids: set[int] | None = None,
    ) -> list[TaskJob]:
        if not isinstance(tasks_payload, list) or not tasks_payload:
            raise ValueError("tasks must be a non-empty list")

        built: list[TaskJob] = []
        seen_ids: set[str] = set()
        for idx, item in enumerate(tasks_payload, start=1):
            task = TaskManager._build_task_from_payload(
                item,
                idx,
                registered_resources,
                registered_config_ids,
            )
            if task.task_id in seen_ids:
                raise ValueError(f"duplicate task_id in payload: {task.task_id}")
            seen_ids.add(task.task_id)
            built.append(task)
        return built

    def start(self) -> bool:
        with self._lock:
            if self._shutdown_requested:
                return False
            if self.host_state != HostState.NOT_RUN:
                return False
            self.host_state = HostState.RUNNING
        print("[HOST] start accepted -> state=RUNNING")
        return True

    def graceful_stop(self) -> bool:
        with self._lock:
            if self._shutdown_requested:
                return False
            if self.host_state != HostState.RUNNING:
                return False
            self.host_state = HostState.DRAINING
        print("[HOST] graceful_stop accepted -> state=DRAINING")
        return True

    def force_stop(self) -> bool:
        with self._lock:
            if self._shutdown_requested:
                return False
            if self.host_state not in {HostState.RUNNING, HostState.DRAINING}:
                return False
            self.host_state = HostState.STOPPING_FORCE

            # Abort pending tasks immediately (no process to terminate).
            for task in self.tasks.values():
                if task.status == TaskStatus.PENDING:
                    self._set_task_status(task, TaskStatus.ABORTED)
                    task.abort_reason = "force_stop"
                    task.blocked_by = None
                    task.ended_at = now_iso()
            self._pending_by_resource.clear()
            self._pending_by_config.clear()

            # Mark startup-phase tasks as aborted immediately.
            for task in self.tasks.values():
                if task.status == TaskStatus.STARTING:
                    self._set_task_status(task, TaskStatus.ABORTED)
                    task.abort_reason = "force_stop_during_starting"
                    task.ended_at = now_iso()

            # Clear resource locks (all locks are released by force-stop).
            self._resource_lock.clear()

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

    def shutdown(self, mode: str = "drain", timeout_sec: float | None = None) -> tuple[bool, str]:
        requested_mode = mode.strip().lower() if mode else "drain"
        if requested_mode not in {"drain", "force"}:
            return False, "invalid_shutdown_mode"

        with self._lock:
            if self._shutdown_requested:
                return False, "shutdown_already_requested"
            if self.host_state != HostState.NOT_RUN:
                return False, "host_not_in_not_run"

            self._shutdown_requested = True
            self._shutdown_mode = requested_mode
            self._shutdown_force_applied = False
            self._shutdown_deadline_monotonic = None
            self.host_state = HostState.SHUTTING_DOWN

        print(f"[HOST] shutdown accepted -> state=SHUTTING_DOWN")
        return True, "accepted"

    def _abort_inflight_locked(self, reason: str) -> None:
        # Abort pending tasks (no process to kill).
        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING:
                self._set_task_status(task, TaskStatus.ABORTED)
                task.abort_reason = reason
                task.blocked_by = None
                task.ended_at = now_iso()
        self._pending_by_resource.clear()
        self._pending_by_config.clear()
        self._resource_lock.clear()

        for task in self.tasks.values():
            if task.status == TaskStatus.STARTING:
                self._set_task_status(task, TaskStatus.ABORTED)
                task.abort_reason = f"{reason}_during_starting"
                task.ended_at = now_iso()

        handles = list(self._running_handles.items())
        for task_id, handle in handles:
            task = self.tasks[task_id]
            if task.status == TaskStatus.RUNNING:
                self._set_task_status(task, TaskStatus.ABORTED)
                task.abort_reason = reason
                task.ended_at = now_iso()
            task.pid = handle.process.pid

        for task_id, handle in handles:
            try:
                handle.process.terminate()
            except OSError:
                pass
            print(f"[HOST] terminating task={task_id} pid={handle.process.pid} reason={reason}")

    def submit_tasks(self, tasks_payload: object, submit_mode: str = "append") -> dict[str, Any]:
        mode = submit_mode.strip().lower() if isinstance(submit_mode, str) else "append"
        if mode not in {"append", "replace"}:
            return {
                "accepted": False,
                "submit_mode": mode,
                "accepted_task_ids": [],
                "reason_code": "invalid_submit_mode",
                "message": "submit_mode must be append or replace",
            }

        try:
            parsed_tasks = self._parse_task_payload(
                tasks_payload,
                self._registered_resources_set,
                set(self._resource_registry.configs.keys()) if self._resource_registry is not None else None,
            )
        except ValueError as exc:
            return {
                "accepted": False,
                "submit_mode": mode,
                "accepted_task_ids": [],
                "reason_code": "invalid_task_payload",
                "message": str(exc),
            }

        with self._lock:
            if self._shutdown_requested:
                return {
                    "accepted": False,
                    "submit_mode": mode,
                    "accepted_task_ids": [],
                    "reason_code": "host_shutting_down",
                    "message": "cannot submit tasks while shutting down",
                }

            inflight = self._inflight_count()
            pending_count = sum(1 for t in self.tasks.values() if t.status == TaskStatus.PENDING)
            if mode == "replace" and (inflight > 0 or pending_count > 0):
                return {
                    "accepted": False,
                    "submit_mode": mode,
                    "accepted_task_ids": [],
                    "reason_code": "inflight_exists",
                    "message": "replace is rejected while tasks are running, starting, or pending",
                }

            if mode == "append":
                duplicates = [task.task_id for task in parsed_tasks if task.task_id in self.tasks]
                if duplicates:
                    return {
                        "accepted": False,
                        "submit_mode": mode,
                        "accepted_task_ids": [],
                        "reason_code": "duplicate_task_id",
                        "message": f"duplicate task_id exists: {duplicates}",
                    }
                for task in parsed_tasks:
                    self.tasks[task.task_id] = task
                    self._insert_queue_sorted(task.task_id)
            else:
                self.tasks = {task.task_id: task for task in parsed_tasks}
                self.queue = sorted(
                    [task.task_id for task in parsed_tasks],
                    key=lambda tid: (self.tasks[tid].priority, self.tasks[tid].created_at),
                )
                self._running_handles.clear()
                self._reader_threads.clear()
                self._log_files.clear()
                self._resource_lock.clear()
                self._pending_by_resource.clear()
                self._pending_by_config.clear()

        accepted_task_ids = [task.task_id for task in parsed_tasks]
        print(f"[HOST] submit accepted mode={mode} tasks={accepted_task_ids}")
        return {
            "accepted": True,
            "submit_mode": mode,
            "accepted_task_ids": accepted_task_ids,
            "reason_code": "accepted",
            "message": "tasks accepted",
        }

    def abort_task(self, task_id: str) -> dict[str, Any]:
        """Abort a single running or pending task by task_id without affecting host or sibling tasks."""
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                return {
                    "accepted": False,
                    "task_id": task_id,
                    "reason_code": "task_not_found",
                    "message": f"task not found: {task_id}",
                }
            if task.status == TaskStatus.PENDING:
                # Remove from pending index; no process to terminate.
                if task.config_id > 0:
                    pending_list = self._pending_by_config.get(task.config_id, [])
                    if task_id in pending_list:
                        pending_list.remove(task_id)
                else:
                    resource = task.resource
                    pending_list = self._pending_by_resource.get(resource, [])
                    if task_id in pending_list:
                        pending_list.remove(task_id)
                self._set_task_status(task, TaskStatus.ABORTED)
                task.abort_reason = "user_abort"
                task.blocked_by = None
                task.ended_at = now_iso()
                print(f"[HOST] abort_task accepted (pending) task={task_id}")
                return {
                    "accepted": True,
                    "task_id": task_id,
                    "reason_code": "accepted",
                    "message": f"task {task_id} aborted (was pending)",
                }
            if task.status != TaskStatus.RUNNING:
                return {
                    "accepted": False,
                    "task_id": task_id,
                    "reason_code": "task_not_abortable",
                    "message": f"task {task_id} is not running or pending (status={task.status.value})",
                }
            handle = self._running_handles.get(task_id)
            self._set_task_status(task, TaskStatus.ABORTED)
            task.abort_reason = "user_abort"
            task.ended_at = now_iso()

        if handle is not None:
            try:
                handle.process.terminate()
            except OSError:
                pass
            print(f"[HOST] abort_task task={task_id} pid={handle.process.pid}")

        print(f"[HOST] abort_task accepted task={task_id}")
        return {
            "accepted": True,
            "task_id": task_id,
            "reason_code": "accepted",
            "message": f"task {task_id} aborted",
        }

    def rerun(self, task_ids: list[str]) -> tuple[list[str], list[str]]:
        accepted: list[str] = []
        rejected: list[str] = []
        with self._lock:
            for task_id in task_ids:
                task = self.tasks.get(task_id)
                if task is None:
                    rejected.append(task_id)
                    continue
                if task.status not in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED}:
                    rejected.append(task_id)
                    continue

                # Archive current run into run_history before resetting.
                record = RunRecord(
                    run_index=task.run_index,
                    started_at=task.started_at,
                    ended_at=task.ended_at,
                    exit_code=task.exit_code,
                    status=task.status.value,
                    log_path=task.log_path,
                    artifact_dir=task.artifact_dir,
                )
                task.run_history.append(record)
                task.run_index += 1

                self._set_task_status(task, TaskStatus.QUEUED)
                task.started_at = None
                task.ended_at = None
                task.pid = None
                task.exit_code = None
                task.abort_reason = None
                task.last_output_ts = None
                task.log_path = None
                task.artifact_dir = None
                task.blocked_by = None
                accepted.append(task.task_id)

            # Rerun tasks are appended to the tail sorted among themselves by
            # (priority, created_at) — they do not jump ahead of waiting queued tasks.
            if accepted:
                accepted_sorted = sorted(
                    accepted,
                    key=lambda tid: (self.tasks[tid].priority, self.tasks[tid].created_at),
                )
                for tid in accepted_sorted:
                    if tid not in self.queue:
                        self.queue.append(tid)

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

    def _insert_queue_sorted(self, task_id: str) -> None:
        """Insert task_id into the queue at the correct position by (priority, created_at)."""
        task = self.tasks[task_id]
        key = (task.priority, task.created_at)
        for i, tid in enumerate(self.queue):
            other = self.tasks[tid]
            if key < (other.priority, other.created_at):
                self.queue.insert(i, task_id)
                return
        self.queue.append(task_id)

    def _insert_pending_sorted(self, resource: str, task_id: str) -> None:
        """Insert task_id into pending_by_resource[resource] sorted by (priority, created_at)."""
        pending_list = self._pending_by_resource.setdefault(resource, [])
        task = self.tasks[task_id]
        key = (task.priority, task.created_at)
        for i, tid in enumerate(pending_list):
            other = self.tasks[tid]
            if key < (other.priority, other.created_at):
                pending_list.insert(i, task_id)
                return
        pending_list.append(task_id)

    def _insert_pending_by_config_sorted(self, config_id: int, task_id: str) -> None:
        """Insert task_id into pending_by_config[config_id] sorted by (priority, created_at)."""
        pending_list = self._pending_by_config.setdefault(config_id, [])
        task = self.tasks[task_id]
        key = (task.priority, task.created_at)
        for i, tid in enumerate(pending_list):
            other = self.tasks[tid]
            if key < (other.priority, other.created_at):
                pending_list.insert(i, task_id)
                return
        pending_list.append(task_id)

    def _wake_pending_for_resource(self, resource: str) -> None:
        """Promote the single highest-priority pending task for a resource back to queued.
        Called while holding self._lock.
        """
        pending_list = self._pending_by_resource.get(resource, [])
        if not pending_list:
            return

        # All tasks at the front share the minimum priority; choose one randomly among ties.
        min_prio = self.tasks[pending_list[0]].priority
        same_prio = [tid for tid in pending_list if self.tasks[tid].priority == min_prio]
        chosen = random.choice(same_prio)

        pending_list.remove(chosen)

        task = self.tasks[chosen]
        task.blocked_by = None
        self._set_task_status(task, TaskStatus.QUEUED)
        self._insert_queue_sorted(chosen)
        print(f"[HOST] resource {resource!r} released -> promoted pending task {chosen} to queued")

    def _wake_pending_for_config(self, config_id: int) -> bool:
        """Promote one pending task waiting on a config pool.

        Returns True when a task is promoted, else False.
        Called while holding self._lock.
        """
        pending_list = self._pending_by_config.get(config_id, [])
        if not pending_list:
            return False

        chosen = pending_list.pop(0)
        task = self.tasks[chosen]
        task.blocked_by = None
        self._set_task_status(task, TaskStatus.QUEUED)
        self._insert_queue_sorted(chosen)
        print(f"[HOST] config_id {config_id} released -> promoted pending task {chosen} to queued")
        return True

    def _config_id_of_resource(self, resource_name: str) -> int | None:
        registry = self._resource_registry
        if registry is None:
            return None
        resource_id = registry.resource_name_index.get(resource_name)
        if resource_id is None:
            return None
        return int(registry.resources[resource_id].config_id)

    def _wake_pending_for_released_resource(self, resource_name: str) -> None:
        """Wake pending waiters when a resource is released.

        Config-pool path is preferred when the released resource maps to a
        config_id in the loaded registry. If no config waiter is found, fall
        back to the legacy resource-specific queue.
        """
        config_id = self._config_id_of_resource(resource_name)
        if config_id is not None and self._wake_pending_for_config(config_id):
            return
        self._wake_pending_for_resource(resource_name)

    def _convert_all_pending_to_queued_locked(self) -> None:
        """Batch-convert all pending tasks to queued (used when DRAINING -> NOT_RUN).
        Called while holding self._lock.
        """
        for task in self.tasks.values():
            if task.status == TaskStatus.PENDING:
                task.blocked_by = None
                self._set_task_status(task, TaskStatus.QUEUED)
                self._insert_queue_sorted(task.task_id)
        self._pending_by_resource.clear()
        self._pending_by_config.clear()

    def _pick_free_resource_from_registry(self, config_id: int, claimed: set[str]) -> str | None:
        """Pick a free resource name for the given config_id from loaded registry."""
        registry = self._resource_registry
        if registry is None:
            return None
        for resource_id in registry.resources_by_config.get(config_id, []):
            resource_name = registry.resources[resource_id].name
            if resource_name in claimed:
                continue
            if resource_name in self._resource_lock:
                continue
            return resource_name
        return None

    def _find_holder_for_pending_task(
        self,
        task: TaskJob,
        to_start: list[tuple[str, str]],
    ) -> str | None:
        """Find the task currently blocking a pending task."""
        # Config-pool mode: any lock holder (or same-tick starter) on same config.
        if task.config_id > 0:
            for holder in self._resource_lock.values():
                holder_task = self.tasks.get(holder)
                if holder_task is not None and holder_task.config_id == task.config_id:
                    return holder
            for starter_id, _assigned in to_start:
                starter_task = self.tasks.get(starter_id)
                if starter_task is not None and starter_task.config_id == task.config_id:
                    return starter_id
            return None

        # Legacy path: blocked by the specific resource owner.
        holder = self._resource_lock.get(task.resource)
        if holder is not None:
            return holder
        return next(
            (
                starter_id
                for starter_id, assigned_resource in to_start
                if assigned_resource == task.resource
            ),
            None,
        )

    def load_resources(self, resources: list[str]) -> dict[str, Any]:
        """Register the resource list. Accepted only when host is NOT_RUN and not yet loaded."""
        with self._lock:
            if self.host_state != HostState.NOT_RUN:
                return {
                    "accepted": False,
                    "reason_code": "invalid_host_state",
                    "message": "resources can only be loaded when host is NOT_RUN",
                }
            if self._resources_loaded:
                return {
                    "accepted": False,
                    "reason_code": "already_loaded",
                    "message": "resources have already been loaded",
                }
            if not resources:
                return {
                    "accepted": False,
                    "reason_code": "empty_resources",
                    "message": "resources list must not be empty",
                }

            # Deduplicate while preserving order.
            seen: set[str] = set()
            deduped: list[str] = []
            for r in resources:
                if not isinstance(r, str) or not r.strip():
                    return {
                        "accepted": False,
                        "reason_code": "empty_resources",
                        "message": "all resource identifiers must be non-empty strings",
                    }
                if r not in seen:
                    seen.add(r)
                    deduped.append(r)

            self._registered_resources = deduped
            self._registered_resources_set = set(deduped)
            self._resources_loaded = True

        print(f"[HOST] resources loaded: {deduped}")
        return {
            "accepted": True,
            "reason_code": "accepted",
            "message": f"loaded {len(deduped)} resources",
        }

    def snapshot_resources(self) -> dict[str, Any]:
        """Return the current resource registry and occupancy status."""
        with self._lock:
            resources = []
            for resource in self._registered_resources:
                held_by = self._resource_lock.get(resource)
                pending_for = list(self._pending_by_resource.get(resource, []))
                resources.append({
                    "resource": resource,
                    "status": "occupied" if held_by else "free",
                    "held_by": held_by,
                    "pending_tasks": pending_for,
                })
            return {
                "loaded": self._resources_loaded,
                "resources": resources,
            }

    def control(
        self,
        command: str,
        task_ids: list[str] | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cmd = command.strip().lower()
        if cmd == "start":
            ok = self.start()
            return {
                "accepted": ok,
                "command": "start",
                "affected_task_ids": [],
                "message": "accepted" if ok else "ignored: host state not startable",
                "reason_code": "accepted" if ok else "invalid_state",
            }
        if cmd == "graceful_stop":
            ok = self.graceful_stop()
            return {
                "accepted": ok,
                "command": "graceful_stop",
                "affected_task_ids": [],
                "message": "accepted" if ok else "ignored: host is not RUNNING",
                "reason_code": "accepted" if ok else "invalid_state",
            }
        if cmd == "force_stop":
            ok = self.force_stop()
            return {
                "accepted": ok,
                "command": "force_stop",
                "affected_task_ids": [],
                "message": "accepted" if ok else "ignored: host is not RUNNING or DRAINING",
                "reason_code": "accepted" if ok else "invalid_state",
            }
        if cmd == "rerun":
            accepted, rejected = self.rerun(task_ids or [])
            return {
                "accepted": bool(accepted),
                "command": "rerun",
                "affected_task_ids": accepted,
                "rejected_task_ids": rejected,
                "message": "accepted" if accepted else "ignored: no succeeded/failed/aborted task selected",
                "reason_code": "accepted" if accepted else "no_eligible_task",
            }
        if cmd == "abort_task":
            t_ids = task_ids or []
            if not t_ids:
                return {
                    "accepted": False,
                    "command": "abort_task",
                    "affected_task_ids": [],
                    "message": "no task_id provided",
                    "reason_code": "task_not_found",
                }
            result_inner = self.abort_task(t_ids[0])
            return {
                "accepted": result_inner["accepted"],
                "command": "abort_task",
                "affected_task_ids": [t_ids[0]] if result_inner["accepted"] else [],
                "message": result_inner["message"],
                "reason_code": result_inner["reason_code"],
            }
        if cmd == "shutdown":
            opts = options or {}
            mode_raw = opts.get("mode", "drain")
            mode = str(mode_raw).strip().lower()
            timeout_raw = opts.get("timeout_sec")

            timeout_sec: float | None = None
            if timeout_raw is not None:
                try:
                    timeout_sec = float(timeout_raw)
                except (TypeError, ValueError):
                    return {
                        "accepted": False,
                        "command": "shutdown",
                        "affected_task_ids": [],
                        "message": "ignored: timeout_sec must be a number",
                        "reason_code": "invalid_timeout",
                    }

            ok, reason = self.shutdown(mode=mode, timeout_sec=timeout_sec)
            if ok:
                message = "accepted"
            elif reason == "invalid_shutdown_mode":
                message = "ignored: mode must be drain or force"
            elif reason == "host_not_in_not_run":
                message = "ignored: shutdown only allowed from NOT_RUN state"
            else:
                message = "ignored: shutdown already requested"

            return {
                "accepted": ok,
                "command": "shutdown",
                "affected_task_ids": [],
                "message": message,
                "reason_code": "accepted" if ok else reason,
            }
        return {
            "accepted": False,
            "command": cmd,
            "affected_task_ids": [],
            "message": f"Unknown command: {command}",
            "reason_code": "unknown_command",
        }

    def snapshot_health(self) -> dict[str, Any]:
        resource = self._resource_probe.snapshot()
        with self._lock:
            queued = sum(1 for t in self.tasks.values() if t.status == TaskStatus.QUEUED)
            pending = sum(1 for t in self.tasks.values() if t.status == TaskStatus.PENDING)
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
                "pending_count": pending,
                "starting_count": starting,
                "running_count": running,
                "completed_count": completed,
                "total_count": total,
                "last_status_ts": now_iso(),
                "cpu_percent": round(resource.cpu_percent, 1),
                "memory_percent": round(resource.memory_percent, 1),
                "disk_active_percent": round(resource.disk_active_percent, 1),
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
            return task.to_dict(include_history=True)

    def read_task_logs(self, task_id: str, cursor: int = 0, limit: int = 200, run_index: int | None = None) -> dict[str, Any] | None:
        if limit < 1:
            limit = 1
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                return None
            if run_index is None:
                # Current run
                log_path = task.log_path
            elif run_index == task.run_index:
                # Explicitly requested current run index
                log_path = task.log_path
            else:
                # Historical run: look up in run_history
                record = next((r for r in task.run_history if r.run_index == run_index), None)
                if record is None:
                    return {"task_id": task_id, "cursor": cursor, "next_cursor": 0, "eof": True, "lines": [], "run_index": run_index, "error": "run_not_found"}
                log_path = record.log_path

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
            "run_index": run_index if run_index is not None else task.run_index,
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
            pending = sum(1 for t in self.tasks.values() if t.status == TaskStatus.PENDING)
            starting = sum(1 for t in self.tasks.values() if t.status == TaskStatus.STARTING)
            running = sum(1 for t in self.tasks.values() if t.status == TaskStatus.RUNNING)
            done = sum(
                1
                for t in self.tasks.values()
                if t.status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.ABORTED}
            )

        print(
            "[HOST] "
            f"state={self.host_state.value} queued={queued} pending={pending} starting={starting} "
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

            # Release resource lock and wake the highest-priority pending task.
            resource = task.assigned_resource or task.resource
            self._resource_lock.pop(resource, None)
            self._wake_pending_for_released_resource(resource)

        print(f"[TASK] {task_id} finished with exit_code={exit_code} status={task.status.value}")

    def _start_task(self, task_id: str, assigned_resource: str = "") -> None:
        with self._lock:
            if self.host_state != HostState.RUNNING:
                return
            task = self.tasks[task_id]
            if task.status != TaskStatus.QUEUED:
                return
            task.assigned_resource = assigned_resource or task.resource
            self._set_task_status(task, TaskStatus.STARTING)
            task.started_at = now_iso()
            task.ended_at = None
            task.exit_code = None
            task.abort_reason = None
            task.blocked_by = None
            task.pid = None
            # Write resource lock at STARTING to prevent same-tick double-admission.
            lock_resource = task.assigned_resource or task.resource
            if lock_resource:
                self._resource_lock[lock_resource] = task_id

        try:
            if task.assigned_resource:
                task.resolved_commands = self._render_commands(task.commands, task.assigned_resource)
            else:
                task.resolved_commands = None
        except ValueError as exc:
            with self._lock:
                task.ended_at = now_iso()
                task.exit_code = -1
                task.abort_reason = f"render_failed: {exc}"
                self._set_task_status(task, TaskStatus.FAILED)
                lock_resource = task.assigned_resource or task.resource
                self._resource_lock.pop(lock_resource, None)
                self._wake_pending_for_released_resource(lock_resource)
            print(f"[TASK] {task_id} command render failed: {exc}")
            return

        self.log_dir.mkdir(parents=True, exist_ok=True)
        task_log_dir = self.log_dir / task.task_id
        task_log_dir.mkdir(parents=True, exist_ok=True)
        log_path = task_log_dir / f"run_{task.run_index}.log"
        task.log_path = str(log_path)

        # Compute artifact_dir for this run and expand {ARTIFACT_DIR} in commands.
        # The directory is only created (and the placeholder only matters) when the
        # task's commands actually reference {ARTIFACT_DIR}.  This ensures non-Kayak
        # tasks produce no artifact directories even when artifact_base_dir is set.
        artifact_dir: str | None = None
        commands_to_run = task.resolved_commands if task.resolved_commands is not None else task.commands
        needs_artifact = any("{ARTIFACT_DIR}" in cmd for cmd in commands_to_run)
        if needs_artifact and self.artifact_base_dir is not None:
            artifact_path = self.artifact_base_dir / task.task_id / f"run_{task.run_index}"
            artifact_path.mkdir(parents=True, exist_ok=True)
            artifact_dir = str(artifact_path)
        task.artifact_dir = artifact_dir

        # Build the effective command list with placeholder substituted.
        effective_commands = commands_to_run
        if artifact_dir is not None:
            effective_commands = [cmd.replace("{ARTIFACT_DIR}", artifact_dir) for cmd in commands_to_run]

        log_file = log_path.open("a", encoding="utf-8")

        try:
            handle = self.runner.start_task(effective_commands)
        except OSError as exc:
            with self._lock:
                task.ended_at = now_iso()
                task.exit_code = -1
                task.abort_reason = f"spawn_failed: {exc}"
                self._set_task_status(task, TaskStatus.FAILED)
                # Release resource lock so pending tasks can proceed (§2.4: lock released
                # when a task enters any terminal state).
                lock_resource = task.assigned_resource or task.resource
                self._resource_lock.pop(lock_resource, None)
                self._wake_pending_for_released_resource(lock_resource)
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

    def _render_commands(self, commands: list[str], resource_name: str) -> list[str]:
        """Render resource placeholders with strict validation.

        Supported placeholders:
        - {resource.name}
        - {resource.properties.<key>}

        The legacy {ARTIFACT_DIR} placeholder is preserved for _start_task to
        resolve later in the launch flow.
        """
        registry = self._resource_registry
        if registry is None:
            return list(commands)

        resource_id = registry.resource_name_index.get(resource_name)
        if resource_id is None:
            raise ValueError(f"assigned resource not found in registry: {resource_name!r}")
        props = registry.resources[resource_id].properties

        token_pattern = re.compile(r"\{([^{}]+)\}")

        def replace_token(match: re.Match[str], command: str) -> str:
            token = match.group(1)
            if token == "resource.name":
                return resource_name
            if token == "ARTIFACT_DIR":
                return "{ARTIFACT_DIR}"
            prop_prefix = "resource.properties."
            if token.startswith(prop_prefix):
                prop_key = token[len(prop_prefix):]
                if prop_key in props:
                    return str(props[prop_key])
                raise ValueError(
                    f"Unknown placeholder {{{token}}} in command: {command!r}"
                )
            raise ValueError(f"Unknown placeholder {{{token}}} in command: {command!r}")

        rendered: list[str] = []
        for command in commands:
            rendered_command = token_pattern.sub(lambda m: replace_token(m, command), command)
            rendered.append(rendered_command)
        return rendered

    def _try_schedule(self) -> None:
        with self._lock:
            running_count = sum(
                1
                for t in self.tasks.values()
                if t.status in {TaskStatus.STARTING, TaskStatus.RUNNING}
            )
            to_start, to_pending = self.scheduler.pick_next_tasks(
                queue=self.queue,
                running_count=running_count,
                host_running=self.host_state == HostState.RUNNING,
                is_runnable=lambda task_id: self.tasks[task_id].status == TaskStatus.QUEUED,
                get_resource_usage=self._resource_probe.snapshot,
                get_task_resource=lambda task_id: self.tasks[task_id].resource,
                is_resource_free=lambda resource: resource not in self._resource_lock,
                get_task_config=(
                    (lambda task_id: self.tasks[task_id].config_id)
                    if self._resource_registry is not None
                    else None
                ),
                pick_free_resource=(
                    self._pick_free_resource_from_registry
                    if self._resource_registry is not None
                    else None
                ),
            )
            # Immediately mark pending tasks with the blocking task_id.
            for task_id in to_pending:
                task = self.tasks[task_id]
                self._set_task_status(task, TaskStatus.PENDING)
                task.blocked_by = self._find_holder_for_pending_task(task, to_start)
                if task.config_id > 0:
                    self._insert_pending_by_config_sorted(task.config_id, task_id)
                    print(
                        f"[TASK] {task_id} -> pending (config_id={task.config_id} "
                        f"blocked_by={task.blocked_by!r})"
                    )
                else:
                    self._insert_pending_sorted(task.resource, task_id)
                    print(
                        f"[TASK] {task_id} -> pending (resource={task.resource!r} "
                        f"blocked_by={task.blocked_by!r})"
                    )

        for task_id, assigned_resource in to_start:
            with self._lock:
                if self.host_state != HostState.RUNNING:
                    break
            self._start_task(task_id, assigned_resource)

    def _all_done(self) -> bool:
        with self._lock:
            return not self.queue and self._inflight_count() == 0

    def _inflight_count(self) -> int:
        status_inflight = sum(
            1
            for t in self.tasks.values()
            if t.status in {TaskStatus.STARTING, TaskStatus.RUNNING}
        )
        # During force-stop, task status may transition to aborted before watcher
        # threads finish process wait/cleanup; running handles still represent
        # in-flight work that must complete before host can be considered settled.
        return max(status_inflight, len(self._running_handles))

    def _advance_host_state(self) -> None:
        with self._lock:
            inflight = self._inflight_count()
            if self.host_state == HostState.DRAINING and inflight == 0:
                # Batch-convert all pending tasks to queued before leaving DRAINING.
                self._convert_all_pending_to_queued_locked()
                self.host_state = HostState.NOT_RUN
                print("[HOST] draining complete -> state=NOT_RUN")
                return
            if self.host_state == HostState.STOPPING_FORCE and inflight == 0:
                self.host_state = HostState.NOT_RUN
                print("[HOST] force stop complete -> state=NOT_RUN")

    def run(self) -> int:
        print(
            f"[HOST] Starting TaskManager with {len(self.tasks)} tasks, "
            f"max_concurrency={self.scheduler.max_concurrency} initial_state={self.host_state.value}"
        )

        while True:
            self._emit_status_if_due()
            self._try_schedule()
            self._advance_host_state()

            with self._lock:
                should_exit = self._shutdown_requested and self._inflight_count() == 0
            if should_exit:
                break

            time.sleep(self.scheduler_tick)

        self._emit_status_if_due()

        failed = [t for t in self.tasks.values() if t.status == TaskStatus.FAILED]
        aborted = [t for t in self.tasks.values() if t.status == TaskStatus.ABORTED]

        print("[HOST] Shutdown completed.")
        print(
            "[HOST] Final summary: "
            f"succeeded={sum(1 for t in self.tasks.values() if t.status == TaskStatus.SUCCEEDED)} "
            f"failed={len(failed)} aborted={len(aborted)}"
        )

        self._print_task_table()
        return 0

    def _print_task_table(self) -> None:
        print("[HOST] Task results:")
        for task in sorted(self.tasks.values(), key=lambda t: t.task_id):
            print(
                "  - "
                f"task_id={task.task_id} status={task.status.value} exit_code={task.exit_code} "
                f"pid={task.pid} log_path={task.log_path}"
            )
