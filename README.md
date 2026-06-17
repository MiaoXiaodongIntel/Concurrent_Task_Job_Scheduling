# Concurrent Task Job Scheduling

## Quick Start (Config-Pool Mode)

Start host with task file + resource registry:

```powershell
.venv\Scripts\python.exe core\task_host.py \
	--tasks-file tests\fixtures\sample_tasks.json \
	--registry-file tests\fixtures\sample_resource_registry.json
```

Control and observe through Monitor API:

- `POST /control/start`
- `POST /control/graceful-stop`
- `POST /control/force-stop`
- `POST /control/shutdown`
- `GET /health`
- `GET /tasks`

## Test Commands

```powershell
# unit
.venv\Scripts\python.exe -m pytest tests\unit -v

# e2e
.venv\Scripts\python.exe -m pytest tests\e2e -v

# all
.venv\Scripts\python.exe -m pytest -v
```

## Design Documentation Navigation

### Architecture Overview

1. [doc/design_arch.md](doc/design_arch.md)

### Module Detailed Design

1. TaskHost (Composition Root and CLI): [doc/design_task_host.md](doc/design_task_host.md)
2. TaskRunner: [doc/design_task_runner.md](doc/design_task_runner.md)
3. TaskManager and State Machine: [doc/design_task_manager.md](doc/design_task_manager.md)
4. Scheduler: [doc/design_scheduler.md](doc/design_scheduler.md)
5. ControlPlane: [doc/design_control_plane.md](doc/design_control_plane.md)
6. MonitorAPI and Observability: [doc/design_monitor_api.md](doc/design_monitor_api.md)
7. Web GUI and Frontend API Contract: [doc/design_gui_web.md](doc/design_gui_web.md)

### Design Flow Record

1. [doc/design_flow.md](doc/design_flow.md)