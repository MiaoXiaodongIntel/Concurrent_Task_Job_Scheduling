# Design Flow Log

## Metadata

- Document purpose: Track requirement and design evolution for future review.
- Project scope: Concurrent task job scheduling and monitoring on Windows host.

## Index

1. [Change Log Rules](#change-log-rules)
2. [Entry 01 - Execution Model Baseline](#entry-01)
3. [Entry 02 - Shell Backend Decision](#entry-02)
4. [Entry 03 - Multi-Task Requirement Expansion](#entry-03)
5. [Entry 04 - Resource-Aware Admission Requirement](#entry-04)
6. [Entry 05 - Host Stop Semantics Requirement](#entry-05)
7. [Entry 06 - Architecture Documentation Split](#entry-06)
8. [Entry 07 - Runtime Module Boundary Refactor](#entry-07)
9. [Entry 08 - Design-to-Implementation Traceability](#entry-08)
10. [Entry 09 - Host Lifecycle and Rerun Extension](#entry-09)
11. [Entry 10 - Force-Stop Escalation Refinement](#entry-10)
12. [Entry 11 - API-First Control Channel](#entry-11)
13. [Entry 12 - Resource Admission Guardrails Implemented](#entry-12)

## Change Log Rules

Each entry uses:
1. Change summary
2. Entry type
3. Original design -> New design
4. Why improved

---

## Entry 01

- Change summary: Established the initial execution approach for running multiple commands in one shared execution context.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Long one-line command chaining in shell.
  - New: Generate a PowerShell script and execute all steps in one process/session, returning final stdout/stderr/exit code to parent process.
- Why improved:
  - Better readability and maintainability.
  - Deterministic error propagation.
  - Cleaner parent-process integration for CI or orchestration.

## Entry 02

- Change summary: Technology decision for shell backend.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Possible use of native Windows cmd.
  - New: Use PowerShell as the primary execution backend.
- Why improved:
  - Stronger scripting model and error handling.
  - Better extensibility for future orchestration and monitoring.
  - Better fit for complex multi-step task jobs.

## Entry 03

- Change summary: Expanded from single task execution to system-level orchestration needs.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: Single task runner focus.
  - New: Multi-task concurrent execution with strict task isolation, real-time monitoring, and GUI-ready observability.
- Why improved:
  - Matches real usage where many task jobs are submitted in parallel.
  - Enables operator visibility and intervention.

## Entry 04

- Change summary: Added host resource-aware admission control.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: Potentially start all submitted jobs immediately.
  - New: Queue-first model with dynamic admission based on host CPU/memory/disk pressure and concurrency limits.
- Why improved:
  - Protects Windows host responsiveness.
  - Prevents overload when users submit large batches (for example, 100 jobs).

## Entry 05

- Change summary: Added host-process stop semantics.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: No explicit host-level stop policy.
  - New: Two explicit controls:
    - Force stop: terminate running child tasks and mark task status as aborted.
    - Graceful stop: stop admitting new tasks, wait for running tasks to complete, then exit host process.
- Why improved:
  - Operational safety for emergency and maintenance scenarios.
  - Predictable lifecycle control for scheduler process.

## Entry 06

- Change summary: Reorganized design documentation into high-level architecture and module-focused detail documents.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Mixed architecture and module-level detail in a single evolving document flow.
  - New: `design_arch.md` keeps high-level intent while module-specific decisions are separated into dedicated documents (`design_task_manager.md`, `design_scheduler.md`, `design_task_runner.md`, and related docs).
- Why improved:
  - Improves readability for different audiences by separating overview from implementation-level design.
  - Reduces coupling between architecture narrative and module evolution.

## Entry 07

- Change summary: Standardized runtime architecture into composition root plus three core execution modules.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Task orchestration behavior concentrated in a single host-oriented script concept.
  - New: `task_host` is the composition root, with distinct module boundaries for admission (`Scheduler`), lifecycle/state machine (`TaskManager`), and execution backend (`TaskRunner`).
- Why improved:
  - Clarifies ownership of scheduling, state transitions, and process execution.
  - Enables independent evolution and testing of each runtime concern.

## Entry 08

- Change summary: Added explicit design-to-implementation traceability between architecture documents and source modules.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Design navigation existed, but mapping from design decisions to concrete source files was implicit.
  - New: Architecture and module docs now include direct links between each design area and its implementation file, including the TaskHost design document.
- Why improved:
  - Speeds up review by making design claims verifiable in code.
  - Reduces drift risk between documentation and implementation.

## Entry 09

- Change summary: Extended lifecycle control with paused startup mode and explicit rerun behavior.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: Host effectively started work immediately and manual control focused on graceful/force stop only.
  - New:
    - Host FSM is standardized to five states: `NOT_RUN`, `RUNNING`, `DRAINING`, `STOPPING_FORCE`, `STOPPED`.
    - Default startup mode is `NOT_RUN`; explicit `start` command transitions host to `RUNNING`.
    - Optional `--auto-start` supports immediate run mode for debug scenarios.
    - `rerun` command requeues selected `succeeded|failed` tasks back to `queued`.
    - Under `graceful_stop`, unadmitted tasks remain `queued` and can resume after a later `start`.
- Why improved:
  - Separates task submission from execution start for safer operator control.
  - Enables controlled replay of completed/failed tasks without rebuilding task definitions.

## Entry 10

- Change summary: Refined force-stop policy to support immediate escalation during draining and startup-phase abort handling.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Force-stop behavior was defined mainly from `RUNNING`, with ambiguous treatment for `DRAINING` and `starting` tasks.
  - New:
    - Host FSM explicitly allows `DRAINING -> STOPPING_FORCE` when user issues `force_stop`.
    - Force-stop contract explicitly allows `starting -> aborted` when startup cannot complete due to forced interruption.
    - Force-stop continues to keep `queued` tasks in `queued` for later resume.
- Why improved:
  - Removes ambiguity in interruption semantics during drain windows.
  - Makes state-machine behavior deterministic for edge phases (`starting`, `DRAINING`).

## Entry 11

- Change summary: Switched TaskHost control model to API-first with optional interactive CLI input.
- Entry type: Design Modification
- Original design -> New design:
  - Original: TaskHost always started a stdin command thread for `start/graceful_stop/force_stop/rerun` control.
  - New:
    - Monitor API is the default control surface for runtime orchestration.
    - Interactive stdin command loop is enabled only when `--interactive-cli` is explicitly provided.
    - When host is `NOT_RUN`, startup guidance points to `POST /control/start`, while CLI mode remains available for terminal workflows.
- Why improved:
  - Improves GUI integration by removing dependence on terminal stdin semantics.
  - Keeps CLI ergonomics without forcing dual-control behavior in non-interactive deployments.

## Entry 12

- Change summary: Implemented resource-aware admission guardrails in runtime scheduler path.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Architecture required resource-aware admission, but concrete runtime behavior was concurrency-only in implementation.
  - New:
    - Scheduler now receives optional host resource usage snapshots per scheduling tick.
    - Admission is suspended when any configured threshold is reached (`cpu`, `memory`, `disk_active_time`).
    - TaskHost exposes runtime knobs: `--max-cpu-percent`, `--max-memory-percent`, `--max-disk-active-percent`.
    - GUI-friendly defaults are applied: `cpu=75`, `memory=75`, `disk_active_time=80`.
    - TaskManager provides host resource sampling and passes it into scheduler admission.
- Why improved:
  - Removes architecture-to-implementation drift for resource admission requirements.
  - Provides explicit operational knobs to tune throughput vs host protection.

