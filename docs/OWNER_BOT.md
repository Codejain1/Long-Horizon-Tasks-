# Owner bot (separate repo)

The personal assistant — WhatsApp, Telegram, iPhone app, travel, food, deploy to `/opt/bot` — was split out on
**2026-10-24** so this repo stays the long-horizon **lab** without product deploy churn.

## Location

| Machine | Path |
|---|---|
| This Mac (sibling folder) | `../owner-bot` |
| Git | Create/push when ready; history before the split lives in this repo's git log |

## Shared server pieces

| Service | Repo | Path on Hermes |
|---|---|---|
| Memrouter | **Long-Horizon** (this repo) | `/opt/embed-memory` |
| Owner bot | **owner-bot** | `/opt/bot` → `/opt/bot-<stamp>` |
| Model | `hermes/stockholm_model.sh` | SSH tunnel `:8000` |

## Contract

Documented in owner-bot `docs/FUEL.md`. No Python import across repos — only HTTP (`MEMORY_URL`, OpenAI-compatible
model URL) and shared deploy conventions.

## Restoring pre-split tree

```bash
git checkout <commit-before-split> -- bot app deploy/bot bench/botsmoke.py bench/forms.py docs/BOT.md
```
