# Concurrent Task Job Scheduling Design Document

## 1. Background and Goals

The existing script [run_powershell_session.py](run_powershell_session.py) supports sequential execution of multiple commands within a single execution channel and returns the final output and exit code. The requirements have now expanded to include concurrent task jobs, host resource protection, observability, interruption control, and GUI integration.

This design document consolidates all currently confirmed requirements and serves as the baseline for implementation.

## 2. Current Capabilities (Already Implemented)

Based on [run_powershell_session.py](run_powershell_session.py), the following are already implemented:

1. Sequential execution of multiple commands within a single task job (passed by repeating `--command`).
2. Shared execution context for commands (working directory, environment variables, function context).
3. Non-zero exit code propagation when a sub-command fails.
4. Full stdout/stderr and final return code available to the parent process.

## 3. Overall Requirement Summary

### 3.1 Task Job Execution Model

1. Support concurrent execution of multiple independent task jobs.
2. The execution context of each task job must be fully isolated.
3. Commands inside each task job must remain sequential.

### 3.2 Observability and Real-Time Output

1. Each task job must provide real-time output so humans can judge whether execution is normal.
2. The host process must be able to monitor the status of all task jobs.
3. Status and logs should be displayable in a GUI in the future (HTML or Python GUI, to be decided).

### 3.3 Host Resource Protection

1. Users may submit many tasks at once (for example, 100).
2. The system must not start all tasks immediately; it must dynamically admit jobs based on Windows host resources.
3. The target is to keep the host responsive and avoid noticeable lag.

### 3.4 Stop Control (Host-Process Level)

Two stop semantics must be supported:

1. Force Stop
   1. Stop scheduling immediately.
   2. Force-terminate all running child task jobs.
   3. Mark not-yet-started queued jobs as `aborted`.
   4. Mark forcibly terminated running jobs as `aborted`.
2. Graceful Stop (Drain Stop)
   1. Do not start any new task jobs.
   2. Wait for currently running task jobs to complete.
   3. Exit the host process after all running jobs finish.

## 4. Design Principles

1. Stability first: task failures, encoding anomalies, and process errors must be controlled.
2. Observability first: statuses must be traceable, logs replayable, and events subscribable.
3. Resource-friendly behavior: dynamically balance throughput and host responsiveness.
4. Extensibility: enable smooth future integration with GUI, persistence, and access control.

## 5. Recommended Architecture

## 5.1 Components

1. TaskRunner
   1. Responsible for starting a single task job, collecting logs, and retrieving exit codes.
2. TaskManager
   1. Responsible for task job lifecycle management and state machine maintenance.
3. Scheduler (resource-aware scheduler)
   1. Responsible for queue admission, concurrency control, and batched throttling.
4. ControlPlane
   1. Receives host-process control commands (`force_stop` / `graceful_stop`).
5. MonitorAPI
   1. Provides status and log query interfaces for GUI or CLI monitoring.

## 5.2 Data Model

Suggested fields for the task model (TaskJob):

1. `task_id`
2. `commands`
3. `status`
4. `created_at`
5. `started_at`
6. `ended_at`
7. `pid`
8. `exit_code`
9. `abort_reason`
10. `last_output_ts`
11. `log_path`

Suggested fields for the host-process model (HostRuntime):

1. `host_state` (`RUNNING` / `DRAINING` / `STOPPING_FORCE` / `STOPPED`)
2. `queue_size`
3. `running_count`
4. `completed_count`
5. `cpu_percent`
6. `memory_percent`
7. `disk_busy_percent`
8. `scheduler_tick_ts`

## 6. State Machine Design

### 6.1 Host Process State Machine

1. `RUNNING`: new jobs can be scheduled.
2. `DRAINING`: no new jobs are started; wait for running jobs to finish.
3. `STOPPING_FORCE`: terminate all running jobs and clear the queue.
4. `STOPPED`: host process has ended.

### 6.2 Task Job State Machine

1. `queued`
2. `starting`
3. `running`
4. `succeeded`
5. `failed`
6. `aborted`

