# TaskHost Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

Implementation file: [../task_host.py](../task_host.py)

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

1. `--tasks-file` (required)
2. `--max-concurrency` (default: `2`)
3. `--max-cpu-percent` (default: `75.0`)
4. `--max-memory-percent` (default: `75.0`)
5. `--max-disk-active-percent` (default: `80.0`)
6. `--scheduler-tick` (default: `0.5`)
7. `--status-interval` (default: `2.0`)
8. `--log-dir` (default: `logs`)
9. `--summary-json` (optional)
10. `--auto-start` (optional, default: `false`)
11. `--monitor-host` (default: `127.0.0.1`)
12. `--monitor-port` (default: `8765`)
13. `--interactive-cli` (optional, default: `false`)

Threshold parameter constraints:

1. `--max-cpu-percent`, `--max-memory-percent`, and `--max-disk-active-percent` are percentage thresholds.
2. Effective runtime range is `1.0..100.0` (values are normalized by `Scheduler`).
3. Max value is fixed at `100.0` because percentage signals cannot exceed 100 in a meaningful way.
4. For interactive GUI/Web monitoring usage on the same host, recommended defaults are `cpu=75`, `memory=75`, `disk_active_time=80`.

Startup mode contract:

1. Default mode (`--auto-start` not set): host enters `NOT_RUN` after loading tasks and waits for explicit start command.
2. Debug mode (`--auto-start` set): host transitions to `RUNNING` immediately after initialization.
3. Control channel default is Monitor API; interactive stdin command loop is enabled only when `--interactive-cli` is set.
4. Execution-round completion does not terminate host process; host transitions to `IDLE` and waits for new control requests.

## 3. Task Definition Contract

Accepted task-file formats:

1. top-level list of task objects
2. object containing `tasks` list

Each task object requires:

1. `task_id` (optional, auto-generated if missing/empty)
2. non-empty `commands: list[str]`

Validation guarantees:

1. no duplicate `task_id`
2. all commands are non-empty strings
3. at least one task exists

Runtime submission contract (via Monitor API):

1. `POST /tasks/submit` payload follows the same task schema (`task_id`, `commands`).
2. `append` mode appends new tasks.
3. `replace` mode is rejected when there are in-flight tasks.

## 4. Runtime Composition Flow

1. `main()` parses args and resolves paths.
2. `load_tasks(...)` validates and returns `list[TaskJob]`.
3. `Scheduler`, `TaskRunner`, `TaskManager` are instantiated.
4. `MonitorServer` is started with configured host/port.
5. Optional stdin command thread is started only in interactive CLI mode.
6. Host enters `NOT_RUN` by default (or `RUNNING` when `--auto-start` is enabled).
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
