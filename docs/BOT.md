# Our bot (replaces Hermes)

Why: measured on Qwen3-14B, code-run flows with the model filling blanks beat a free-roaming agent
(forms 85 % vs plain 32 % on held-out requests). Hermes is a free-roaming agent: ~25 tools and 26k
characters of instructions, so the small model picked the wrong tool or asked the owner to do the work.
In the bot, code decides every step; the model makes one small call per step (route, extract, choose, write).

## Pieces
| File | Job |
|---|---|
| `bot/main.py` | the loop: poll WhatsApp and Telegram, handle each owner message, tick the scheduler every minute |
| `bot/channels.py` | WhatsApp through Hermes's Node bridge (`bridge.js`, run as its own service, same session: no re-pairing); Telegram Bot API |
| `bot/brain.py` | model calls (Qwen on the Stockholm vLLM, thinking off; OpenRouter optional per job) |
| `bot/router.py` | one call: which flow, with its details (`task_start`-style tool call) |
| `bot/flows.py` | chat (memrouter context), bookings as forms (`booking.py`), YES/NO, rules, STOP/RESUME |
| `bot/scheduler.py` | Hermes's 8 cron jobs: script jobs, monitor jobs (run, compare, summarise on change), model jobs |
| `bot/playbooks/` | per site, code that drives the logged-in desktop Chrome over CDP (milestone 2) |

Memory stays in memrouter (`/search`, `/add`, rules, taste, mood) and the booking store. Secrets stay in the
existing env file, read only by the service (systemd `EnvironmentFile`).

## Milestones and acceptance criteria
**M1: replace Hermes (switch-over possible after this).**
1. A WhatsApp or Telegram message from the owner gets a reply. Messages from anyone else are ignored.
2. `STOP` blocks every action until `RESUME`.
3. A booking request goes through the form: one question for what's missing, then the summary, then the owner's
   YES before anything happens.
4. All 8 jobs run on their Hermes schedules (UTC) and deliver to the same Telegram chat:
   - script jobs send their output, or nothing when it's empty;
   - monitor jobs stay silent while the output is unchanged and send one summary when it changes (an ERROR line
     counts as a change);
   - model jobs send their message.
5. Median reply time under 10 s for chat (measured on live).
6. Rollback is one command: stop the bot, start `hermes-gateway`.

**M2: food on Swiggy.** The playbook reads order history, searches a dish, reads real prices, and fills the cart up
to checkout. The model only picks among real options. Tested on saved pages, then live, without ordering.

**M3: bus (redbus) and train (ixigo)**, up to the payment page.

## Not carried over (Hermes extras unused by the owner in 30 days)
- the terminal and coding sandbox;
- image generation and skills;
- Discord and Slack;
- kanban.

Add them back when asked.
