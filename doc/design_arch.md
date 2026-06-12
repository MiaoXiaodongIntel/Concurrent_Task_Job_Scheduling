# Concurrent Task Job Scheduling - Architecture Overview

## 1. Purpose and Scope

This document is the high-level architecture reference for the concurrent task job scheduling project.

It focuses on:

1. Project-level functional capabilities.
2. Module responsibilities.
3. Navigation links to detailed design documents.

Detailed algorithms, state transition rules, and implementation notes are maintained in module-level documents.

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

- TaskRunner
   - Responsibilities:
      - Executes one task job process.
      - Collects exit code and output streams.
   - Details: [design_task_runner.md](design_task_runner.md)
- TaskManager
   - Responsibilities:
      - Owns task metadata and lifecycle state transitions.
      - Tracks queue/running/completed progress and task snapshots.
   - Details: [design_task_manager.md](design_task_manager.md)
- Scheduler
   - Responsibilities:
      - Chooses when and how many queued jobs to start.
      - Applies concurrency and resource-related admission rules.
   - Details: [design_scheduler.md](design_scheduler.md)
- ControlPlane
   - Responsibilities:
      - Receives runtime control commands.
      - Coordinates host-level stop behavior.
   - Details: [design_control_plane.md](design_control_plane.md)
- MonitorAPI
   - Responsibilities:
      - Exposes host/task status and log query interfaces.
      - Serves as integration boundary for GUI/CLI and automation.
   - Details: [design_monitor_api.md](design_monitor_api.md)

## 4. Acceptance Baseline (Architecture Level)

1. Architecture supports concurrent task job orchestration without changing per-task sequential semantics.
2. Lifecycle and observability contracts are stable and queryable.
3. Module boundaries are clear enough for independent implementation and future extension.

---

Last updated: 2026-06-12
