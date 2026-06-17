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
14. [Entry 13 - Resident Host and Unified Shutdown Model](#entry-13)
14. [Entry 14 - Startup Contract Simplification](#entry-14)
15. [Entry 15 - Host State Machine IDLE Removal](#entry-15)
16. [Entry 16 - Task Aborted State Made Recoverable](#entry-16)
17. [Entry 17 - Web GUI UX Conventions Established](#entry-17)
18. [Entry 18 - Host Commands Consolidated into Dashboard](#entry-18)
19. [Entry 19 - Per-Task User Abort Requirement Added](#entry-19)
20. [Entry 20 - Remote Resource Conflict Detection Requirement](#entry-20)
21. [Entry 21 - Per-Run Artifact Directory Requirement and Injection Design](#entry-21)
22. [Entry 22 - Per-Run Execution History Model](#entry-22)
23. [Entry 23 - Task Detail Selection Switches on Change](#entry-23)
24. [Entry 24 - Adjustable Web GUI Refresh Interval](#entry-24)
25. [Entry 25 - Tasks View exit_code Column Replaced by run_index](#entry-25)
26. [Entry 26 - Tasks View Column and Button Labels Clarified](#entry-26)
27. [Entry 27 - Resource Pool Scheduling Model](#entry-27)
28. [Entry 28 - Execution Path Guardrails and Validation](#entry-28)
29. [Entry 29 - Backward-Compatible Migration Path](#entry-29)
30. [Entry 30 - Legacy Per-Machine Binding Interface Removed](#entry-30)
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

## Entry 13

- Change summary: Shifted host process behavior from round-complete termination to resident service mode with unified shutdown and runtime task submission.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: Host lifecycle used `STOPPED` and implied process-level stop semantics after stop/completion flows; task list was primarily loaded at startup.
  - New:
    - Host lifecycle replaces `STOPPED` with `IDLE` and adds `SHUTTING_DOWN` for explicit process-exit orchestration.
    - Round completion moves host to `IDLE` instead of terminating process.
    - Control surface adds `shutdown` (default `drain`) for GUI/CLI/test script unified process termination.
    - Monitor/API control surface adds runtime `tasks/submit` with `append|replace` semantics; `replace` is rejected while in-flight tasks exist.
- Why improved:
  - Enables multi-round execution without restarting CLI process, matching GUI-oriented operation.
  - Separates execution control from process lifecycle, reducing control ambiguity.
  - Provides one shutdown path usable by GUI, interactive CLI, and automated tests.

## Entry 14

- Change summary: Simplified startup contract by removing auto-start mode and allowing optional task-file boot.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: TaskHost supported optional `--auto-start`, and startup task set was expected from a required `--tasks-file`.
  - New:
    - TaskHost removes `--auto-start`; host always initializes to `NOT_RUN` and requires explicit `start` command.
    - `--tasks-file` becomes optional; when omitted, host starts with empty task set and waits for runtime `POST /tasks/submit`.
    - Startup guidance keeps Monitor API as the default path for both submission and start sequencing.
- Why improved:
  - Makes startup semantics deterministic across CLI and GUI operators.
  - Decouples process boot from initial task provisioning, improving service-style operation.

## Entry 15

- Change summary: Removed IDLE host state; stop flows now return to NOT_RUN and shutdown is restricted to NOT_RUN only.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Host FSM included `IDLE` as the post-completion and post-stop resting state; `RUNNING` transitioned to `IDLE` when all tasks finished; `IDLE` accepted `start`/`resume` and `shutdown` commands; shutdown was accepted from any non-terminal state.
  - New:
    - `IDLE` is removed from the host FSM; the states are: `NOT_RUN`, `RUNNING`, `DRAINING`, `STOPPING_FORCE`, `SHUTTING_DOWN`.
    - `RUNNING` persists after all current tasks complete; rerun requests are accepted directly without re-issuing `start`.
    - `DRAINING -> NOT_RUN` and `STOPPING_FORCE -> NOT_RUN` replace the former `-> IDLE` transitions.
    - `start` command is accepted only from `NOT_RUN` (no longer from `IDLE`).
    - `shutdown` command is accepted only from `NOT_RUN`; other states must stop first.
- Why improved:
  - Eliminates ambiguity between `IDLE` and `NOT_RUN`; a single quiescent state is clearer.
  - Keeps `RUNNING` alive for immediate rerun without an extra start round-trip.
  - Enforces a deliberate stop-then-shutdown flow, preventing accidental process exit from active states.

## Entry 16

- Change summary: Removed aborted as a terminal task state and extended rerun eligibility to include aborted tasks.
- Entry type: Design Modification
- Original design -> New design:
  - Original: `aborted` was listed as a terminal state alongside `succeeded` and `failed`; rerun accepted only `succeeded` and `failed` tasks; `queued -> starting` had no explicit host-state precondition.
  - New:
    - `aborted` is a non-terminal state; transition `aborted -> queued` is allowed via rerun command.
    - Terminal states are `succeeded` and `failed` only.
    - Rerun eligibility set is expanded to `succeeded | failed | aborted`.
    - `queued -> starting` is explicitly conditioned on host being in `RUNNING` state.
- Why improved:
  - Allows recovery from force-stop or force-shutdown without requiring task redefinition.
  - Makes the admission precondition explicit, removing implicit coupling between task and host state machines.

## Entry 17

- Change summary: Established web GUI UX conventions for command button feedback, breadcrumb navigation, new-user guidance, and refresh transparency.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Host command buttons had no visual distinction between enabled and disabled states and clicks on disabled buttons produced no feedback; Task Detail had no navigation path back to the Tasks list; Dashboard had no guidance for first-time users when the task list was empty; Refresh Now button had no indication of data freshness or in-progress state.
  - New:
    - Disabled command buttons render at 35% opacity with `cursor: not-allowed`; the `title` attribute carries a human-readable explanation of the required `host_state`.
    - A live host-state colored badge is displayed next to the Host Commands heading, showing the current state before the user decides which button to click.
    - Task Detail view shows a breadcrumb `Tasks › <task_id>`; clicking "Tasks" returns to the Tasks list and keeps the sidebar highlight correct.
    - Dashboard shows an empty-state panel when no tasks are loaded, prompting the user to navigate to Submit Tasks.
    - Refresh Now button displays "Updated Xs ago" label updated every second; during an in-flight request it changes to "Refreshing…" and is temporarily disabled.
- Why improved:
  - Removes silent failures: users understand why a button cannot be clicked instead of receiving no feedback.
  - Reduces navigation friction: users can return from Task Detail without relying on the sidebar.
  - Reduces first-time confusion: new users are directed to Submit Tasks before expecting task data.
  - Increases system transparency: users can judge whether displayed data is stale.

## Entry 18

- Change summary: Consolidated all host control commands from a separate Control Panel page into Dashboard, eliminating Control Panel as a standalone navigation destination.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Dashboard was observation-only (summary cards, system resources, recent tasks); host commands (Start, Graceful Stop, Force Stop, Shutdown) and Command History resided in a dedicated Control Panel page; sidebar navigation had five items.
  - New:
    - Dashboard is the unified control hub; Host Commands panel (Start, Graceful Stop, Force Stop, Shutdown with inline drain/force select) and Command History (last 20 entries) are co-located with summary cards and resource meters.
    - Control Panel page is removed; sidebar is reduced to four items: Dashboard / Tasks / Task Detail / Submit Tasks.
    - Submit Tasks remains the sole page for data-entry operations (task JSON payload).
- Why improved:
  - Eliminates the navigation round-trip between observing state and issuing commands; operators act on state directly from the same view.
  - Matches the operator mental model: seeing `RUNNING` and clicking Graceful Stop is a single-step decision on one screen.
  - Simplifies the navigation structure and reduces the surface area users must learn.

## Entry 19

- Change summary: Added per-task user abort as a new manual lifecycle intervention, including the API endpoint, state machine extension, and a confirm dialog in the Web GUI.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: The only path to `running -> aborted` was a host-level `force_stop`, which terminated all running and starting tasks simultaneously; no mechanism existed to abort a single task independently; the Abort button in the GUI had no confirmation step.
  - New:
    - Users can issue an abort against a single `running` task without affecting host state or sibling tasks.
    - The task's subprocess is terminated immediately and the task transitions to `aborted` with `abort_reason = "user_abort"`.
    - Host state remains unchanged; other tasks continue executing.
    - `aborted` tasks remain eligible for rerun.
    - A new endpoint `POST /tasks/{id}/abort` is added to the control surface.
    - `abort_task` is added to the control command enumeration in all API and design documents.
    - New reason codes `task_not_found` and `task_not_running` are added to the reason code dictionary.

## Entry 20

- Change summary: Added remote resource conflict detection with pending admission state, task priority, and resource registry.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: All tasks competed only for host concurrency slots; no concept of remote machine resources; tasks started as soon as slots were available.
  - New:
    - Each task now has two mandatory attributes: `resource` (remote machine identifier) and `priority` (positive integer, lower = higher priority).
    - Resources must be pre-registered via `--resources-file` CLI argument or `POST /resources` API before tasks can be submitted; the registry is immutable after loading.
    - A new `pending` task state is introduced: tasks whose required resource is already occupied by a `starting` or `running` task enter `pending` instead of `starting`.
    - Resource lock is written when a task enters `starting` (not `running`) to prevent same-tick double-admission to one resource.
    - When a task reaches any terminal state (`succeeded`/`failed`/`aborted`), its resource lock is released and the single highest-priority pending task for that resource is promoted back to `queued`.
    - Priority governs global scheduling queue order (ascending; lower number first) using stable sort by `created_at` as tie-breaker.
    - `rerun` tasks are appended to the queue tail (sorted among themselves), not inserted by priority to avoid jumping the queue.
    - `pending -> queued` promotions use the original `created_at` as the stable sort key.
    - `force_stop` now also aborts `pending` tasks (same as `starting`); `queued` tasks are preserved.
    - `abort_task` (per-task) now accepts `pending` tasks in addition to `running` tasks.
    - `replace` submit mode now rejects when `pending` tasks exist, in addition to `starting`/`running`.
    - `GET /resources` exposes resource occupancy and per-resource pending queue.
    - `POST /resources` is the runtime API to load the resource registry (accepted only when host is `NOT_RUN` and resources not yet loaded).
    - A new Resources page is added to the Web GUI (independent tab).
    - Dashboard Summary Cards add a `pending_count` card.
    - Task model adds `resource`, `priority`, and `blocked_by` fields.
    - `abort_task` reason code `task_not_running` is replaced by `task_not_abortable` (covers both running and pending eligibility).
    - New reason codes: `missing_resource_field`, `resource_not_registered`, `missing_priority_field`, `invalid_priority`, `invalid_host_state`, `already_loaded`, `empty_resources`.
- Why improved:
  - Enforces single-occupancy of remote machines, preventing conflicting parallel task execution on the same machine.
  - Priority-based scheduling gives operators fine-grained control over which tasks run first without manual reordering.
  - `pending` state provides full observability of the wait reason (`blocked_by`), enabling informed operator decisions.
  - Resource registry validation at load time prevents misconfiguration from causing silent scheduling failures.
    - Clicking the Abort button in either the Tasks list or the Task Detail view opens a browser `confirm` dialog before dispatching the API call; cancelling discards the action with no server request.
- Why improved:
  - Enables targeted interruption without collateral impact on concurrent tasks.
  - Aligns task-level and host-level abort semantics: both use the same `aborted` state and rerun recovery path.
  - Fills the operational gap between doing nothing and issuing a full force-stop.
  - Prevents accidental abort from a misclick; consistent with the existing Force Stop confirm dialog pattern.

## Entry 21

- Change summary: Added requirement to associate each task run with its tool-generated artifact output directory, fulfilled via the {ARTIFACT_DIR} command placeholder mechanism.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: Task execution produced no record of tool-generated output directories; when multiple concurrent sessions wrote to the same working directory (e.g. `kayak_submit`), there was no way to trace which directory belonged to which task or run attempt; supporting tool-specific output flags (e.g. Kayak's `--log-dir`) would require host-level knowledge of individual tool CLI syntax.
  - New:
    - Each task run tracks an `artifact_dir` field that records the tool-generated output location; the association is established at execution start and preserved in the per-run history for all past attempts.
    - Task commands may contain the literal placeholder `{ARTIFACT_DIR}`; `_start_task()` computes `<artifact_base_dir>/<task_id>/run_<N>/`, creates the directory, and substitutes the placeholder before spawning the subprocess.
    - Directory creation and substitution are triggered only when at least one command in the task actually contains `{ARTIFACT_DIR}`; tasks without the placeholder are entirely unaffected.
    - `--artifact-base-dir` (default: `task_artifacts`) configures the root location; the host holds no knowledge of any specific tool name or flag — tool-specific CLI syntax lives entirely in the task definition.
- Why improved:
  - Enables operators to navigate directly from a task session to its output artifacts without manual filesystem searching.
  - Makes multi-session concurrent runs unambiguous: each run's artifact location is uniquely identified by `task_id` and `run_index`.
  - Maintains a clean boundary: task definitions own tool-specific concerns; the host framework owns per-run directory lifecycle.
  - Enables any future tool with an output directory argument to opt in by adding `{ARTIFACT_DIR}` to its command, with zero host code changes.

## Entry 22

- Change summary: Introduced per-run execution history model with RunRecord; rerun now archives past runs instead of discarding them.
- Entry type: Design Modification
- Original design -> New design:
  - Original: `TaskJob` held a single flat set of run metadata (`started_at`, `ended_at`, `exit_code`, `log_path`); `rerun()` cleared all fields unconditionally; system logs were written to `logs/<task_id>.log` with append semantics, mixing runs in one file; task detail API and log API had no concept of run index.
  - New:
    - `RunRecord` dataclass captures a completed-run snapshot: `run_index`, `started_at`, `ended_at`, `exit_code`, `status`, `log_path`, `artifact_dir`; provides `to_dict()` / `from_dict()` as the stable data contract.
    - `TaskJob` gains `run_index: int` (0-based, incremented on each rerun) and `run_history: list[RunRecord]`.
    - `rerun()` archives current run into `run_history` and increments `run_index` before resetting live fields.
    - System log files are isolated per run: `logs/<task_id>/run_<N>.log`.
    - `to_dict(include_history=True)` is used by the single-task detail endpoint; list snapshot omits history to keep response size small.
    - `read_task_logs(run_index=N)` reads the system log of a specific historical run.
    - Web GUI Task Detail renders a collapsible Run History table; each historical row includes status, exit code, timestamps, artifact dir, and a "View Logs" button that switches the log viewer to that run's log file.
- Why improved:
  - Retains full operational context across rerun attempts without requiring external storage.
  - Isolates log files per run, preventing output from different attempts from being interleaved.
  - Clean `to_dict` / `from_dict` contract makes future migration to a persistent store a localized change.

## Entry 23

- Change summary: Task Detail now switches the viewed task immediately when the dropdown selection changes, replacing the explicit "Open" button.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Task Detail had a task dropdown plus an "Open" button; the detail/log view only updated after the user clicked "Open". The periodic task refresh also forcibly reset the dropdown's value back to the active detail task, so changing the selection appeared to revert.
  - New: Selecting a different task in the dropdown fires a `change` handler that opens that task's detail and logs directly; the "Open" button is removed. The task-list refresh preserves the user's current dropdown selection instead of overwriting it.
- Why improved:
  - Removes a redundant click for the common case of inspecting a different task.
  - Fixes the confusing behaviour where the dropdown visually reverted to the previous task.

## Entry 24

- Change summary: Web GUI auto-refresh is now controlled by an adjustable interval slider (0 = paused, up to 30s) instead of a fixed-interval "Refresh Now" button.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: The top bar had a "Refresh Now" button; GUI polling ran at fixed intervals (health 1s, tasks 1.5s, logs 0.8s) with no user control.
  - New: A range slider (0–30s, default 1s) sets a single auto-refresh interval applied to all GUI polling (health, tasks, logs, and the resources view). The leftmost position (0) pauses auto-refresh entirely. The control affects only how often the Web GUI pulls data from the API and has no effect on task-host business logic.
- Why improved:
  - Lets users reduce polling frequency or pause it to limit load and visual churn when observing long-running tasks.
  - Consolidates the previously hard-coded per-stream intervals into one user-visible, adjustable control.
  - Explicitly scopes the change to the presentation layer, leaving scheduling and execution untouched.

---

## Entry 25

- Change summary: The Tasks view replaces the redundant `exit_code` column with a `run_index` column showing the task's rerun index.
- Entry type: Design Modification
- Original design -> New design:
  - Original: The Tasks table had an `exit_code` column, which duplicated information already conveyed by the `status` badge (succeeded/failed/aborted).
  - New: The `exit_code` column is removed and replaced with a `run_index` column that shows how many times the task has been rerun, providing information not available from status alone.
- Why improved:
  - Eliminates redundancy between `exit_code` and `status` columns.
  - Exposes rerun count directly in the task list, giving operators immediate visibility into retry activity without opening Task Detail.

---

## Entry 26

- Change summary: The Tasks view's `detail` column header is renamed to `Operation` and the `Open` button is relabeled `Detail` to clarify their purpose.
- Entry type: Design Modification
- Original design -> New design:
  - Original: The action column was titled `detail` and the navigation button was labeled `Open`, making it unclear that the column holds action controls and that the button leads to Task Detail.
  - New: The column header is `Operation`, signaling it contains action controls (Detail, Abort). The navigation button is labeled `Detail`, directly indicating it navigates to the Task Detail view.
- Why improved:
  - `Operation` makes it clear the column is for user actions, not a data field.
  - `Detail` on the button aligns the label with the destination view name, reducing confusion for new users.

---

## Entry 27

- Change summary: Scheduling changed from fixed machine binding to dynamic resource-pool allocation by configuration.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: Each task had to bind to one preselected machine, which limited parallel throughput and required manual matching.
  - New: Tasks declare a target configuration pool, and the scheduler dynamically selects an available resource at dispatch time.
- Why improved:
  - Increases effective concurrency and resource utilization for workloads sharing the same capability profile.
  - Reduces operational overhead from manual machine-to-task assignment.

## Entry 28

- Change summary: Execution path now applies standardized resource-context rendering with strict validation before task start.
- Entry type: Design Modification
- Original design -> New design:
  - Original: Task commands were executed with mostly static input assumptions and weak validation for missing runtime context.
  - New: Resource context is injected through command templates before start, and unresolved placeholders are treated as explicit start-time errors.
- Why improved:
  - Improves execution predictability by failing early on invalid runtime inputs.
  - Prevents hidden misconfigurations from propagating into hard-to-diagnose runtime failures.

## Entry 29

- Change summary: New registry-based flow is introduced with full compatibility for legacy task and resource interfaces.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: Transition to new scheduling semantics risked breaking existing tasks and operational startup scripts.
  - New: Registry/config-id workflow is added while preserving legacy resource-based loading and execution paths.
- Why improved:
  - Enables phased adoption without disrupting existing production usage.
  - Lowers migration risk by allowing old and new operational models to coexist during rollout.

---

## Entry 30

- Change summary: Remove legacy per-machine task binding interface; all tasks now exclusively use config-pool-based scheduling.
- Entry type: Requirement Change
- Original design -> New design:
  - Original: Tasks could bind to either a specific named machine (`resource` field + `--resources-file`) or a config pool (`config_id` + `--registry-file`); both paths coexisted in the scheduler, TaskManager, and CLI.
  - New: The `resource` field, `--resources-file` argument, and legacy per-resource pending queue are removed. Tasks must declare a positive `config_id`; the scheduler exclusively uses config-pool dispatch.
- Why improved:
  - Eliminates the dual-path complexity that increased maintenance cost and cognitive overhead for operators.
  - Enforces a single, predictable scheduling contract across all task definitions and tooling.


