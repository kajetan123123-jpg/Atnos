# Channel operations

- Read the current order/project state from the Atnos API or canonical production store before reporting a stage.
- Report the channel, order ID, durable stage, status, worker telemetry, update time and blocker when available.
- Treat missing or stale telemetry as UNKNOWN; do not infer that a worker is alive from a saved `running` status.
- Recommend only the next action supported by the state. Never create, resume, publish or change an order merely to answer a status question.
- Treat Obsidian channel notes as history and context; the production store remains the live source of truth.
