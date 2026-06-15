# TaskRunner Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

Implementation file: [../core/task_runner.py](../core/task_runner.py)

## 1. Responsibility

TaskRunner is responsible for executing a single task job and emitting runtime events.

## 2. Inputs and Outputs

Inputs:

1. ordered `commands`

Outputs:

1. `RunningTaskHandle(process, script_path)` from `start_task(...)`
2. synchronous `CompletedProcess` from `run_session(...)`
3. process streams and exit code consumed by `TaskManager`

## 3. Execution Contract

1. Commands for one task job must be executed sequentially in one execution context.
2. Any failed command propagates non-zero return code to the task job.
3. Output should be available in near real time for monitoring consumers.

## 4. Error Handling Contract

1. Process creation failure must produce a terminal failure event.
2. Stream decoding issues must not crash the host process.
3. Runner cleanup should be best effort and non-blocking to scheduler progress.

## 5. Integration Points

1. `TaskManager` calls `start_task(commands)` for async orchestration.
2. `TaskManager` owns stream-reader threads and completion wait.
3. `cleanup(handle)` removes generated temporary script files.
4. `run_session(commands)` is a synchronous helper reused by tooling.

Related docs:

1. [design_task_manager.md](design_task_manager.md)
2. [design_control_plane.md](design_control_plane.md)
