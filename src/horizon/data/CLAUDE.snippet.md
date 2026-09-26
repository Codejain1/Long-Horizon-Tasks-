<!-- horizon:start -->
## Horizon: task state and outcome memory

This project is connected to the Horizon MCP server. It keeps the task on track and learns which approaches work. Follow these rules on every coding task:

1. **Start:** before any edits, call `start_task` with the user's request **verbatim** as `goal`, plus constraints and a short plan. Keep the returned `task_id`. If the session start message lists an active task for this request, reuse it instead.
2. **Before each significant decision or edit** (choosing an approach, library, architecture or fix, or retrying after a failure), call `recall_context` with the `task_id` and a one-sentence `situation`. Prefer what worked before and avoid what failed under similar conditions. Keep the `recall_id`.
3. **After every test run** that follows a change, call `record_outcome` with the same `situation`, the option you implemented as `chosen`, the real `tests_passed` / `tests_failed` counts, the `recall_id` and, if you estimated one, `predicted_success`. Do this whether the tests passed or failed.
4. **Finish:** on the last `record_outcome` for the task, set `task_complete: true`.

Never skip `record_outcome` after a test run. If Horizon is unavailable, carry on with the task as normal.
<!-- horizon:end -->
