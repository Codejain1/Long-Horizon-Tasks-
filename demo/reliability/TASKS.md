# Demo tasks

Paste one prompt per fresh Claude Code session in the demo project. The prompts don't mention Horizon on purpose: the measurement is whether the tool descriptions, the CLAUDE.md snippet and the hooks get Claude to call it unprompted.

1. `Implement slugify in textkit/core.py so that tests/test_slugify.py passes. Don't change the tests.`
2. `parse_duration in textkit/core.py can't handle combined durations like 1h30m. Fix it so tests/test_duration.py passes, without changing the tests.`
3. `word_count in textkit/core.py miscounts words around whitespace and punctuation. Fix it so tests/test_word_count.py passes.`
4. `Make chunk in textkit/core.py validate its size argument (see tests/test_chunk.py) and get those tests passing.`
5. `Implement title_case in textkit/core.py. Try the simplest approach first: make the shared helper _cap return word.title() and build title_case on it, then run the whole test suite. If anything fails, replace it with a proper implementation until all tests pass.`
   This one is built so the first attempt causes a **regression**: changing the shared `_cap` helper to `str.title()` breaks the already-passing `sentence_case` tests. Rollbacks trigger only on regressions (tests failing at the task's baseline don't count), so this exercises the Phase 3 rollback path: does the host follow `rollback` (restore, then `recall_context`) before retrying?

The source of truth is `tasks.json`, which `run.sh` reads.