## 7. Resource-Aware Scheduling Strategy

## 7.1 Basic Strategy

1. All user-submitted jobs first enter `queued`.
2. The scheduler evaluates whether new task jobs can be started on a periodic tick (recommended: every 1-2 seconds).
3. The number of newly started jobs is jointly constrained by:
   1. Hard concurrency cap `max_concurrency_hard`.
   2. CPU, memory, and disk thresholds.
   3. Startup throttling (at most N jobs per tick).

## 7.2 Suggested Default Thresholds (Configurable)

1. `max_concurrency_hard = 8`
2. `cpu_percent < 75`
3. `memory_percent < 80`
4. `disk_busy_percent < 70`
5. Start at most `1~2` jobs per scheduling tick.
6. Pause admission during sustained high pressure and gradually increase throughput during sustained low pressure.

## 7.3 Anti-Lag Mechanisms

1. Reserve host headroom (for example, at least 2 logical cores and memory equivalent to 2GB).
2. Enforce per-task startup timeout to avoid blocking scheduler slots.
3. Reclaim resources quickly from abnormal task jobs through fast failure.

## 8. Stop Behavior Definition

### 8.1 Force Stop

1. Host state switches to `STOPPING_FORCE`.
2. All queued task jobs are marked `aborted`.
3. All running task jobs are force-terminated (Windows recommendation: `taskkill /PID <pid> /T /F`).
4. Host process switches to `STOPPED` after cleanup is complete.

### 8.2 Graceful Stop

1. Host state switches to `DRAINING`.
2. Starting new jobs is disabled.
3. Wait until `running_count == 0`.
4. Host process switches to `STOPPED` and exits.

## 9. Monitoring and GUI Integration Requirements

Provide at least the following minimal interfaces (HTTP or local IPC, final choice pending):

1. `GET /health`: host-process liveness and state.
2. `GET /metrics`: resource and concurrency metrics.
3. `GET /tasks`: list and status of all task jobs.
4. `GET /tasks/{id}`: single task job details.
5. `GET /tasks/{id}/logs?cursor=...`: incremental log stream.
6. `POST /control/graceful-stop`.
7. `POST /control/force-stop`.

Minimum GUI display capabilities:

1. Host-process state.
2. Queue length, running count, completed count.
3. Real-time output and final status per task job.
4. Manual stop actions (force / graceful).

## 10. Relationship to Existing Script

1. [run_powershell_session.py](run_powershell_session.py) can be retained as the single-task execution kernel.
2. A new multi-task scheduling host process is required as the orchestration layer.
3. In the future, it is recommended to extract reusable execution logic into shared modules to reduce duplicate implementation.

Additional notes:

1. A PowerShell session is one underlying implementation method for executing a task job, not a high-level business abstraction.
2. If execution backends change in the future (for example, cmd, containers, or remote executors), the upper-layer task job model should remain semantically unchanged.

## 11. Non-Functional Requirements

1. Log traceability: each task job must have an independent log file.
2. Fault recovery: support restoring task snapshots after host-process crashes (optional phase 2).
3. Security: control interfaces must be restricted to local access or protected by authentication.
4. Testability: cover state machine logic, scheduling admission, stop semantics, and log streaming.

## 12. Milestone Suggestions

1. M1 (minimum viable)
   1. Multi-task concurrency + state machine + real-time log output.
2. M2
   1. Resource-aware scheduling + anti-lag strategy.
3. M3
   1. End-to-end force/graceful stop flows + task-state persistence.
4. M4
   1. GUI integration (choose one: HTML or Python GUI).

## 13. Acceptance Criteria

1. Submitting 100 jobs does not trigger instant full startup.
2. Concurrency is automatically reduced under high resource pressure while the host remains responsive.
3. Real-time logs are visible for each task job so humans can identify abnormalities.
4. Force Stop can clear running jobs within bounded time and mark them `aborted`.
5. Graceful Stop blocks new jobs and waits for running jobs to finish naturally.
6. Host-process state and task states are queryable through stable interfaces.

---

Last updated: 2026-06-11 13:35:11 +08:00
