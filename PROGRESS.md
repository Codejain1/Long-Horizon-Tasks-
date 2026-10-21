# Progress

The full log up to 2026-10-18 (1,300 lines: Horizon v1/v2, memory benchmarks, Hermes setup) is in the git tag
`snapshot-pre-restructure` (`git show snapshot-pre-restructure:PROGRESS.md`).

## Status (2026-10-18)

**Live**
- **Hermes** on AWS (Mumbai `i-0d0e2f600c143e705`). It runs on WhatsApp (self-chat only), with memrouter as its
  memory (`/opt/embed-memory`) and the rails plugin.
- **The model:** Qwen3-14B-AWQ on vLLM on a Stockholm g5.xlarge ($1.07/h while on), reached through an SSH tunnel.
- **Claude Code memory:** memrouter over MCP (`claude/`).

**Results that hold**
- **Recall engine:** on LoCoMo it beats plain retrieval (the only significant memory-benchmark result). Search takes
  25 ms, episode writes 21 ms, and `/add` through the Qwen writer 2.7 s (in the background).
- **Rules:** an owner rule ("from now on…") becomes a standing rule that reaches every runner job.
- **Runner verification:** when the runner says a job is done, the hidden tests agree 63/64 times
  (fastbench2 + fastbench3).

**Results that don't prove what they seemed to**
- **Conventions run (housebench):** late-half house-rule checks +64.6 pts [+43.8, +83.3] with memory vs none. But the
  bench hands memory the exact rule sentence and the none arm gets no feedback, so it shows "told the rules" beats
  "never told". A notes file would likely match it.
- **Experience lessons on ordinary tasks:** no benefit. Run 2: +0.0 [−18.8, +15.6]. fastbench3, with silent memory:
  −6.2 [−21.9, +9.4]. The none arm alone moved 78.1 → 71.9 % on identical tasks, so ±6 pts is noise. What gets
  through the filter is raw-pytest "advice" notes.
- **The relevance bar:** memrouter scores are relative (per-query normalised, LoCoMo-trained), so `MIN_SCORE 0.5`
  does not mean "relevant".

**The runner's real cost**
- About half the jobs end on the 10-min budget, and 31 of those had already passed the hidden tests.
- Contract, tests and spec review take 56–62 % of the time on ~40-line programs.

## Architecture review (2026-10-18)
Evidence from the code and from the live Hermes memory (read-only):
- **Experience isn't wired to Hermes:** 0 episodes and 0 credited recalls in 214 memories since 5 Sep. Only the
  benchmark records outcomes.
- **Wrong standing rules are injected into every reply:** "The assistant can find Japanese, Mexican or Italian
  dessert options" and 3 similar capability statements are stored as rules. A profile entry is a question ("which is
  better, paddu or idli").
- **The writer drops numbers or names in 35 of 59 writes** (`checked = mismatch`).
- **Credit uses the runner's own step results:** the hidden-test outcome is sent without a `recall_id`, so it never
  reaches the memories that were recalled.
- **Consolidated "strategies" are the retry label** ("build via first try succeeded in 5/6"), with no content.
- **One SQLite connection shared across server threads,** with reads outside the lock (the flaky test).
- **The memory service also does geocoding, food search, mood and location.**
- **Hermes, the writer and every benchmark stream share one A10G.**

## Long-horizon parts 1-5 (2026-10-18, design in docs/LONG_HORIZON.md)
1. **Design doc:** written.
2. **Judge (`runner/judge.py`):** built.
   - Backfill over 250 past jobs, job_done verdicts held out: Brier raw 0.323 (worse than the base rate 0.250, ECE
     0.32: overconfident), calibrated 0.220, ECE 0.004. **The acceptance criterion is met.**
   - Benchmarks now label every job's verdict with its hidden result.
3. **Research jobs:** `--kind research`, the evidence check (each quote must be on its page) and `bench/web.py`
   (versions / places / flight prep, scored against independent truth). Live run `/home/hermes/web1` (plain vs
   runner, 2 reps) has started; results pending.
4. **Big-model arm:** per-role endpoints (`--planner/--spec/--judge URL,MODEL,KEY_ENV`, `--api-key-env`), tokens
   per model, backlog `--label/--extra`, retries on rate limits.
   - **Owner OK'd the free OpenRouter model.** Running in `/home/hermes/backlog-smoke-e` next to plain / runner / mem
     (rental tickets 1–3): `big` = nvidia/nemotron-3-ultra-550b-a55b:free alone (plain loop), `bigplan` = runner +
     memory with that model as planner only.
   - The judge stays on local Qwen in every arm.
