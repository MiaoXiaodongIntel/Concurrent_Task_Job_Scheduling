# TaskRunner Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

## 1. Responsibility

TaskRunner is responsible for executing a single task job and emitting runtime events.

## 2. Inputs and Outputs

Inputs:

1. `task_id`
2. ordered `commands`
3. runtime context (cwd/env/encoding policy)

Outputs:

1. process metadata (`pid`, start/end timestamps)
2. stream events (`stdout`, `stderr`)
3. completion event with `exit_code`

## 3. Execution Contract

1. Commands for one task job must be executed sequentially in one execution context.
2. Any failed command propagates non-zero return code to the task job.
3. Output should be available in near real time for monitoring consumers.

## 4. Error Handling Contract

1. Process creation failure must produce a terminal failure event.
2. Stream decoding issues must not crash the host process.
3. Runner cleanup should be best effort and non-blocking to scheduler progress.

## 5. Integration Points

1. Consumes launch requests from TaskManager/Scheduler.
2. Publishes stream and completion events to TaskManager.
3. Exposes process identity for ControlPlane termination path.

Related docs:

1. [design_task_manager.md](design_task_manager.md)
2. [design_control_plane.md](design_control_plane.md)
