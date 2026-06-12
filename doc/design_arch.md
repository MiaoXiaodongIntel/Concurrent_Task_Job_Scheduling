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
4. Lifecycle governance: maintain host-level and task-level lifecycle states.
5. Controlled admission: scheduler admits queued jobs under configurable constraints.
6. Stop control: support force stop and graceful stop semantics.
7. Monitoring integration: provide stable status/log interfaces for CLI/GUI/API consumers.

## 3. High-Level Component View

- TaskHost
   - Responsibilities:
      - Parses CLI arguments and loads task definitions.
      - Wires `Scheduler`, `TaskRunner`, and `TaskManager` as composition root.
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
      - Tracks queue/running/completed progress and task snapshots.
   - Details: [design_task_manager.md](design_task_manager.md)
   - Implementation: [../task_manager.py](../task_manager.py)
- Scheduler
   - Responsibilities:
      - Chooses when and how many queued jobs to start.
      - Applies concurrency and resource-related admission rules.
   - Details: [design_scheduler.md](design_scheduler.md)
   - Implementation: [../scheduler.py](../scheduler.py)
- ControlPlane
   - Responsibilities:
      - Receives runtime control commands.
      - Coordinates host-level stop behavior.
   - Details: [design_control_plane.md](design_control_plane.md)
   - Implementation status: planned (not implemented as independent module yet)
- MonitorAPI
   - Responsibilities:
      - Exposes host/task status and log query interfaces.
      - Serves as integration boundary for GUI/CLI and automation.
   - Details: [design_monitor_api.md](design_monitor_api.md)
   - Implementation status: planned (not implemented as independent module yet)

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

## 5. Acceptance Baseline (Architecture Level)

1. Architecture supports concurrent task job orchestration without changing per-task sequential semantics.
2. Lifecycle and observability contracts are stable and queryable.
3. Module boundaries are clear enough for independent implementation and future extension.

---

Last updated: 2026-06-12