5. **World model (`runner/forecast.py`):** v0 was weak: AUC 0.44 early, 0.61 mid (no better than test pass rate
   alone), no task knowledge, and it read "more failed checks" as good. **v1 (owner: "do it")** adds a task prior,
   rate features, per-phase fits and calibration, gated on held-out tasks. Gain over the base rate +0.041 (TF-IDF)
   / +0.049 (our encoder); AUC 0.70–0.71 from minute 2 (target 0.75, not met yet). The doom stop now fires (17 jobs,
   2 of them solved, 55 min saved).
   - Model: `/home/hermes/forecast/forecast-v1.json`.
   - Level 2 data: every benchmark explores 20 % of watcher decisions with logged propensities. See
     docs/LONG_HORIZON.md §9.
- **Research live run web1 (invalid):**
  - the planner used `true` checks;
  - Qwen wrote versions from memory (Python 3.11.6 vs the true 3.15.0);
  - the judge accepted them from the doer's summary;
  - the goal never told the plain arm the answer format.

  Fixed: trivial checks become evidence checks, a job finishes only once every claim is on its page, the judge
  treats the summary as a claim, and every goal states the answer format. Rerun as `/home/hermes/web2`.
- **Brownfield:** the smoke showed every arm failed rental ticket 1 by rebuilding the project. The runner now has a
  project map, existing tests fixed from the start and re-run, writes that drop names or shadow the package refused,
  and an extend-don't-rebuild plan shape. The guard was seen working live (it refused a rewrite that dropped
  `KINDS`). The v3e smoke `/home/hermes/backlog-smoke-e` (rental 1-3, plain / runner / mem) is running.
  - **Ticket 1, runner:** it extended `properties.py` correctly but forgot `from typing import Optional`
    (NameError) and ran out of its 12-minute budget during the re-plan.
  - **Ticket 1, plain:** it wrote the function, ran only the 3 old tests, stopped, and never exported it.
  - These are now model-quality failures, not harness bugs.

## Logins built in (2026-10-22, owner's request)
**Phone + OTP:**
- The owner sends `login phone <number>` once.
- When a site is logged out, the bot opens its sign-in, types the stored phone, presses Get OTP and asks for the
  OTP on WhatsApp. It types the OTP, checks the login, and carries on with what was asked.

**Google:** `google for logins <email>`. The owner signs in to Google once by hand in the bot's Chrome; the bot then
uses "Continue with Google" and the account in the popup.

**Code:** `bot/login.py` (Swiggy, ixigo, redbus, Zomato) and `cdp.type_login`.

**Rule change (owner asked for OTP entry):** login OTPs yes, payment OTPs never.
- The only typing the bot can do is the owner's own phone, or 4–8 digits.
- Only on a page whose text is a sign-in.
- Never on payment gateways, never into card / CVV / expiry / UPI PIN fields.
- The OTP is never stored: not in memory, the log or traces (`[login OTP]`).

**Untested on the real sites' sign-in pages:** button labels are guesses per site, and captures and traces will
show the real ones. Zomato may still block logins from the server's data-centre IP.

## Why so many bugs, and the systemic fixes (2026-10-22, owner asked)
Nearly every bug this week came from five root causes. One fix per cause, not per bug:

1. **Free-text model output parsed by hand in 5 places** (comma numbers, a call written as text, "Extra data").
   **Fix:** `brain.extract`, forced to a JSON schema by vLLM 0.31 `json_schema` (verified live: pure JSON even when
   told to explain). Used for routing (DECIDE), booking updates, Swiggy orders and offers, and weekly-reflect. Tool
   calling and the hand parsers are removed. An unusable answer is retried once (live: one run generated to the
   token limit).
2. **Built against an imagined outside world, with tests on my own fakes** (Hermes's frozen prompts and browser
   mode, WhatsApp `@lid`, Swiggy page shapes). **Fix:** pages the bot can't read are saved to `~/.bot/captures/`, so
   fixes are tested on real pages.
3. **Failures only seen when the owner reported them.** **Fix:** every turn leaves a trace in `bot.db` (each model
   call: schema, time, output; errors), read with `deploy/bot/trace.sh`. A turn error also alerts the owner on
   Telegram.
4. **A slow manual loop** (every deploy and test needs the owner). **Fix:** `bench/botsmoke.py` runs on the real
   model before every deploy. Letting Claude deploy the bot itself is the owner's choice.
