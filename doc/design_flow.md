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

