# Horizon reliability demo

This demo measures whether Claude Code calls Horizon's tools unprompted (`PROJECT.md` §14: "rate of reliable MCP invocation by the host"). It uses 6 small tasks on a tiny Python package (`project/`). Each task starts with failing tests.

## Run it headless (recommended)

```bash
uv venv -p 3.12 && uv pip install -e ".[dev,embeddings]"   # from the repo root, once
demo/reliability/run.sh /tmp/horizon-run1
```

For each task, `run.sh`:
1. copies `project/` to `OUT/project` and runs `horizon install-claude-code`, which declares Horizon in `.mcp.json` and installs the hooks and the CLAUDE.md snippet;
2. runs one fresh `claude -p` session with the prompt from `tasks.json`, logging the transcript to `OUT/logs/<task>.jsonl`;
3. runs that task's tests again itself (`OUT/logs/<task>.check.txt`).

Sessions are isolated from your own setup: `--setting-sources project,local` (only the project's hooks) and `--strict-mcp-config` (only Horizon). Set `CLAUDE_MODEL=sonnet` (or any model) to choose the host model.

At the end it prints the report and writes `OUT/report.json`. The sessions use your normal Claude Code login. No Anthropic API key is needed.

## Run it by hand (for example in a cloud session)

```bash
demo/reliability/run.sh --setup-only /tmp/horizon-manual
cd /tmp/horizon-manual/project && claude     # approve the horizon MCP server once
# paste each prompt from TASKS.md into a fresh session
.venv/bin/python demo/reliability/report.py /tmp/horizon-manual   # from the repo root
```

Without transcripts, the report shows only the Horizon-side numbers.

## What the report measures

| Per task | Meaning |
|---|---|
| `started_before_edit` | `start_task` was called before the first file edit |
| `baseline_run` | a test run happened after `start_task` and before the first `recall_context`, so already-failing tests became the baseline |
| `recalled_before_edit` | `recall_context` was called before the first file edit |
| `recorded_after_last_test` | `record_outcome` came after the last test run |
| `task_complete_sent` | the final `record_outcome` set `task_complete` |
| `tests_pass_after` | the task's tests pass after the session |

| `hooks_fired_as_expected` | SessionStart and Stop fired once, PreToolUse once per `recall_context`, PostToolUse once per Bash call that ran, and none failed (from `--include-hook-events`) |
| `rollback_returned` | a `record_outcome` returned `rollback.action: "rollback"` (task 5 is built to fail its first attempt) |
| `restored_after_rollback` | the host ran the `git restore` command before its next edit |
| `recalled_after_rollback` | the host called `recall_context` before its next edit |

`rates` gives the share of tasks meeting each criterion. `horizon_db` holds the server-side counts, including `outcome_recording_rate`: the share of hook-captured test runs that `record_outcome` used.

Run it 2–3 times to see the variance. Compare runs with and without the hooks (delete `.claude/settings.json` after setup) to see how much the hooks add.
