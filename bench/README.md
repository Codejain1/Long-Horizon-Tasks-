# Phase 1 benchmark harness

This harness runs mini-SWE-agent with `claude-sonnet-5` on 50 fixed SWE-bench Verified tasks and records success, tokens, cost, steps and wall time for each task. The spec is `docs/PROJECT.md` §18. Settings live in `config.yaml`.

**Dry run is the default.** A dry run uses a mock model, a stub git repo per task and a mock evaluator. It makes no paid calls and no network calls. Real runs are switched on with **one flag**, `real_runs: true` in `config.yaml`, and they need `ANTHROPIC_API_KEY` and `SWEBENCH_API_KEY`.

```bash
uv pip install -e ".[dev,bench]"

horizon-bench select                       # write tasks/swebench_verified_50.json (needs huggingface.co)
horizon-bench run --stage smoke            # the first 10 tasks of the list, 1 repeat
horizon-bench run --stage full             # all 50, `execution.repeats` (3) times, to measure variance
horizon-bench evaluate RUN_DIR...          # re-evaluate (run does this unless --no-eval)
horizon-bench report RUN_DIR...            # report.md and report.json over several runs
```

Without a committed task list, dry runs use a synthetic 500-task pool shaped like SWE-bench Verified (same bucket sizes and repo mix), selected with the same rules. Real runs refuse to start without the committed list.

## Task selection (`horizon-bench select`)

- Every `>4 hours` task is included (SWE-bench Verified has 3).
- The remaining 47 places go to `1-4 hours` (40%), `15 min - 1 hour` (35%) and `<15 min fix` (25%), using largest-remainder rounding. A bucket's unused share moves to the others.
- Within each bucket, places go to repos in proportion to their share, and tasks are picked with the fixed seed (`selection.seed`).
- The first 10 tasks are a stratified sample of the 50 under the same rules. They form the smoke run.

## Environments (`execution.environment: auto`)

- **docker:** the task's SWE-bench image (`docker.io/swebench/sweb.eval.x86_64.*`), when the Docker daemon answers.
- **local:** the fallback. It clones the repo at `base_commit` and runs `pip install -e .` into a venv. This is best effort and can differ from the evaluation environment, so the setup result is recorded per task in `env_setup`.

## Output (`bench/runs/<run_id>/`, git-ignored)

| File | Contents |
|---|---|
| `results.jsonl` | one record per task run (schema version 1): exit status, submitted, resolved, steps, tokens (input / output / cache write / cache read), cost in USD from the config prices, wall time, environment |
| `preds.json` | patches in sb-cli format |
| `trajectories/` | full mini-SWE-agent trajectories |
| `meta.json` | stage, repeat, environment, task list, git SHA, config snapshot |
| `evaluation.json` | resolved ids and the evaluator used |

## Caps

The caps are 50 steps and $1 per task, enforced by mini-SWE-agent. Both caps are checked before each model call, so a task can overrun the cost cap by at most one call. Cost comes from `model.prices` in the config. litellm's own figure is recorded as `litellm_cost_usd` for cross-checking.
