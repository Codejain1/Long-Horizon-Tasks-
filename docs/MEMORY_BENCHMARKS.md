# Memory benchmarks: what the field uses, and what Horizon must pass

Research notes (2026-10-07) on how memory systems (Mem0, Zep, Letta, Cognee and others) prove themselves, mapped to what Horizon has and hasn't tested. Horizon aims to be general memory for agents, not only for coding, so it has to answer the same questions.

## The benchmarks that matter

| Benchmark | What it tests | How it's scored | Notes |
|---|---|---|---|
| **LoCoMo** (2024) | Memory over long multi-session conversations (about 9K tokens, up to 35 sessions): single-hop, multi-hop, temporal, open-domain, adversarial questions | LLM judge (usually GPT-4o), plus F1 or BLEU; vendors also report tokens per retrieval and p50 latency | The most cited. Scores are disputed: Mem0 reports 91.6–92.5 % and Zep 75–94 % depending on who runs it. **Letta scored 74.0 % against Mem0's then-published 68.5 % by putting the conversation in a plain file and giving the agent grep.** |
| **LongMemEval** (ICLR 2025) | 500 questions over chat histories of about 115K tokens (S) or 500 sessions (M), on five abilities: **information extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention** | LLM judge | Commercial assistants and long-context models drop about 30 %. Mem0's weakest category is knowledge updates: an add-only store keeps old facts that resurface. |
| **BEAM** (ICLR 2026) | 1M to 10M-token conversations: fact tracking, entity updates, **contradiction resolution**, temporal ordering, **instruction versus preference**, multi-hop, summarisation | LLM judge over 2,000 probing questions | Mem0: 64.1 % at 1M and 48.6 % at 10M. Scale is the test. |
| **MemoryAgentBench** (2025) | Four competencies: **accurate retrieval**, **test-time learning** (new skills during use), **long-range understanding**, **selective forgetting** (revise or remove facts on contradiction) | Task metrics (accuracy, F1, Recall@5) | **Every method failed selective forgetting: at most 7 % on multi-hop.** RAG wins retrieval; long context wins understanding. |
| **STATE-Bench** (Microsoft, May 2026) | Whether agents **improve with experience** on 450 enterprise tasks (support, travel, shopping), with a simulated user and "bring your own memory" | **pass@1, pass^5 (succeeds on all 5 runs), cost per task**, user-experience score | Grades actions, not recall. GPT-5.1 without memory: pass^5 about 30 % in travel. |
| **DolphinBench** (2026) | Memory used for real actions in simulated apps, about 500K tokens of history per persona | Action completion, and the **accuracy against cost and latency Pareto frontier** | Compares against **simple filesystem or grep and full-context baselines**, which are often competitive. |

## Lessons for running them fairly

1. **Always include the simple baselines:** full context, a notes file, and filesystem plus grep. They have beaten famous memory layers before (Letta on LoCoMo). Horizon's own head-to-head confirmed it: a notes file the agent was told to keep matched Horizon on rules kept, at the same cost.
2. **Fix the protocol:** the same answer model, judge model, prompts and dataset split for every system. Small protocol changes have moved vendor scores by more than 15 points.
3. **Report cost and latency next to accuracy** (tokens per call, p50 latency, cost per task). Without them, an accuracy number can't be compared.
4. **Report reliability, not one lucky run:** pass^k, or repeats with the spread.
5. **The write step is barely measured** (deciding what to keep from mostly noise). Horizon's rule capture is exactly that step; `evals/rules/tickets.py` measures it, and few benchmarks do.

## Horizon against the competencies

