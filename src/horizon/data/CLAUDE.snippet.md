<!-- horizon:start -->
## Horizon: task state and outcome memory

This project is connected to the Horizon MCP server. It keeps the task on track and learns which approaches work. Follow these rules on every coding task:

1. **Start:** before any edits, call `start_task` with the user's request **verbatim** as `goal`, plus constraints, a short plan and the `target_tests` the task must make pass. Keep the returned `task_id`. Then **run the project's full test suite once, before any edit**: tests that already fail become the baseline and won't count against your changes. If the session start message lists an active task for this request, reuse it instead.
2. **Before each significant decision or edit** (choosing an approach, library, architecture or fix, or retrying after a failure), call `recall_context` with the `task_id` and a one-sentence `situation`. Prefer what worked before and avoid what failed under similar conditions. Keep the `recall_id`.
3. **Before a crucial choice** (framework, database, architecture, key library, anything hard to reverse), call `evaluate_options` with 2-4 realistic options and your cost/token estimates. Follow its `decision`: implement `chosen` for `routine`, `clear_winner` and `close_call`; for `check_consequences`, run the `consequence_plan` (static checks, then one small spike per option under `.horizon/spikes/`, never in the project's own files) and send the results with `submit_consequences`; for `try_and_rollback`, implement `chosen` and let a regression roll it back; for `ask_human`, ask the user first; for `unscored`, decide yourself.
4. **After every test run** that follows a change, call `record_outcome` with the same `situation`, the option you implemented as `chosen`, the real `tests_passed` / `tests_failed` counts, the `recall_id` and, if you estimated one, `predicted_success`. Do this whether the tests passed or failed.
5. **After a failed test run,** give a one-sentence `failure_reason` to `record_outcome`. If the response has `rollback.action: "rollback"`, run the `restore.git` command **right away, before any other edit, even if you will rewrite the same code** (or follow `restore.claude_code`), then call `recall_context` and try a different approach from the ones in `previous_failures`. If it says `"escalate"`, stop retrying, restore, and ask the user how to proceed; pass their answer to `recall_context` as `human_guidance`.
6. **Finish:** on the last `record_outcome` for the task, set `task_complete: true`.

Never skip `record_outcome` after a test run. If Horizon is unavailable, carry on with the task as normal.
<!-- horizon:end -->
