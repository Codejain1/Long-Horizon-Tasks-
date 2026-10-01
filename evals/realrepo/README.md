# A real project's history as an unattended backlog

Eight consecutive real changes to [attrs](https://github.com/python-attrs/attrs), from 2025-03 to 2026-08 (`tasks.json`). Each is written as the issue it solved: the problem and the expected behaviour, never the fix. One unattended Claude Code session per change, with and without Horizon.

- **Same directory throughout.** Before each session the working copy is reset to the change's parent commit, so Horizon's project memory carries over as it would on a real project.
- **`./ci.sh` stands in for attrs' GitHub CI:** the test suite plus the changelog check (`ci_checks/`). attrs wants a news fragment in `changelog.d/` for every user-visible change (`.github/CONTRIBUTING.md`).
- **Grading:**
  - the change's real test files, laid over the agent's work, must pass along with the whole suite (as SWE-bench does);
  - the changelog check is reported separately.

**The tests discriminate.** Validated with fake agents: applying each commit's real source changes resolves 8/8, with the changelog check passing. Without the source changes, 0/8 resolve. Without the changelog fragment, the changelog check fails 8/8. Python 3.13 is used because task 8's test needs `copy.replace` (3.13+).

```bash
python evals/realrepo/run.py --arm baseline --out /tmp/rr [--source /local/attrs/clone]
python evals/realrepo/run.py --arm lean --out /tmp/rr [--source /local/attrs/clone]
```

## Result, run 1 (2026-10-01)

On tasks 1–7, both arms resolved 6/7 and added changelog fragments 7/7. The changelog check never failed in a CI run, because the convention is visible from the repo's own `changelog.d/`. Lean cost 6 % less. Details are in `evals/longhorizon/RESULTS.md`.
