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
2. `--resources-file` (required when `--tasks-file` is provided; omit only when starting with empty task set)
3. `--max-concurrency` (default: `2`)
4. `--max-cpu-percent` (default: `75.0`)
5. `--max-memory-percent` (default: `75.0`)
6. `--max-disk-active-percent` (default: `80.0`)
7. `--scheduler-tick` (default: `0.5`)
8. `--status-interval` (default: `2.0`)
9. `--log-dir` (default: `logs`)
10. `--summary-json` (optional)
11. `--monitor-host` (default: `127.0.0.1`)
12. `--monitor-port` (default: `8765`)
13. `--interactive-cli` (optional, default: `false`)

CLI validation rules:

1. If `--tasks-file` is provided, `--resources-file` must also be provided (fail-fast with exit code `2`).
2. If `--tasks-file` is omitted, `--resources-file` may also be omitted; resources are loaded later via `POST /resources`.
3. Threshold parameters are normalized to `1.0..100.0` range by Scheduler.

Startup mode contract:

1. Host always enters `NOT_RUN` after initialization and waits for explicit start command.
2. Control channel default is Monitor API; interactive stdin command loop is enabled only when `--interactive-cli` is set.
3. Execution-round completion does not terminate host process; host transitions to `IDLE` and waits for new control requests.

## 3. Task Definition Contract

Accepted task-file formats:

1. top-level list of task objects
2. object containing `tasks` list
3. when `--tasks-file` is omitted, host starts with an empty task set and waits for runtime submission (`POST /tasks/submit`)

Each task object requires:

1. `task_id` (optional, auto-generated if missing/empty)
2. non-empty `commands: list[str]`
3. `resource: str` — required, must be a non-empty string matching a registered resource (case-sensitive)
4. `priority: int` — required, must be a positive integer (lower value = higher priority)

Validation guarantees:

1. no duplicate `task_id`
2. all commands are non-empty strings
3. `resource` field present and non-empty
4. `resource` value exists in the registered resource list (cross-validated after resources are loaded)
5. `priority` field present and is a positive integer
6. at least one task exists (when tasks file is provided)

## 3.1 Resources Definition Contract

Accepted resources-file format:

1. object containing `resources` list of non-empty strings

```json
{ "resources": ["machine-A", "machine-B"] }
```

Validation guarantees:

1. `resources` is a non-empty list
2. each entry is a non-empty string
3. duplicate entries are deduplicated (first occurrence kept)
4. resource identifiers are case-sensitive

Runtime resource loading via `POST /resources` follows the same schema and validation rules.

Runtime submission contract (via Monitor API):

1. `POST /tasks/submit` payload follows the same task schema (`task_id`, `commands`, `resource`, `priority`).
2. `append` mode appends new tasks inserted at the correct position by (priority, created_at).
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