5. **Safety rules lost in the switch:** Hermes's rails plugin stopped with Hermes. **Fix:** the rails live in
   `bot/cdp.py`, the layer every browser action passes. It never opens payment gateways and never clicks
   pay / order / book (checked on the element itself), and it has no typing method at all.
   - Also ported: "mistakes the owner corrected before", lost in the same switch.

**Also:** the bot is self-contained (`booking.py` and `places.py` in `bot/`), and the Hermes plugins are removed
(they stay in git history; rollback uses the server's installed copies).

**Real model after the fixes (botsmoke, 2 reps):**

| Set | Result |
|---|---|
| test2 | 40/40 |
| test3 (seen) | 20/20 |
| chat | 16/16 |
| **test4 (fresh)** | 13/16 before the retry fix, 14/16 after |

- The remaining test4 miss: "Order something for dinner" gets a question back instead of a started food order.
- Reply time: median 2.0 s, p90 2.7 s.
- Tests: 223 pass.

## Our bot replaces Hermes (2026-10-22, owner's decision: "replace", keep the jobs and extras)
**Why:**
- On Qwen3-14B, Hermes's free-roaming agent kept picking the wrong tool or handing work back to the owner.
- Code-run steps were measured better: forms 85 % vs plain 32 %.
- Hermes took 60–110 s per reply, mostly Qwen's thinking.

**Inventory (read-only):**
- Owner chats were rare: 4 on WhatsApp, 1 on Telegram.
- 8 cron jobs did most of the work: 619 runs, all delivered to Telegram, on OpenRouter Nemotron (free).
- WhatsApp runs through Hermes's standalone Node bridge.

**Built (`bot/`, `docs/BOT.md`, 220 tests pass):**
- channels;
- the assistant: one model call per step, bookings as forms;
- grounding: drops details the message doesn't support, code resolves weekdays and named travellers, tool calls
  written as text are read;
- the scheduler with the 8 jobs (script / monitor seeded from Hermes / model), all on Qwen now;
- the switch and rollback kit.

**Real-model check (`bench/botsmoke.py`, memory off, 2 reps):**
- **reply time median 1.6 s, max 2.4 s** (Hermes: 60–110 s);
- chat 16/16;
- test2 (shaped the grounding) 38/40;
- **held-out test3 18/20 with values checked**. Its 2 failures (comma numbers, two-weekday ranges) were then fixed,
  so test3 is now seen too.

**Not carried over:** terminal/coding sandbox, image generation, skills, Discord/Slack, kanban (unused in 30 days).

**Switch-over: done 2026-10-21 20:56 UTC (owner ran `deploy/bot/switch.sh`).**
- `owner-bot` and `owner-whatsapp-bridge` are active; `hermes-gateway` is stopped and disabled; WhatsApp is
  connected on the same session.
- Owner: replies feel instant.
- They got the same joke 3 times. Temperature 0.2 is on purpose, for repeatable extraction; a would-be repeat is now
  asked again at 0.9 (deployed with `deploy/bot/update.sh`).

**Swiggy (M2, built, not yet tried live):**
- `bot/cdp.py`: a stdlib WebSocket and DevTools client; the bot uses its own tab in the desktop Chrome.
- `bot/food.py`: code opens order history, search (Dishes tab) and checkout, and clicks ADD by visible text. The
  model only turns page text into lists; every restaurant, item and price is checked against the page. Code filters
  veg and budget and sorts by price. It never presses pay or place order.
- In the bot:
  - "look at my swiggy orders" saves the orders, and the pattern goes into every reply's memory;
  - a food order, after YES, shows the 3 cheapest real options; replying 1–3 fills the cart and reports the
    checkout total;
  - interim "Looking on Swiggy…" messages are sent while it works.
- **Untested on real Swiggy pages** (tests use page text written to look like Swiggy's). The first live run will
  show the page shapes.


The owner redefined the product after the held-out ward result. It is a personal assistant on a small model. It:
- books flights, trains, buses, cabs and hotels, and plans trips around a ticket;
- recommends and orders food, comparing the same order across platforms on price and reviews;
- fills everything up to payment, then hands off by UPI request to the owner's own UPI ID, which the owner approves
  with their PIN.

The coding-ticket benchmarks are no longer the target.

**Deployed to live Hermes 2026-10-20 19:45 UTC (owner ran it; the permission check blocks me from deploying):**
- the booking tools and the rails changes;
- backups in `~/.hermes/backup-plugin-{embed_memory,rails}-20261020-194515`;
- after the deploy: gateway and memory are active, WhatsApp is connected, 18 tools load (7 new), and UPI is off
  (no `upi_id`).

- **2026-10-21: Hermes's browser tools now drive the desktop Chrome** (`browser.cdp_url: http://127.0.0.1:9222`).
  Before, they used a temporary hidden browser, so site logins never persisted.
  - Chrome now has a 127.0.0.1-only debugging port and its own profile `~/.config/hermes-chrome-cdp`.
  - The drop-in is `/etc/systemd/system/hermes-chrome.service.d/cdp.conf`; the config backup is
    `config.yaml.before-cdp-20261021-160212`.
  - The owner logs in once over DCV (SSM port-forward 8443). Hermes reuses the session, and the owner can watch it
    work.

- **2026-10-21: Hermes is back on the click-by-label browser tools** (`browser.backend: "off"`).
  - **Why:** in Browser Use mode (the Hermes default) the only browser tool was `browser_exec` (Python code). The
    model didn't use it and fell back to keyless web search, which is IP rate-limited. The page capture for
    `compare_offers` and the rails' label checks don't see `browser_exec`.
  - The rails now also block card, CVV, OTP and UPI PIN typing inside `browser_exec` code.
  - Verified: `browser_*` tools available True, browser-use mode False, WhatsApp connected.
  - Config backups: `config.yaml.before-backend-off*`.
  - The owner is logged in to Swiggy, ixigo and redbus. Zomato login fails from the server (likely a data-centre IP
    block); its menus are readable without login.

- **2026-10-21 18:02, first live try and fixes:**
  - **Seen:** the owner asked for the cheapest veg biryani on Swiggy. Hermes called `nearby_food` (OpenStreetMap)
    twice and never opened the browser or `task_start`. The plugin's old food guidance said "use nearby_food for
    every food recommendation".
  - **Latency:** a turn took 65 s of Qwen thinking (6.3k characters of reasoning), then 38 s of `nearby_food`, then
    7 s. The model server itself takes 0.4 s for a tiny request with no queue.
  - **Measured:** `/no_think` gives 1.1–1.3 s per call vs 9–19 s, with the same tool choice. The forms bench was
    also run with thinking off.
  - **Deployed (owner ran it; backup `backup-plugin-embed_memory-20261021-180226`):**
    - ordering, delivery and compare requests go to the app in the browser, with `task_start`;
    - `nearby_food` is only for eating out;
    - `save_orders` saves past orders from Swiggy's order history page, and food tasks return `order_history`
      (top dishes, restaurants, usual spend);
    - `/no_think` added.

**What was built:**
- **`hermes/embed_memory/booking.py`:** a task is a form (template per kind). The model fills only what the owner
  said. Code then:
  - adds habits from memory;
  - names what's missing (one question);
  - writes the summary the owner says YES to.

  Memory is split by kind:
  - facts with source and expiry (travellers = permanent; prices = 6 h);
  - habits, a default only after ≥ 2 YES and ≥ 60 % of choices;
  - tasks as episodes;
  - site notes from failures.

  Offers count only if their quote is on a browser page Hermes saw from that platform and holds the price. Prices over
  the cap are dropped. `plan_trip` drafts the hotel, the arrival cab and the days.
- **Hermes tools** (in the embed_memory plugin; a post_tool_call hook keeps the last 30 browser pages): `task_start`,
  `task_update`, `task_confirm`, `task_finish`, `compare_offers`, `plan_trip`, `save_traveller`.
- **Rails UPI handoff (opt-in):** it is on only when the owner writes their UPI ID to `~/.hermes/rails/upi_id`. On
  payment pages Hermes may choose UPI, type exactly that ID, and press pay after the owner's approval. Blocked:
  - cards, net banking, wallets and UPI app intents;
  - the PIN and OTP;
  - any other text.

  Without the file, the old rails apply. Bare "Cards" options are now blocked on food sites too; this gap predates
  the change.
- **`bench/forms.py`:** does the form beat a plain prompt with the same memory as text?
  - 30 cases (10 dev, 20 test), 3 reps, paired, Wilson intervals.
  - A test checks that a perfect extraction passes all 30, and that the scorer catches guessed details.
- **Forms bench result (Qwen3-14B, 3 reps; `/home/hermes/forms1`, `forms2`):**

  **forms1 (before fixes):** test forms 55 % [42, 67] vs plain 8 % [4, 18].

  I read plain's raw answers: its failures are real. It invents details (old budgets copied into new trips, hotel
  dates made up, "something veg" ordered as paneer biryani). It gets weekdays wrong ("next Saturday" → a Thursday).
  It asks about optional details instead of being ready.

  Forms' failures led to code fixes in booking.py:
  - slot synonyms (budget → max_price);
  - people as names ("me and mom"); a bare count is asked, never filled with the owner alone;
  - placeholders dropped ("current location");
  - **the fixed rails override the model:** it had set a cab's pay to "credit card".

  Both arms' prompts were then tuned on dev. The old test split is no longer held out. 20 fresh cases (test2) were
  written before forms2.

  **forms2, held-out test2: forms 51/60 = 85 % [74, 92] vs plain 19/60 = 32 % [21, 44].** Pairs: only forms 35,
  only plain 3. Asks when it should: forms 18/21, plain 4/21.

  Remaining forms failures are 3 cases, the same in every rep:
  - no tool call ("Fly to Goa on Friday with Rahul");
  - the time dropped from a cab's when;
  - "Get a cab": the model invented the drop and the time.

  Limits:
  - I wrote both the cases and the code.
  - One plain prompt style.
  - Qwen3-14B only.
  - This measures understanding the request, not booking on real sites.
- Tests: 192 pass.

## Results after the runner fixes (2026-10-19)
- **The first real ticket solved by the system:** rental ticket 1 (property search). The runner passed every hidden
  test on its first try in 4.6 min, and runner + mem also solved it. Plain failed it, even after a QA fix-up.
- Ticket 2 (KYC masking) failed in every arm. In ticket 3 the runner broke ticket 1's search (the regression guard
  was all-or-nothing and turned off); mem kept it.
