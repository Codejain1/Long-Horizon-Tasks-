"""Memory writer: one model call turns a raw message into clean, typed memories at save time.

Replaces the keyword detectors (rules, mood, taste, dates), which only worked for the phrasings they were written
for (generality suite: rules 3/9, mood 2/6, taste 2/6, dates 3/7). The model sees the message, who said it, when,
the last few messages of the chat and the slot names already in use, and returns a list of memories:

    {"kind": fact|event|plan|rule|preference|mood|agent_action|agent_claim|chitchat,
     "memory": one clean English sentence, absolute dates ("6 October 2023"), names resolved,
     "slot": what this sets that can later change ("home address"), reusing an existing slot name, or "",
     "mood": one of MOODS when kind is mood}

Any OpenAI-compatible endpoint works (vLLM, Ollama, a cloud API). If the call fails the caller falls back to
the rule-based path, so memory never stops working.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dates as D  # noqa: E402

KINDS = ("fact", "event", "plan", "rule", "preference", "mood", "agent_action", "agent_claim", "correction", "chitchat")
MOODS = ("happy", "celebrating", "sad", "tired", "stressed", "bored", "angry", "sick", "relaxed")

PROMPT = """You maintain a personal assistant's long-term memory. Turn the NEW MESSAGE into memories.

Return JSON: {{"memories": [{{"english": ..., "about": ..., "kind": ..., "memory": ..., "slot": ..., "mood": ...}}]}}
Fill the fields in this order:
english: what this part of the message means, translated to plain English (it is fine to repeat it if already English).
about: whose feeling, taste, habit or fact it is: "owner", "assistant" or "other" (someone or something else).

kind (pick the best one per memory):
- fact: a lasting fact about the owner or their world (who, what, where, numbers, names)
- event: something that happened
- plan: something scheduled or intended for the future
- rule: ANY instruction about how the ASSISTANT should behave or reply in general, however it is phrased or in
  whatever language ("I prefer you...", "please no...", "keep it...", "never/always..."). A one-time task
  ("send me the report") is not a rule. Something the owner does themselves ("I never eat late") is not a rule.
- preference: a lasting like, dislike, diet or taste of the OWNER about their own life (food, places, things)
- mood: only when the owner describes their OWN state right now: feelings, energy, health ("I'm so tired",
  "my head hurts" -> sick, "ugh what a day"), or a big personal win or loss that clearly sets it ("got promoted!"
  -> celebrating). Set "mood" to one of: {moods}. A sentence about a movie, place, food or other people is NOT a
  mood even when it uses feeling words ("the movie was sad" is an event, not the owner being sad).
- agent_action: something the ASSISTANT did, created, booked, sent or promised (keep links, times, ids)
- agent_claim: what the ASSISTANT said about itself, its abilities or opinions
- correction: the OWNER says something the assistant said or did was wrong or not what they wanted ("no, that
  place is closed", "I said under 200", "wrong file"). memory: what was wrong and what they want instead; slot: the
  topic it is about ("food picks: budget"). A new instruction for the future is a rule, not a correction.
- chitchat: greetings, filler, nothing worth remembering

memory: one clean English sentence that still makes sense months later: translate Hinglish/other languages,
resolve "he/it/that" from the recent messages, keep exact names, numbers, links. Empty for chitchat.
Do NOT calculate dates yourself: write any time as a plain English phrase exactly like one of these and the system
will turn it into a date: "today", "tomorrow", "yesterday", "the day after tomorrow", "on Friday",
"next Tuesday", "last Monday", "in 2 weeks", "in 3 days", "on the 20th", "3 months ago", "next month", "last year"
(so "kal milte hain" -> "... tomorrow", "parso" -> "the day after tomorrow"). Keep dates that are already absolute.

slot: for things that can change later, "<entity>: <attribute>" where entity is the person/thing it is about
(e.g. "owner: home address", "owner: internet provider", "meeting with Sam: time", "Riya: city"). If one of these
existing slots means the same thing, use it exactly: {slots}
Otherwise "" for things that don't get replaced (events, one-off facts).

A long message can give several memories (one per useful fact). Negations matter ("not tired" is not tired).

Recent messages in this chat:
{recent}

NEW MESSAGE from {speaker} at {when}:
{text}"""


def _chat(url: str, model: str, prompt: str, timeout: float) -> str:
    body = {"model": model, "temperature": 0, "max_tokens": 900,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def parse(raw: str) -> list[dict]:
    """The model's JSON -> clean memory dicts (unknown kinds and moods dropped, never raises)."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    try:
        items = json.loads(m[0]).get("memories", []) if m else []
    except (json.JSONDecodeError, AttributeError):
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or it.get("kind") not in KINDS:
            continue
        mood = it.get("mood") if it.get("mood") in MOODS else ""
        if it["kind"] == "mood" and not mood:
            continue
        about = str(it.get("about") or "owner").lower()
        if it["kind"] in ("mood", "preference") and about != "owner":  # a friend's diet or a sad movie is not the owner's
            it = {**it, "kind": "fact"}
            mood = ""
        out.append({"kind": it["kind"], "memory": " ".join(str(it.get("memory") or "").split())[:600],
                    "slot": " ".join(str(it.get("slot") or "").lower().split())[:60], "mood": mood})
    return out


class Writer:
    def __init__(self, url: str, model: str = "local", timeout: float = 60, chat=_chat):
        self.url, self.model, self.timeout, self.chat = url, model, timeout, chat

    def write(self, text: str, speaker: str, when: datetime, recent: list[str] = (), slots: list[str] = ()) -> list[dict]:
        prompt = PROMPT.format(moods=", ".join(MOODS), today=when.strftime("%-d %B %Y"), weekday=when.strftime("%A"),
                               slots=json.dumps(list(slots)[:60]), recent="\n".join(recent[-4:]) or "(none)",
                               speaker=speaker, when=when.strftime("%-d %B %Y %H:%M"), text=text[:6000])
        out = parse(self.chat(self.url, self.model, prompt, self.timeout))
        for m in out:  # language by the model, date arithmetic by code: "on Friday" -> "on Friday (Friday 6 October 2023)"
            m["memory"] = D.resolve(m["memory"], when)
        return out
