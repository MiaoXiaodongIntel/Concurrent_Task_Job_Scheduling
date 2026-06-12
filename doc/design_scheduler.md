# Scheduler Detailed Design

Back to architecture: [design_arch.md](design_arch.md)

## 1. Responsibility

Scheduler controls task admission from queue to execution.

## 2. Inputs

1. queued task list
2. running count
3. host lifecycle state
4. scheduler configuration and resource signals

## 3. Outputs

1. per-tick start decisions
2. admission pause/continue decisions

## 4. Admission Policy (Conceptual)

1. Apply hard concurrency cap.
2. Apply optional startup throttling per tick.
3. Apply resource guardrail checks before admitting new tasks.
4. Maintain host responsiveness as first constraint.

## 5. Configurable Knobs

1. `max_concurrency_hard`
2. scheduler tick interval
3. per-tick max starts
4. CPU/memory/disk thresholds
5. pressure hysteresis window

## 6. Interface with TaskManager

1. Reads queue and runtime counters.
2. Emits start intents for specific task IDs.
3. Must not mutate task terminal status directly.

Related docs:

1. [design_task_manager.md](design_task_manager.md)
2. [design_monitor_api.md](design_monitor_api.md)
