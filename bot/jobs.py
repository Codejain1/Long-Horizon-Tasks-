"""Hermes's cron jobs as the bot runs them. `load` converts Hermes's jobs.json on the server (so the owner's
Telegram id and the schedules never enter the repo); `Jobs` gathers inputs and makes the one model call per job.

Model jobs, by name (the job prompts are Hermes's own, unchanged; each gets the memory files as context):
- pipeline-canary: a real model call, then the exact line the job asks for.
- daily-companion: candidates from Hacker News' front page and Google News India (the model can't browse), plus
  what this job sent before (Hermes's context_from: self).
- weekly-reflect: LEARNED.md and the owner's last 7 days of messages. Writes LEARNED.md only when the reply keeps
  every heading and stays under 100 lines; with no owner messages it changes nothing and says so (as the prompt
  requires), without a model call.
"""

from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import brain

UA = "Mozilla/5.0 (compatible; owner-assistant/0.1)"
FEEDS = {"hn": "https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30",
         "news": "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"}


def load(hermes_jobs: Path) -> list[dict]:
    """Hermes jobs.json -> bot jobs. Kinds: script (no_agent), monitor (monitor_script, seeded with Hermes's last
    output so the switch-over doesn't fire every monitor), model (everything else)."""
    d = json.loads(Path(hermes_jobs).read_text())
    out = []
    for j in d.get("jobs", d):
        kind = "script" if j.get("no_agent") else "monitor" if j.get("monitor_script") else "model"
        out.append({"name": j["name"], "kind": kind, "schedule": j["schedule"]["expr"], "deliver": j["deliver"],
                    "enabled": bool(j.get("enabled", True)) and not j.get("paused_at"), "prompt": j.get("prompt", ""),
                    "script": j.get("script") or j.get("monitor_script"), "seed": _seed(j.get("monitor_state")),
                    "last_run": (j.get("last_run_at") or "")[:16] or None})
    return out


def _seed(state):
    if isinstance(state, dict):
        state = state.get("last_output") or state.get("output") or state.get("text")
    return state if isinstance(state, str) else None


def _get(url: str, timeout: float = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def candidates(fetch=_get) -> str:
    lines = []
    try:
        for h in json.loads(fetch(FEEDS["hn"])).get("hits", [])[:25]:
            lines.append(f"- [HN {h.get('points', 0)} pts] {h.get('title')} {h.get('url') or ''}".strip())
    except Exception as e:
        lines.append(f"- (Hacker News unavailable: {e!r})")
    try:
        for item in ET.fromstring(fetch(FEEDS["news"])).iter("item"):
            lines.append(f"- [India news] {item.findtext('title')} {item.findtext('link')}")
            if len(lines) > 50:
                break
    except Exception as e:
        lines.append(f"- (Google News unavailable: {e!r})")
    return "\n".join(lines)


REFLECT = {"type": "object", "properties": {"learned_md": {"type": "string"}, "message": {"type": "string"}},
           "required": ["learned_md", "message"]}


class Jobs:
    def __init__(self, memories: Path, log, state: dict | None = None, chat=brain.chat, fetch=_get):
        """log(days) -> [(utc iso, role, text)] of the owner's conversation; state: the scheduler's state."""
        self.mem, self.log, self.state, self.chat, self.fetch = Path(memories), log, state or {}, chat, fetch

    def _memory(self) -> str:
        parts = [f"## {p.name}\n{p.read_text()[:6000]}" for p in sorted(self.mem.glob("*.md")) if p.stat().st_size]
        return "\n\n".join(parts)

    def gather(self, job: dict) -> str:
        if job["name"] == "daily-companion":
            before = "\n".join(f"- {s['at']}: {s['text'][:200]}" for s in self.state.get(job["name"], {}).get("sent", []))
            return (f"Today's candidates (you cannot browse; pick from these):\n{candidates(self.fetch)}\n\n"
                    f"What you sent on earlier days (don't repeat a subject):\n{before or '- nothing yet'}")
        if job["name"] == "weekly-reflect":
            msgs = self.log(7)
            return "\n".join(f"{at} {role}: {text[:500]}" for at, role, text in msgs)
        return ""

    def think(self, job: dict, ctx: str) -> str:
        if job["name"] == "pipeline-canary":
            self.chat([{"role": "user", "content": "Say ok."}], max_tokens=5)
            return f"✅ Weekly canary OK — model={brain.MODEL}@{brain.URL}, time={datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
        if job["name"] == "weekly-reflect":
            return self._reflect(job, ctx)
        system = (f"{job['prompt']}\n\nYour memory files:\n{self._memory()}\n\nReply with only the message to send "
                  "(it is delivered as is).")
        return (self.chat([{"role": "system", "content": system}, {"role": "user", "content": ctx or "Run now."}])
                .get("content") or "").strip()

    def _reflect(self, job: dict, ctx: str) -> str:
        if not any(" owner:" in line for line in ctx.splitlines()):
            return "No messages from Kartik this week: LEARNED.md unchanged."
        path = self.mem / "LEARNED.md"
        old = path.read_text() if path.exists() else ""
        system = (f"{job['prompt']}\n\nCurrent LEARNED.md:\n{old}\n\nAnswer with learned_md (the whole new file) "
                  "and message (the one line for Kartik).")
        out = brain.extract([{"role": "system", "content": system}, {"role": "user", "content": ctx}], REFLECT, self.chat,
                            max_tokens=3000)
        new = str(out.get("learned_md") or "")
        heads = re.findall(r"^#+ .*$", old, re.M)
        if not new.strip() or len(new.splitlines()) > 100 or any(h not in new for h in heads):
            return "⚠ weekly-reflect: the model's rewrite lost a heading or broke the format; LEARNED.md unchanged."
        path.write_text(new)
        return str(out.get("message") or "Reflected; LEARNED.md updated.")[:300]
