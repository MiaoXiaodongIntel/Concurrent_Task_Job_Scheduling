# Tests Directory

## Directory Structure

```
tests/
├── TESTING.md                    # This file: structure overview and conventions
├── fixtures/                     # Test data (JSON task files)
│   ├── sample_tasks.json         # Standard 2-task sample (config_id-based)
│   ├── sample_resource_registry.json  # Resource registry fixture (config pool)
│   ├── tasks_single.json         # Single task, for minimal scenarios
│   ├── tasks_replace.json        # Reserved fixture for submit/replace scenarios
│   ├── tasks_long_running.json   # Single task with 30 s sleep; for graceful-stop/force-stop tests
│   └── tasks_failing.json        # Single task that exits with code 1; for RUNNING->FAILED test
│
├── unit/                         # Pure logic unit tests, no process or network
│   ├── test_scheduler.py         # Scheduler.pick_next_tasks() admission logic
│   ├── test_scheduler_pool.py    # Scheduler config-pool allocation details
│   ├── test_task_loading.py      # task_host.load_tasks() file parsing and validation
│   ├── test_resource_registry_loader.py  # Resource registry loader contract
│   ├── test_host_state_machine.py  # HOST state transitions (start/graceful-stop/force-stop/shutdown)
│   ├── test_task_state_machine.py  # Task state transitions (rerun, STARTING->ABORTED via force-stop)
│   ├── test_config_pool_conflict.py  # Config-pool pending/wake behavior
│   ├── test_resource_conflict.py  # Pending/lock behavior around config pools
│   ├── test_template_render.py    # resource placeholder rendering
│   ├── test_artifact_dir.py       # ARTIFACT_DIR behavior
│   └── test_abort_task.py         # abort_task semantics
│
└── e2e/                          # End-to-end tests, spawning a real host process + HTTP (local only)
    ├── conftest.py               # Shared helpers: HostProcess, http_get/post, wait helpers
    ├── test_smoke.py             # Minimal functional test: load tasks → start → all succeeded
    ├── test_control.py           # Control commands: graceful-stop, force-stop→aborted, rerun
    └── test_config_pool.py       # End-to-end config-pool scheduling and assignment checks
```

---

## Layering Rules

| Layer | Spawns process | HTTP | Purpose |
|-------|----------------|------|---------|
| `unit/` | No | No | Pure function/module logic; all inputs constructed in-test |
| `e2e/` | Yes | Yes | Full-stack black-box; behavior observed through the public HTTP API |

**Do not call internal methods** (i.e. names starting with `_`) in `unit/`. Test public interfaces only.

**Do not introduce fake/mock runners** in `unit/`. Scenarios that require real process execution belong in `e2e/`.

---

## Feature-to-Test Mapping

| Doc section | Feature | Test file |
|-------------|---------|-----------|
| design_scheduler.md §4 | Admission policy: host_running, resource thresholds, concurrency cap, FIFO | `unit/test_scheduler.py` |
| design_scheduler.md §4 | Config-pool admission: config_id callbacks and pool allocation | `unit/test_scheduler_pool.py` |
| design_task_host.md §3 | Task file formats (list/dict), validation (duplicate id, empty commands) | `unit/test_task_loading.py` |
| design_task_host.md §3.1 | Resource registry loader validation and config-name resolution contract | `unit/test_resource_registry_loader.py` |
| design_task_manager.md §3 | HOST state machine: all transitions, invalid-transition rejection | `unit/test_host_state_machine.py` |
| design_task_manager.md §2.2 | Task state machine: STARTING->ABORTED, rerun (SUCCEEDED/FAILED/ABORTED->QUEUED) | `unit/test_task_state_machine.py` |
| design_task_manager.md §2.4 | Config-pool pending/wake behavior, priority wake order, force-stop pending cleanup | `unit/test_config_pool_conflict.py` |
| design_task_manager.md §4.1 | Resource placeholder rendering and strict unknown-placeholder failure | `unit/test_template_render.py` |
| design_task_manager.md §4.4 | Artifact directory placeholder expansion and per-run path behavior | `unit/test_artifact_dir.py` |
| design_control_plane.md §3.6 | Per-task abort_task semantics | `unit/test_abort_task.py` |
| design_arch.md capability 1/5 | Multi-task concurrent execution, all tasks succeed | `e2e/test_smoke.py` |
| design_arch.md capability 1/5 | Config-pool end-to-end scheduling with assigned_resource verification | `e2e/test_config_pool.py` |
| design_control_plane.md §3.1 | graceful-stop: RUNNING->DRAINING, in-flight tasks complete, ->NOT_RUN | `e2e/test_control.py` |
| design_control_plane.md §3.2 | force-stop: RUNNING->STOPPING_FORCE, tasks->ABORTED, ->NOT_RUN | `e2e/test_control.py` |
| design_control_plane.md §3.3 | rerun: SUCCEEDED->QUEUED, task completes again | `e2e/test_control.py` |
| design_task_manager.md §2.2 | RUNNING->FAILED: task exits with non-zero code | `e2e/test_control.py` |

---

## How to Run

```powershell
# Run unit tests only (fast, no host process required)
.venv\Scripts\python.exe -m pytest tests/unit/ -v

# Run e2e tests only (requires task_host.py to be runnable on this machine)
.venv\Scripts\python.exe -m pytest tests/e2e/ -v

# Run all tests
.venv\Scripts\python.exe -m pytest -v
```

---

## Conventions for Adding New Tests

1. **Classify**: if a test needs a process or HTTP, put it in `e2e/`; otherwise put it in `unit/`.
2. **Fixture files**: place task JSON files needed by tests in `fixtures/`; do not inline large JSON blobs in test code.
3. **e2e tests**: use the `HostProcess` context manager from `conftest.py` to manage the host process lifecycle, ensuring the process is cleaned up after the test.
4. **Ports**: use `conftest.free_port()` to allocate a dynamic port; hardcoded port numbers are not allowed.
5. **Assertions**: observe system state only through the public HTTP API (`/health`, `/tasks`, `/tasks/{id}`, `/tasks/{id}/logs`, `/control/*`).
