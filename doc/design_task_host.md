# TaskHost Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

Implementation file: [../core/task_host.py](../core/task_host.py)

## 1. Responsibility

`task_host.py` is the composition root and CLI entry point.

It is responsible for:

1. Parsing runtime arguments.
2. Loading and validating task definitions.
3. Wiring `Scheduler`, `TaskRunner`, and `TaskManager`.
4. Wiring `MonitorServer` for read/control HTTP endpoints.
5. Producing final summary output when `--summary-json` is provided.
6. Running host loop in resident mode until explicit shutdown is requested.

## 2. CLI Interface

Current arguments:

1. `--tasks-file` (optional)
2. `--registry-file` (optional config-pool resource registry path)
3. `--max-concurrency` (optional; if omitted, no concurrency cap is applied and admission is limited only by the CPU/memory/disk thresholds)
4. `--max-cpu-percent` (default: `75.0`)
5. `--max-memory-percent` (default: `75.0`)
6. `--max-disk-active-percent` (default: `80.0`)
7. `--scheduler-tick` (default: `0.5`)
8. `--status-interval` (default: `2.0`)
9. `--log-dir` (default: `logs`)
10. `--artifact-base-dir` (default: `task_artifacts`)
11. `--summary-json` (optional)
12. `--monitor-host` (default: `127.0.0.1`)
13. `--monitor-port` (default: `8765`)
14. `--interactive-cli` (optional, default: `false`)

CLI validation rules:

1. If `--tasks-file` is provided, `--registry-file` must also be provided (fail-fast with exit code `2`).
2. If `--tasks-file` is omitted, `--registry-file` may also be omitted; tasks can be loaded later via `POST /tasks/submit`.
3. Threshold parameters are normalized to `1.0..100.0` range by Scheduler.
4. `--max-concurrency` has no default; when omitted, the Scheduler applies no concurrency cap and admission is governed solely by the CPU/memory/disk host thresholds.
5. `--artifact-base-dir` defaults to `task_artifacts` (relative path resolved from CWD). The directory is only created for a specific task run when that task's commands contain the `{ARTIFACT_DIR}` placeholder; tasks without the placeholder are entirely unaffected.

Startup mode contract:

1. Host always enters `NOT_RUN` after initialization and waits for explicit start command.
2. Control channel default is Monitor API; interactive stdin command loop is enabled only when `--interactive-cli` is set.
3. Execution-round completion does not terminate host process; host remains in `RUNNING` and waits for new control requests.

## 3. Task Definition Contract

Accepted task-file formats:

1. top-level list of task objects
2. object containing `tasks` list
3. when `--tasks-file` is omitted, host starts with an empty task set and waits for runtime submission (`POST /tasks/submit`)

Each task object requires:

1. `task_id` (optional, auto-generated if missing/empty)
2. non-empty `commands: list[str]`
3. `config_id: int` — required positive integer matching a registered config ID from the resource registry
4. `priority: int` — required, must be a positive integer (lower value = higher priority)

Validation guarantees:

1. no duplicate `task_id`
2. all commands are non-empty strings
3. `config_id` is present and is a positive integer
4. `config_id` exists in the loaded resource registry (when registry is loaded)
5. `priority` field present and is a positive integer
6. at least one task exists (when tasks file is provided)

## 3.2 Upstream Task Generation Contract

TaskHost consumes runtime `tasks.json`, while task generation can be sourced from upstream query data:

1. Upstream Task Builder may build `tasks.json` from HSD-ES query results.
2. Generated tasks must still satisfy TaskHost's runtime schema (`task_id`, `commands`, `config_id`, `priority`).
3. TaskHost remains source-agnostic: it validates only the final task payload, regardless of whether tasks came from manual files or query-driven generation.

## 3.1 Resource Registry Contract

Accepted registry-file format: object containing a `resources` list where each entry declares `config_id`, `name`, and `properties`.

```json
{ "resources": [ { "config_id": 1, "name": "machine-A", "properties": { "ip": "10.0.0.1" } } ] }
```

Validation guarantees:

1. `resources` is a non-empty list
2. each entry has a positive integer `config_id`, a non-empty `name`, and an optional `properties` object
3. duplicate resource names raise an error

Runtime submission contract (via Monitor API):

1. `POST /tasks/submit` payload follows the same task schema (`task_id`, `commands`, `config_id`, `priority`).
2. `append` mode inserts new tasks at the correct position by (priority, created_at).
3. `replace` mode is rejected when there are in-flight or `pending` tasks.

## 4. Runtime Composition Flow

1. `main()` parses args and resolves paths.
2. `load_tasks(...)` validates and returns `list[TaskJob]`.
3. `Scheduler`, `TaskRunner`, `TaskManager` are instantiated.
4. `MonitorServer` is started with configured host/port.
5. Optional stdin command thread is started only in interactive CLI mode.
6. Host enters `NOT_RUN` by default.
7. `TaskManager.run()` executes lifecycle loop with start/stop/rerun/submit/shutdown control participation.
8. `build_summary(...)` writes JSON snapshot if requested.
9. Process exits only after shutdown command completion.

## 5. Exit Code Contract

1. returns `2` when task-file loading/parsing fails
2. otherwise returns `TaskManager.run()` exit code (normally `0` when shutdown flow completes)

Related docs:

1. [design_task_manager.md](design_task_manager.md)
2. [design_scheduler.md](design_scheduler.md)
3. [design_task_runner.md](design_task_runner.md)