| Competency | Horizon's evidence | Gap |
|---|---|---|
| Information extraction / accurate retrieval | Rules from earlier tickets: 40/40 (agent alone 7/40). **`horizon.factmem` + `evals/memory/longmemeval.py`:** on a stratified **n=60** slice of LongMemEval-S (same Sonnet answer+judge as RAG/full), **Horizon 56/60 (93.3 %) vs RAG 53/60 (88.3 %)**, ~$2.63 vs ~$2.53, ~5.9k vs ~5.7k prompt tokens; **full** long-context arm **10/60** before usage limit (resume from `/tmp/lme60/full.jsonl`). | **Lean hooks** still store rules only; **general chat facts** go through `FactMemory` (rounds, embed+BM25, Jev relevance). Not yet wired into Claude Code lean install — benchmark path only. **n=500** and vendor-comparable protocol still to run. |
| Multi-session reasoning | Rules combined across tickets (ward's dose limit uses four) | — |
| Knowledge updates | Rental's April rate change; ward's guideline change: both kept | Older and newer versions are both shown; there's no explicit "superseded". |
| Temporal reasoning | Light (dated rate changes) | Not tested directly. |
| **Abstention** (knowing it doesn't know) | LongMemEval n=60: **3/4** (Horizon) vs **4/4** (RAG); Jev relevance gate in `factmem.search` is the abstention hook | Weak vs plain RAG on this slice; item 4 (explicit “don’t know” before answer) not separate yet. |
| **Selective forgetting** (a rule revoked or changed) | Not tested | Needs a test: everyone scores ≤7 % here, so it's an opening. |
| **Test-time learning / improving with experience** | Pitfall warnings: 0/15 repeat CI failures against 7/15 | STATE-Bench-style pass^k across repeated episodes, outside coding. |
| Instruction versus preference | Not distinguished | BEAM tests it. |
| Cross-project / cross-person | Team rules: 24/24 against 15/24; hosted, two people | Head-to-head against notes and an MCP memory server: running. |
| Cost and latency | Same build cost as no memory, −15 % to working software; hooks 46 ms | Report tokens per call and p50 like the vendors. |
| Simple baselines | Notes file and MCP memory server: equal on rules when told to keep them | Horizon's edge is that it's automatic. |

## Results: Cursor composer-2.5 answers, Codex (gpt-5.6-luna) as independent judge (2026-10-09)

Same answer model, prompts and questions for every arm; a second judge from another vendor so the answer model never grades itself (`evals/memory/rejudge.py`). Raw rows: `/tmp/mem-eval-v2/`.

| Benchmark | Horizon | Plain retrieval | Full history | Published by others |
|---|---|---|---|---|
| LoCoMo, J on categories 1-4 (n=62 of 80) | **85.5 %** | 64.5 % | 91.9 % | Mem0 66.9 % (paper) to 91.6 % (later claims); Zep 75-94 %; Letta 74.0 % |
| LoCoMo category 5, never mentioned (official two-option prompt) | 17/17 | 17/17 | 18/18 | usually left out |
| LongMemEval-S (n=60) | **90.0 %** | 88.3 % | rerun queued | — |
| FactConsolidation multi-hop 6K (exact match, no judge) | 68 % | 62 % | **96 %** | ≤ 7 % for every method in the paper (older models) |

- **These LoCoMo and BEAM numbers predate the date-order fix (session 28):** memories reached the reader sorted by date text ("April" before "March"), in both the Horizon and plain-retrieval arms. Rerun both before quoting them. LongMemEval's dates sorted correctly.
- Prompt size on LoCoMo: Horizon about 950 tokens per question, full history about 20,000. Full history wins there because each conversation fits in the prompt; memory has to win where it doesn't (BEAM 1M+, LongMemEval-M).
- The judges agree on 54-60 of every 60-62 questions; composer's self-grading didn't inflate Horizon (LoCoMo 85.5 under both).
- Not the vendors' protocol: they answer with GPT-4o or GPT-4o-mini, which the ChatGPT plan behind Codex doesn't serve. The fair claim is the gap between arms, not the absolute numbers. Samples are small (spread about ±5-8 points); the LongMemEval gap (one question) is within it.
- Runner fixes behind these numbers: LoCoMo category 5 lacked the official options (0/18 before); the J judge was missing (token F1 punishes full-sentence answers); BEAM sorted sessions as text (April before March, 185 of 188 messages in one undated session) and judged against free text instead of its rubric; Cursor prompts over about 1 MB failed on the argument limit (now stdin); Jev calls had no timeout (a BEAM run hung).

### Re-scored on shared questions, with significance (session 28)

`evals/memory/compare.py` on the rows above: only questions every arm answered, a 95 % bootstrap interval on Horizon's gap, and an exact McNemar p-value. Most leads were noise.

| Benchmark (n shared) | Horizon − plain retrieval | Horizon − full history |
|---|---|---|
| LoCoMo J (62) | **+27.4 pts [+16.1, +40.3], p < 0.001** | −9.7 [−19.4, −1.6], p = 0.07 |
| BEAM 100K pass (35) | +8.6 [−2.9, +20.0], p = 0.38 | +8.6 [−5.7, +22.9], p = 0.45 |
| LongMemEval-S dev (60) | +3.3 [−5.0, +11.7], p = 0.69 | not run |
| FactConsolidation multi-hop (50) | +6.0 [−6.0, +18.0], p = 0.51 | **−28.0 [−40.0, −16.0], p < 0.001** |
| FactConsolidation single-hop (50) | −2.0, p = 1.0 | −2.0, p = 1.0 |

- **The FactConsolidation full-history arm had been run** (96 % on multi-hop) but was left out of the table. The "68 % against ≤ 7 %" comparison was the answer model, not Horizon: plain retrieval got 62 % with the same model, and full history beats Horizon by 28 points.
- The only significant Horizon win is over plain retrieval on LoCoMo, and that baseline was weaker than Horizon's own search (meaning only, 10 memories against Horizon's 12-30). The rerun below fixes that.

## The fair protocol (session 28): `evals/memory/run_protocol.sh`

- **Arms** (`evals/memory/arms.py`), all on the same factmem store and the same budget (12 memories; up to 30 on counting questions):
  - `horizon`: meaning + words candidates, Jev judges relevance (with its floor, which is how it abstains);
  - `hybrid`, the strong baseline: the same candidates ranked by an off-the-shelf cross-encoder (`ms-marco-MiniLM-L-6-v2`), no floor, as rerankers are used;
  - `rag`: the top candidates by meaning, unjudged;
  - `full`: the whole history.
- **One answer model and one grader for every arm:** Cursor composer-2.5 answers, Codex (the ChatGPT plan's gpt-5.6-luna) grades (`--judge-backend codex`). FactConsolidation is exact match.
- **Held-out split:** LongMemEval `dev` is the 60 questions the counting rule was tuned on; results are reported on `test` (the other 440), never tuned on.
- **Same questions in every arm:** each arm runs twice, so errored questions are retried, and `compare.py` scores only questions every arm answered.
- Sizes: LoCoMo 150, BEAM 100K 80, LongMemEval test 100, FactConsolidation 50 per split. Benchmarks run one at a time: each question re-embeds its whole history, and parallel runs pushed the load to 20 on 8 cores.
- Still not the vendors' protocol (GPT-4o-mini): absolute numbers don't compare with published ones; the gaps between arms do.

## Plan

1. **Head-to-heads on what notes files can't do** (running): team rules across new projects, and recurring CI failures.
2. **New scenario tests** for the uncovered competencies, in non-coding domains as well as coding:
   - **selective forgetting:** a rule changed or revoked mid-way, where the old one must stop applying;
   - **abstention:** a question about something never stated, where the agent must not invent;
   - **instruction versus preference.**
3. **Public benchmarks:** Run with **Cursor Agent** (Composer / Grok): `./evals/memory/run_all_cursor.sh` (`MODEL='composer-2.5'`, `OUT=/tmp/mem-eval-cursor`, tunables `LME_N`, `LOCOMO_N`, `BEAM_N`, `FC_N`). Harnesses: `longmemeval.py`, `locomo.py` (token-F1, official categories), `beam.py` (HF `Mohammadta/BEAM` 100K probing Qs + LLM judge), `factconsolidation.py` (MemoryAgentBench). Full LME **n=500**: `LME_N=500` (many hours at ~1 min/question with Composer).
4. **Agent simulators (optional):** `state_bench.py` (microsoft/STATE-Bench, `RUN_STATE_BENCH=1` + locked eval client) and `dolphinbench.py` (`RUN_DOLPHINBENCH=1` + harness stub under `evals/memory/adapters/`). Default `run_all_cursor.sh` only records skip/smoke status for these.

Sources: [LoCoMo results and disputes (Mem0)](https://mem0.ai/blog/ai-memory-benchmarks-in-2026), [LongMemEval](https://arxiv.org/abs/2410.10813), [BEAM (Mem0 overview)](https://mem0.ai/library/agent-memory/what-is-beam-memory-benchmark), [MemoryAgentBench](https://arxiv.org/html/2507.05257v3), [STATE-Bench](https://opensource.microsoft.com/blog/2026/05/19/introducing-state-bench-a-benchmark-for-ai-agent-memory/), [DolphinBench](https://arxiv.org/pdf/2609.24971), [Mem0 vs Zep vs Letta comparison](https://ecorpit.com/ai-agent-memory-mem0-zep-letta-cloudflare-comparison-2026/).
