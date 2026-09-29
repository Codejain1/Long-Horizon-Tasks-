# Demo tasks

Paste one prompt per fresh Claude Code session in the demo project. The prompts don't mention Horizon on purpose: the measurement is whether the tool descriptions, the CLAUDE.md snippet and the hooks get Claude to call it unprompted.

1. `Implement slugify in textkit/core.py so that tests/test_slugify.py passes. Don't change the tests.`
2. `parse_duration in textkit/core.py can't handle combined durations like 1h30m. Fix it so tests/test_duration.py passes, without changing the tests.`
3. `word_count in textkit/core.py miscounts words around whitespace and punctuation. Fix it so tests/test_word_count.py passes.`
4. `Make chunk in textkit/core.py validate its size argument (see tests/test_chunk.py) and get those tests passing.`
5. `Implement title_case in textkit/core.py. Try the simplest approach first: make the shared helper _cap return word.title() and build title_case on it, then run the whole test suite. If anything fails, replace it with a proper implementation until all tests pass.`
   This one is built so the first attempt causes a **regression**: changing the shared `_cap` helper to `str.title()` breaks the already-passing `sentence_case` tests. Rollbacks trigger only on regressions (tests failing at the task's baseline don't count), so this exercises the Phase 3 rollback path: does the host follow `rollback` (restore, then `recall_context`) before retrying?
6. `Add a persistent WordCountCache in a new module textkit/cache.py (see tests/test_cache.py): it must survive process restarts and will later be shared by several worker processes. Pick a sensible storage approach for that and get the tests passing.`
   This one contains a crucial choice (the storage format, which must work for several processes later). It measures whether the host calls `evaluate_options` before committing to it.
7. `Add a command-line interface in textkit/cli.py (see tests/test_cli.py): subcommands slugify, words and title; input from an argument, a --file, or stdin; a --json output mode; and an optional --cache DIR that stores word counts with textkit.cache.WordCountCache. Work in small steps and run the tests as you go.`
   The long, multi-step task: several features, a library choice (argparse, click or typer), and tests to pass incrementally. It depends on tasks 1, 3, 5 and 6, so run it last.

The source of truth is `tasks.json`, which `run.sh` reads.