- Fixed since: the guard is per test, and research `kind: link` claims (the quote is in the url, the link must open).
- **The v3l rerun was worse (`backlog-l`, `web5`):** runner and mem 0 passing on every ticket; research 0/6
  verified, 7/20 checks.
  - Cause on rental: in ticket 1 the model made `properties.py` import a `search_properties` that `store.py`
    didn't have. `import rentals` broke, and every later attempt, re-plan and ticket started from the broken
    package. The guard saw it but nothing undid it.
  - With n = 1 per arm, j's solved ticket 1 and l's failure are both within run-to-run noise. One solved run proves
    the system can do it, not that it reliably does.
  - Research: the model never used `kind: link` for flight links (the repair message didn't mention it), and it
    quoted old versions again.
- **Fixed in v3m:** the code (not tests) is snapshotted at each step's start. An attempt that breaks a test that
  passed before, or drops an existing name, is rolled back, and the model is told so. Evidence failures now show the
  link-claim form. Rerun: `backlog-m` (runner, mem), `backlog-m-r2` (runner again, for n = 2), `web6`.
- **Held-out result (2026-10-19, v3p) — negative:**
  - Ward tickets 1, 2, 11 × 3 repeats: plain 0/9, runner 0/9 solved (interval 0–30 % each). The runner used the
    full 20 min on every job (8× plain's time, 2× tokens) and broke earlier work once. The rental gains did not carry
    over: they were fitted to rental.
  - Research with the user-agent fix: plain 2/6 complete and verified (places both reps), runner 0/6 verified though
    12/20 checks vs plain 8/20. The runner's extra work did not turn into verified answers.
  - v3n rental final: no arm lost earlier work (rollback works); ticket 1 solved 3/3, ticket 2 1/3, ticket 3 0/3.
  - Owner's call after this: pause runs, rethink the memory system and how tasks are defined.
- **The test loop was the bottleneck (2026-10-19):** every question cost a 1.5–2 h backlog run, harness bugs were
  found hours in, and one run per arm swung either way. Now:
  - `bench/preflight.py` (seconds): no `._` files, the model answers, the sandbox reaches the task sites, the runner
    reads the scenario, ticket 1's hidden tests fail on the start. The go scripts stop if it fails. First live use
    stopped a launch in 2 min (a false alarm from the check itself, fixed: it now reads the project in the job sandbox).
  - `bench/tickets.py` (about 25–40 min for a round): any ticket × arm × repeat in parallel, each from a verified
    state (the reference minus the modules of later tickets). Valid: rental 1–4, 6, 7, 11; ward 1, 2, 11 (the rest
    change shared modules and stay in backlog.py). Report: solved rate with a Wilson interval, kept earlier tickets,
    pairs vs plain. Scenario references restored from the snapshot tag (they pass 23/23 and 31/31).
  - Full backlog runs only at milestones.
- **v3n rental (in progress):** all three arms solved ticket 1 and kept it. Ticket 2 (KYC) solved for the first time,
  by runner run 2 (3/3, ticket 1 kept); runner run 1 and mem 1/3. Runner run 2 then failed ticket 3 but kept 1–2.
  The fixes fired: done refused 2× per arm, rollbacks 1–4×. One run per arm, tuned on these tickets: not a claim.
- **Running (v3p):** held-out ward tickets 1, 2, 11, plain vs runner × 3 (`tk-ward1`); research both arms × 2 with the
  user-agent fix (`web8`).
- **v3m crashed at startup, unnoticed by the benchmark:** macOS `tar` added `._*.py` metadata files that Python can't
  read; the bench threw away the runner's error output and scored the crash as "0 passed" (1 min, 0 tokens). Now:
  tarballs with `COPYFILE_DISABLE=1 --no-xattrs`, the runner skips `._` files, and a crash stops the arm with its error.
  Research (web6) did run: 7/20 checks, evidence ok 2/6, verified 0/6, about the same as before.
- **Why ticket 2 (KYC) fails, from the backlog-j logs:**
  - The tests step said "created tests/test_tenant_kyc.py" three times without writing it: 4 min and both re-plans
    gone before any code. Now `done` is refused while the step's file is missing.
  - The runner added `verify_tenant` to `people.py` but never exported it from `rentals/__init__.py`; quick fix and
    thinking mode both missed it. Now the watcher names the missing export line.
  - Both arms' own tests used Aadhaar `123456789012` as valid; the ticket forbids a leading 1. The spec reviewer caught
    this 3/3 times when asked offline (old prompt too), but in the run the 20-min budget ran out first.
  - Ticket 2 gates the rest: the hidden conftest's `tenant()` calls `verify_tenant`, so later tickets' hidden tests
    can't pass without it.
- **Places scored 0 because it was impossible:** Nominatim answers the bare `Mozilla/5.0` user agent with 403, so no
  arm ever got data. The runner now fetches as `Mozilla/5.0 (compatible; long-horizon-runner/0.1)` (200 on every
  task site), and the goal states the viewbox order (models wrote lat first).
- **Newest-version check:** a claim of the newest version fails if its own page lists a higher stable version
  (pre-releases skipped). On the real pages: 10/10 right (correct answers pass; 3.13.12, 20.9.0, 16.3 flagged).
  Ceiling: a stale page without the newer version still passes, and "latest LTS" goals would be misjudged.
- **Report pools repeat runs:** `backlog.py report --out D1 D2 ...` gives each arm's mean and, from 3 runs per arm,
  a bootstrap 95 % interval vs plain.
- **Running (v3n, all of the above except the user agent):** `backlog-n` (runner, mem), `backlog-n-r2` (runner),
  `web7` (runner). Research needs a rerun of both arms with the user-agent fix: plain was blocked too.
- **Rental tickets 1–3 (`backlog-j`, v3j: edit_file, existing tests shown, quick fix, 20-min budget):**

  | Arm | Hidden tests passing right after each ticket | Passing at the end | Minutes | Tokens |
  |---|---|---|---|---|
  | plain | 0 % | 0/4 | 7 | 0.61M |
  | runner | **25 %** (before the fixes: 0 %) | 0/4 | 80 | 2.14M |
  | mem | **37.5 %** | **1/4** (the only arm with a passing test at the end) | 105 | 2.30M |

  First real progress on existing-project work. Still no ticket fully solved and kept, and the cost is about 10×
  plain.
- **Research (`web4`, v3k: fetch tool, Nominatim):** complete and verified 0/6 in both arms; checks runner **9/20** vs
  plain 2/20.
  - The runner found the **true** Node.js version (26.12.0) with a verified quote. It left Python and PostgreSQL
    blank rather than invent them.
  - The plain agent's quotes were real but for **old** versions (Python 3.13.12): the evidence check proves a quote
    exists, not that it is the newest.
  - Flight links: runner 4/4 and 3/4 checks (links resolve, route and date right). The evidence check fails because
    Google Flights is a JavaScript page with no quotable text.
  - Places: 0 in both arms.

## Latest live results (2026-10-18, Qwen3-14B)
- **Brownfield rental, tickets 1–3 (backlog-smoke-e):** plain 0/4 hidden at the end (5.6 min, 0.14M tokens);
  runner 0/4 (64 min, 1.21M tokens); runner + memory: package import broken, 0/0 (40 min, 0.56M tokens). No arm
  solved a ticket. The runner costs about 10× more with no gain yet.
- **Research tasks (web3):** complete and verified 0/4 in both arms; checks plain 3/14 vs runner 6/14. Qwen invented
  versions (3.11–3.12 vs the true 3.15.0) and a place at the owner's exact coordinates; the evidence check caught
  every one. The places task can't be scored (Overpass unreachable from the server): replace its truth source.
- **Free frontier model:** blocked by the 50 requests/day free-tier limit.

## In progress
- **New memory deployed to live Hermes (2026-10-18, owner's OK):** v3 memory service (pending directives,
  failures/corrections, concurrency fix, food search moved to the plugin) and the plugin (`confirm_rule`,
  `confirm_preference`, `forget_preference`, mistakes block, `places.py`).
  - Before it: a dry run on a copy of the live DB (214 memories, 1 rule, 4 tastes, search 45 ms, no errors).
  - After it: both services active, the plugin prefetch returns the rule, tastes and location, and the WhatsApp
    bridge is `connected`.
  - Backups: `/opt/embed-memory/backup-20261017-230031/`, `~/.hermes/backup-plugin-embed_memory-20261017-230031`.
- **Hermes container cleanup:**
  - The stopped and created `hermes-*` containers (195 of 265) were removed.
  - Removing the running ones older than a day (about 70, idle `sleep infinity`, from `container_persistent: true`)
    was blocked by the permission check: the owner runs it.
- **Wrong live rules removed (2026-10-18, owner's instruction):** rules 3–6 and profile 5 are deactivated; live
  Hermes now has 1 rule and 4 taste entries. The backup is `memory.db.bak-20261017-203609`.
- **Step 0 (stop the harm):**
  - **Concurrency fix (done):** every SQLite statement runs under one lock with its rows fetched whole, and the
    HTTP accept queue went from 5 to 128. A new stress test (8 threads × 60 requests) passes; the old flaky test
    passed 10/10.
  - **Wrong live rules (not done):** removing rules 3–6 and profile 5 on live Hermes was approved by the owner but
    blocked by the permission check. The owner runs it (command in the session summary). A backup exists:
    `/home/hermes/.embed-memory/memory.db.bak-20261017-203609`.
- **Step 1, measurement (built, smoke running):**
  - `bench/backlog.py` runs the rental and ward multi-ticket scenarios in one repo, with hidden tests after each
    ticket and QA fix-up rounds.
  - Arms: plain / runner / notes / mem. The runner gained `--plain` and token accounting.
  - The scenarios are checked: rental's 23 and ward's 31 hidden tests pass on the reference and fail on the start.
  - Smoke runs (rental tickets 1–2): `/home/hermes/backlog-smoke` (v3a = the system before the redesign) and
    `backlog-smoke-b` (v3b = after). Check with `bash bench/backlog_progress.sh backlog-smoke backlog-smoke-b [report]`.
- **Step 2, memory redesign (built):**
  - Directives inferred by a model wait as pending; Hermes asks the owner (`confirm_rule`, `confirm_preference`).
  - `failures.py` replaces `experience.py`: failing checks from real outcomes, per scope, become warnings, credited by
    `warning_id`.
  - The runner reads rules, warnings and what the owner said, and writes nothing back.
  - Not yet deployed to live Hermes. The food/location move waits for that deploy.
- **Step 3, lean runner (built):** it finishes when every fixed test passes, reviews the spec only when stuck, and
  thinks only on escalation.
- **housebench2 (stopped at ~60 %, old memory design):**
  - mem vs none solved +38.9 [+16.7, +61.1], house checks +42.6 [+20.4, +64.8];
  - rules-only vs none solved +15.8 [+0.0, +31.6], house checks +33.3 [+17.5, +49.1].
  - Rules alone carry most of the rule-following. Full memory solved more, likely via the feedback naming failing
    checks, which is what `failures.py` now does on purpose.

## Decisions made
- 2026-10-22: the bot may type the owner's phone and a login OTP the owner sends (owner's request). Payment OTPs,
  cards, CVV and UPI PIN stay impossible, enforced in `bot/cdp.py`.
- 2026-10-22: Hermes is replaced by our bot (owner's call). The bot keeps Hermes's WhatsApp session, env file,
  scripts and memory files, and copies the bridge to `/opt/bot-bridge`, so Hermes's install can be removed later.
- 2026-10-22: the cron jobs run on the local Qwen instead of free OpenRouter Nemotron (rate-limited in the logs, and
  one model to keep alive).
- 2026-10-22: WhatsApp owner filtering is the bridge's own (LID-aware) allowlist. A phone-number check in the bot
  would drop the owner's `@lid` messages.
- 2026-10-21: the target is the booking assistant (owner's spec). Tasks are forms with code-checked slots, not open
  goals, because the measured small-model failures were mostly misreading the task or what "done" means.
- 2026-10-21: only habit slots become defaults (from, passengers, class, seat, cab type, diet...). Destination,
  dish, dates and budgets are said each time or asked.
- 2026-10-21: the UPI handoff is opt-in through a file only the owner writes. Live Hermes is unchanged until the owner
  says deploy.
- 2026-10-21: the forms bench is the first gate (minutes). Live-site rehearsals up to payment come next, and only if
  forms beat plain on the test split.
- 2026-10-19: ward is the held-out scenario: no runner fix was made from ward runs. Rental results after this
  date's fixes are tuned-on-test and don't count as evidence.
- 2026-10-19: fast loop first (preflight, tickets.py); a full backlog run only for a milestone decision.
- 2026-10-19: the spec reviewer prompt stays as is: offline, Qwen caught the invalid Aadhaar 3/3 with it, so the
  ticket 2 failure was time, not review.
- 2026-10-19: the places goal names Nominatim's viewbox order: documentation every arm gets, not a hint for one.
- 2026-10-19: a failed attempt that broke earlier work is rolled back to the step's start, not built on: building on a
  broken package cost the whole v3l rental run.
- 2026-10-19: deploy presigns need `--region ap-south-1` (the default region gives a 301 redirect, which the md5
  check caught).
- 2026-10-18: repo restructured to only what runs. Everything else is in the tag `snapshot-pre-restructure`;
  `IDEAS.md` lists what is worth reusing from it, with evidence and paths.
- 2026-10-18: the runner no longer writes Horizon events (`--events-db` removed): nothing read them.
- 2026-10-18: `stall.py` trimmed to `Run` + `stuck`, the only parts the runner uses.
- 2026-10-18: LoCoMo loading for training is now `memrouter/locomo_data.py`. Training scripts use fastembed
  directly instead of `horizon.memrouter.embedding`.
- 2026-10-17: runner lessons are silent by default: rules always; beyond them only advice, lessons and fears with
  score ≥ 0.5, at most 3.

## Open questions
- **UPI request reachability:** NPCI stopped person-to-person UPI collect requests in 2025. Merchant checkouts that
  take a UPI ID likely still work, but some sites offer only QR or app intent. Check per site in rehearsals.
- **Logins:** IRCTC, Swiggy, Zomato and Uber need OTP logins, which Hermes must not enter. Default: the owner logs in
  once in Hermes's browser and the session is reused.
- **Flaky test:** `test_experience_loop_through_memrouter` fails about 1 in 5 full-suite runs, never alone. It's
  likely concurrent memory writes on one SQLite connection. Default: leave as is until it bites.
- **Stockholm GPU billing** ($1.07/h): default is to keep it on while housebench2 runs.

## Next step (2026-10-21)
1. Done: forms beat plain on held-out cases (85 % vs 32 %).
2. Next (deploy done): the owner logs in to Swiggy/Zomato in Hermes's browser (DCV desktop). Then rehearse food comparison (Swiggy vs Zomato)
   and one train search up to payment, without paying. If they don't: the form idea is wrong for this model, so
   rethink before building more.
3. Owner: the deploy to live Hermes, writing `upi_id`, and logging in to the sites.

## Earlier plan (2026-10-18, coding direction, kept for history)
0. **Stop the harm (≤ 1 day):**
   - remove the 4 wrong live rules and the wrong profile entry (owner's yes needed);
   - fix SQLite concurrency (connection per request + WAL);
   - finish housebench2.
1. **Measurement loop (largest, ~4–5 days):**
   - a real benchmark: the ward and rental multi-ticket projects adapted to the runner;
   - the metric is cost to working software (fix-up rounds until the hidden tests pass);
   - arms: plain Qwen loop, runner, runner + notes file, runner + memrouter;
   - benchmarks run only when Hermes is idle.
2. **Memory redesign (~4 days):**
   - directives: owner-confirmed, scoped, revocable;
   - experience: keyed by failure signature, written only from ground truth with the recall link, shown as
     concrete warnings;
   - conversation recall unchanged;
   - food and location move into their own Hermes tool.
3. **Lean runner (~2 days):** code and tests written together; stop when the deliverable passes; spec review only
   when stuck.
4. **Hermes outcomes (~1 day, after 2):** owner accepts or corrects a reply → an experience record.
5. **Model and compute only where 1 shows a need:** a stronger writer or spec model, or a second GPU for benchmark
   days.
