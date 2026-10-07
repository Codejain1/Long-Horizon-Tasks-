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
| Information extraction / accurate retrieval | Rules from earlier tickets: 40/40 (agent alone 7/40) | **Lean stores rules and constraints only, not every fact said**, so a LoCoMo-style question ("what's my sister's name?") would fail. General facts need memrouter episodes, or a fact store, put back in the loop. |
| Multi-session reasoning | Rules combined across tickets (ward's dose limit uses four) | — |
| Knowledge updates | Rental's April rate change; ward's guideline change: both kept | Older and newer versions are both shown; there's no explicit "superseded". |
| Temporal reasoning | Light (dated rate changes) | Not tested directly. |
| **Abstention** (knowing it doesn't know) | Not tested | Needs a test. |
| **Selective forgetting** (a rule revoked or changed) | Not tested | Needs a test: everyone scores ≤7 % here, so it's an opening. |
| **Test-time learning / improving with experience** | Pitfall warnings: 0/15 repeat CI failures against 7/15 | STATE-Bench-style pass^k across repeated episodes, outside coding. |
| Instruction versus preference | Not distinguished | BEAM tests it. |
| Cross-project / cross-person | Team rules: 24/24 against 15/24; hosted, two people | Head-to-head against notes and an MCP memory server: running. |
| Cost and latency | Same build cost as no memory, −15 % to working software; hooks 46 ms | Report tokens per call and p50 like the vendors. |
| Simple baselines | Notes file and MCP memory server: equal on rules when told to keep them | Horizon's edge is that it's automatic. |

## Plan

1. **Head-to-heads on what notes files can't do** (running): team rules across new projects, and recurring CI failures.
2. **New scenario tests** for the uncovered competencies, in non-coding domains as well as coding:
   - **selective forgetting:** a rule changed or revoked mid-way, where the old one must stop applying;
   - **abstention:** a question about something never stated, where the agent must not invent;
   - **instruction versus preference.**
3. **Public benchmarks:** LongMemEval-S (500 questions) through a Horizon fact path, with the same answer and judge model as the published numbers, plus a notes or grep baseline. This first needs a general fact capture: memrouter episodes in the loop, or a fact store. Without it, the honest score would be low.
4. **STATE-Bench** with Horizon as the plugged-in memory, for experience-driven improvement outside coding, reported as pass^5 and cost per task.

Sources: [LoCoMo results and disputes (Mem0)](https://mem0.ai/blog/ai-memory-benchmarks-in-2026), [LongMemEval](https://arxiv.org/abs/2410.10813), [BEAM (Mem0 overview)](https://mem0.ai/library/agent-memory/what-is-beam-memory-benchmark), [MemoryAgentBench](https://arxiv.org/html/2507.05257v3), [STATE-Bench](https://opensource.microsoft.com/blog/2026/05/19/introducing-state-bench-a-benchmark-for-ai-agent-memory/), [DolphinBench](https://arxiv.org/pdf/2609.24971), [Mem0 vs Zep vs Letta comparison](https://ecorpit.com/ai-agent-memory-mem0-zep-letta-cloudflare-comparison-2026/).
