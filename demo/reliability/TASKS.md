# Demo tasks

Paste one prompt per fresh Claude Code session in the demo project. The prompts don't mention Horizon on purpose: the measurement is whether the tool descriptions, the CLAUDE.md snippet and the hooks get Claude to call it unprompted.

1. `Implement slugify in textkit/core.py so that tests/test_slugify.py passes. Don't change the tests.`
2. `parse_duration in textkit/core.py can't handle combined durations like 1h30m. Fix it so tests/test_duration.py passes, without changing the tests.`
3. `word_count in textkit/core.py miscounts words around whitespace and punctuation. Fix it so tests/test_word_count.py passes.`
4. `Make chunk in textkit/core.py validate its size argument (see tests/test_chunk.py) and get those tests passing.`

The source of truth is `tasks.json`, which `run.sh` reads.
