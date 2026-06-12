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

## 2. CLI Interface

Current arguments:

1. `--tasks-file` (required)
2. `--max-concurrency` (default: `2`)
3. `--scheduler-tick` (default: `0.5`)
4. `--status-interval` (default: `2.0`)
5. `--log-dir` (default: `logs`)
6. `--summary-json` (optional)
7. `--auto-start` (optional, default: `false`)
8. `--monitor-host` (default: `127.0.0.1`)
9. `--monitor-port` (default: `8765`)
10. `--interactive-cli` (optional, default: `false`)

Startup mode contract:

1. Default mode (`--auto-start` not set): host enters `NOT_RUN` after loading tasks and waits for explicit start command.
2. Debug mode (`--auto-start` set): host transitions to `RUNNING` immediately after initialization.
3. Control channel default is Monitor API; interactive stdin command loop is enabled only when `--interactive-cli` is set.

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

## 4. Runtime Composition Flow

1. `main()` parses args and resolves paths.
2. `load_tasks(...)` validates and returns `list[TaskJob]`.
3. `Scheduler`, `TaskRunner`, `TaskManager` are instantiated.
4. `MonitorServer` is started with configured host/port.
5. Optional stdin command thread is started only in interactive CLI mode.
6. Host enters `NOT_RUN` by default (or `RUNNING` when `--auto-start` is enabled).
7. `TaskManager.run()` executes lifecycle loop with start/stop/rerun control participation.
8. `build_summary(...)` writes JSON snapshot if requested.

## 5. Exit Code Contract

1. returns `2` when task-file loading/parsing fails
2. otherwise returns `TaskManager.run()` exit code

Related docs:

1. [design_task_manager.md](design_task_manager.md)
2. [design_scheduler.md](design_scheduler.md)
3. [design_task_runner.md](design_task_runner.md)
