# Side-by-side demo: the same backlog, with and without Horizon

Two terminals, the same agent, the same 12 tickets of **RentEase** (a no-brokerage rental platform where deposits are placed with a lending partner). Each ticket runs in a **fresh session**, as a long project does day by day. Tickets 1–3 state nine business and legal rules that later tickets need but don't repeat, like the deposit size, the reserve, consent and the yield split. That gap is what Horizon exists for.

```bash
# terminal 1                          # terminal 2
demo/sidebyside/run.sh without        demo/sidebyside/run.sh with

# afterwards (or any time, for the tickets so far)
demo/sidebyside/scoreboard.py                     # Claude Code: real cost
demo/sidebyside/scoreboard.py --price 1.5         # Cursor: cost estimated at $1.50 per million tokens
```

- **The scenario:** `SCENARIO=rental` (the default) or `SCENARIO=ward`, the harder one: 14 tickets of hospital medication administration with 11 rule checks (`evals/longhorizon/scenarios/ward`). Both sides must use the same one. `SCENARIO=boxoffice` tests decisions rather than memory: three crucial early choices whose tempting answers break in later tickets.
- **The agent:** `AGENT=claude` (the default, model `sonnet`, override with `MODEL=…`) or `AGENT=cursor`. Both sides must use the same one.
- **Each terminal prints a line per finished ticket:** hidden tests, turns, tool calls, time, tokens and, with Claude Code, cost.
- **The scoreboard compares the tickets both sides finished:**
  - time, tokens and cost;
  - hidden tests passed;
  - the nine rule checks, rule by rule.

**Fair by construction:**
- "Without" has no Horizon installed at all, rather than being told not to use it, because lean Horizon works through hooks, not tool calls.
- Both sides start from the same code and get the same tickets.
- Each side is kept out of the other's folder and out of the hidden tests, by an OS-level read guard on macOS.
- Tokens and cost are read from each session's own result, never from the agent's self-report.

**What to expect:**
- Earlier runs (`evals/longhorizon/RESULTS.md`, "Rental"): with Horizon and Jev, 26/27 rule checks over 3 runs, against 8/27 without, at 56–62 % of the tokens.
- **Jev matters.** With a TypeSafe key (`TYPESAFE_API_KEY`, or the macOS Keychain item `horizon-typesafe`), Jev picks out the rules. Keyword fallback alone kept 14/27.
- A **single** session would show no difference, since the agent sees everything at once. The effect needs several sessions.

**Cost:** about 20 minutes per side, run in parallel, and 24 agent sessions in total on your subscription.
