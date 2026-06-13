# Tests Directory

## Directory Structure

```
tests/
├── TESTING.md                    # This file: structure overview and conventions
├── fixtures/                     # Test data (JSON task files)
│   ├── sample_tasks.json         # Standard 2-task sample (from UI preloadSubmitTemplate)
│   ├── tasks_single.json         # Single task, for minimal scenarios
│   └── tasks_replace.json        # Dedicated to replace-mode tests (2 tasks, no inflight dependency)
│
├── unit/                         # Pure logic unit tests, no process or network
│   ├── test_scheduler.py         # Scheduler.pick_next_tasks() admission logic
│   └── test_task_loading.py      # task_host.load_tasks() file parsing and validation
│
└── e2e/                          # End-to-end tests, spawning a real host process + HTTP (local only)
    ├── conftest.py               # Shared helpers: HostProcess, http_get/post, wait helpers
    ├── test_smoke.py             # Minimal functional test: load tasks → start → all succeeded
    ├── test_control.py           # Control commands: graceful-stop, force-stop→aborted, rerun
    ├── test_submit.py            # Task submission: append/replace semantics, replace rejected when inflight
    └── test_monitor_api.py       # API schema validation: /health fields, /tasks structure, logs cursor
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
| design_task_host.md §3 | Task file formats (list/dict), validation (duplicate id, empty commands) | `unit/test_task_loading.py` |
| design_arch.md capability 1/5 | Multi-task concurrent execution, all tasks succeed | `e2e/test_smoke.py` |
| design_control_plane.md §3.1 | graceful-stop: drain → wait for inflight, no new admissions | `e2e/test_control.py` |
| design_control_plane.md §3.2 | force-stop: running tasks transition to aborted | `e2e/test_control.py` |
| design_control_plane.md §3.3 | rerun: succeeded/failed → queued → succeed again | `e2e/test_control.py` |
| design_control_plane.md §3.4 | submit append: add tasks; submit replace: rejected when inflight exists | `e2e/test_submit.py` |
| design_monitor_api.md §4.1/4.3 | /health field completeness, /tasks schema, /tasks/{id}/logs cursor pagination | `e2e/test_monitor_api.py` |

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
5. **Assertions**: observe system state only through the public HTTP API (`/health`, `/tasks`, `/tasks/{id}`, `/tasks/{id}/logs`, `/control/*`, `/tasks/submit`).
