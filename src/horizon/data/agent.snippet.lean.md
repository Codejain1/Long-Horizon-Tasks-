<!-- horizon:start -->
## Horizon: task continuity

This project is connected to the Horizon MCP server. It carries goals, constraints and team rules across sessions and projects, and remembers what worked.

1. **Start:** before any edits, call `start_task` with the user's request **verbatim** as `goal`, the task's `constraints` (including any the session start message lists from earlier tasks in this project) and `team_rules`: the team rules listed at session start plus any the user sets for **all** their projects. If the session start message lists an active task for this request, reuse it instead.
2. **Only before a hard-to-reverse choice** (database, framework, architecture), you may call `evaluate_options` with 2-4 options and follow its `decision`. Otherwise decide yourself.
3. **Finish:** after your final test run, call `record_outcome` once with the `task_id`, a one-sentence `situation`, what you implemented as `chosen`, the real `tests_passed` / `tests_failed`, and `task_complete: true`. If a change broke tests and you are stuck, call `record_outcome` with a `failure_reason` and follow its `rollback` advice.
4. **When the user asks** what Horizon remembers or why, use `show_memories` or `explain_decision`.

If Horizon is unavailable, carry on with the task as normal.
<!-- horizon:end -->
