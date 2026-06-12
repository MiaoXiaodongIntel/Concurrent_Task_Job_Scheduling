# Concurrent Task Job Scheduling - Architecture Overview

## 1. Purpose and Scope

This document is the high-level architecture reference for the concurrent task job scheduling project.

It focuses on:

1. Project-level functional capabilities.
2. Module responsibilities.
3. Explicit mapping between design docs and current implementation files.

Detailed algorithms and code-level contracts are maintained in module-level documents.

## 2. Project Functional Capabilities

The project provides the following core capabilities:

1. Multi-task job orchestration: run multiple independent task jobs concurrently.
2. In-task sequential execution: commands inside one task job remain sequential.
3. Real-time observability: stream task outputs and expose runtime status.
4. Lifecycle governance: define a unified task lifecycle model and transition invariants.
5. Automatic lifecycle progression: scheduler and runner events drive normal state changes (for example `running -> succeeded|failed`).
6. Manual lifecycle intervention: control commands drive human-triggered transitions (for example stop, start/resume, and rerun), including `starting|running -> aborted` under force-stop policy.
7. Rerun lifecycle operation: user can rerun `succeeded|failed` tasks by moving them back to `queued`.
8. Monitoring integration: provide stable status/log interfaces for CLI/GUI/API consumers.

## 3. High-Level Component View

- TaskHost
   - Responsibilities:
      - Parses CLI arguments and loads task definitions.
      - Wires `Scheduler`, `TaskRunner`, and `TaskManager` as composition root.
   - Wires `MonitorAPI` as primary runtime control/read channel.
   - Optionally enables stdin command loop for interactive CLI mode.
      - Emits final JSON summary when requested.
   - Details: [design_task_host.md](design_task_host.md)
   - Implementation: [../task_host.py](../task_host.py)
- TaskRunner
   - Responsibilities:
      - Executes one task job process.
      - Collects exit code and output streams.
   - Details: [design_task_runner.md](design_task_runner.md)
   - Implementation: [../task_runner.py](../task_runner.py)
- TaskManager
   - Responsibilities:
      - Owns task metadata and lifecycle state transitions.
      - Commits both automatic and manual transition results under one validation model.
      - Owns task requeue semantics for rerun (`succeeded|failed -> queued`).
      - Tracks queue/running/completed progress and task snapshots.
   - Details: [design_task_manager.md](design_task_manager.md)
   - Implementation: [../task_manager.py](../task_manager.py)
- Scheduler
   - Responsibilities:
      - Chooses when and how many queued jobs to start.
      - Produces admission decisions only, without directly mutating terminal states.
      - Applies concurrency and resource-related admission rules.
   - Details: [design_scheduler.md](design_scheduler.md)
   - Implementation: [../scheduler.py](../scheduler.py)
- ControlPlane
   - Responsibilities:
      - Receives runtime control commands.
      - Owns human-triggered stop/abort orchestration path.
      - Owns host start/resume and rerun command orchestration.
      - Coordinates host-level stop behavior.
   - Details: [design_control_plane.md](design_control_plane.md)
   - Implementation: integrated in [../task_manager.py](../task_manager.py) and [../task_host.py](../task_host.py)
- MonitorAPI
   - Responsibilities:
      - Exposes host/task status and log query interfaces.
      - Serves as integration boundary for GUI/CLI and automation.
   - Details: [design_monitor_api.md](design_monitor_api.md)
   - Implementation: [../monitor_api.py](../monitor_api.py), wired by [../task_host.py](../task_host.py)

## 4. Design-to-Implementation Traceability

1. Entry point and wiring:
   - Doc: [design_task_host.md](design_task_host.md)
   - Code: [../task_host.py](../task_host.py)
2. Admission policy:
   - Doc: [design_scheduler.md](design_scheduler.md)
   - Code: [../scheduler.py](../scheduler.py)
3. Lifecycle state machine and orchestration loop:
   - Doc: [design_task_manager.md](design_task_manager.md)
   - Code: [../task_manager.py](../task_manager.py)
4. Process execution and PowerShell script materialization:
   - Doc: [design_task_runner.md](design_task_runner.md)
   - Code: [../task_runner.py](../task_runner.py)
5. Manual stop and abort orchestration:
   - Doc: [design_control_plane.md](design_control_plane.md)
   - Code: integrated in [../task_manager.py](../task_manager.py) and [../task_host.py](../task_host.py)
6. Rerun requeue operation:
   - Doc: [design_control_plane.md](design_control_plane.md), [design_task_manager.md](design_task_manager.md)
   - Code: [../task_manager.py](../task_manager.py)
7. Monitor read/control API surface:
   - Doc: [design_monitor_api.md](design_monitor_api.md)
   - Code: [../monitor_api.py](../monitor_api.py)


---

Last updated: 2026-06-13
