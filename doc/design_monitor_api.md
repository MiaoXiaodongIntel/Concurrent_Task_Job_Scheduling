# MonitorAPI and Observability Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

## 1. Responsibility

MonitorAPI provides read and control interfaces for monitoring and integration.

## 2. Read Interfaces

1. `GET /health`
2. `GET /metrics`
3. `GET /tasks`
4. `GET /tasks/{id}`
5. `GET /tasks/{id}/logs?cursor=...`

## 3. Control Interfaces

1. `POST /control/graceful-stop`
2. `POST /control/force-stop`

## 4. Data Exposure Contract

1. Host-level state and counters must reflect TaskManager source of truth.
2. Task list/details must include status, pid, timestamps, exit metadata, and log location.
3. Log streaming must support incremental cursor-based consumption.

## 5. Integration Expectations

1. Supports CLI polling and future GUI pages.
2. Must provide stable response schema for automation.
3. Access should be restricted to local or authenticated clients.

Related docs:

1. [design_arch.md](design_arch.md)
2. [design_task_manager.md](design_task_manager.md)
3. [design_control_plane.md](design_control_plane.md)
