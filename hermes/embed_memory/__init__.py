"""Hermes memory provider backed by our embedding memory service (memrouter/memory_service.py).

Every completed turn is saved; before each reply the best memories (≤500 tokens) are recalled and injected.
Fails open: if the service is down, Hermes runs without memory. Service URL: EMBED_MEMORY_URL
(default http://127.0.0.1:8765).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request
from typing import Any

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)


def _post(path: str, body: dict, timeout: float) -> dict:
    url = os.environ.get("EMBED_MEMORY_URL", "http://127.0.0.1:8765") + path
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class EmbedMemoryProvider(MemoryProvider):
    @property
    def name(self) -> str:
        return "embed_memory"

    def is_available(self) -> bool:
        return True  # local service, no credentials; reachability is handled fail-open per call

    def system_prompt_block(self) -> str:
        return ("## Your memory system (answer from this when asked about your memory)\n"
                "IMPORTANT: when anyone asks what memory you have or how you remember things, answer with exactly "
                "these facts, not with server or disk details: your long-term memory is a custom embedding memory "
                "built for you, a fine-tuned bge-small embedding model plus a learned ranker, running locally on "
                "this server with no outside memory service. Every conversation turn is saved automatically; before "
                "each reply the most relevant past messages (up to ~500 tokens) are recalled and shown to you under "
                "\"What you remember\". You can also search it with the memory_search tool.\n"
                "Each remembered line starts with its #id. Lines marked OUTDATED were replaced or corrected: never treat "
                "them as current. Lines marked (unverified) may be wrong: confirm before acting on them. If the owner "
                "says something you remember is wrong, call correct_memory with its #id and the right version; if they "
                "ask you to forget it, call forget_memory.\n\n"
                "## How to recommend food or places\n"
                "1. If the owner has not given a budget for this request, ask for it first (one short question).\n"
                "2. Call nearby_food (optionally with a cuisine) and pick ONLY from the places it returns; never name a "
                "place from your own knowledge.\n"
                "3. Respect the taste profile strictly (diet first), and use the owner's current mood to choose the vibe "
                "(comfort food when low or tired, something fun when celebrating).\n"
                "4. Reply with 3 picks: name, cuisine, distance in km, one line on why it fits their taste and mood, and "
                "the maps link. Say which area you searched.")

    @staticmethod
    def _profile_block(profile: list, mood: dict | None) -> str:
        lines = [f"- {p['text']}" for p in profile]
        if mood:
            lines.append(f"- Current mood: {mood['mood']} (from \"{mood['said']}\", {mood['when']}, "
                         f"{mood['hours_ago']} h ago){' - may be outdated' if mood['hours_ago'] > 12 else ''}")
        if not lines:
            return ""
        return "## Owner's taste profile and mood (private: never share)\n" + "\n".join(lines)

    @staticmethod
    def _location_block(loc: dict | None) -> str:
        if not loc:
            return ("## Owner's location\nNot shared yet. For anything 'near me', ask them to share it on WhatsApp "
                    "(attach > Location).")
        where = loc.get("area") or loc.get("name") or ""
        place = f"{where} " if where else ""
        stale = (" This is over 6 hours old: confirm or ask them to share their location again before recommending."
                 if loc["hours_ago"] > 6 else "")
        return (f"## Owner's current location (private: never send it to any site or person except for a search or "
                f"delivery they asked for)\n{place}{loc['lat']},{loc['lng']}, shared {loc['when']} "
                f"({loc['hours_ago']} h ago).{stale}")

    def initialize(self, session_id: str, **kwargs) -> None:
        self._write = kwargs.get("agent_context", "primary") == "primary"  # no writes from subagents/cron

    @staticmethod
    def _rules_block(rules: list) -> str:
        if not rules:
            return ""
        lines = "\n".join(f"- ({r['date']}) {r['text']}" for r in rules)
        return ("## Standing rules from the user: follow these in EVERY reply, they override your usual habits\n"
                f"{lines}")

    @staticmethod
    def _pending_block(rules: list, profile: list) -> str:
        """What a model inferred from chat as a standing rule or taste: not followed until the owner says yes."""
        lines = [f"- rule #{r['id']}: {r['text']}" for r in rules] + [f"- taste #{p['id']}: {p['text']}" for p in profile]
        if not lines:
            return ""
        return ("## Possible standing rules and tastes, waiting for the owner's OK (do NOT follow them yet)\n"
                + "\n".join(lines) + "\nAt a natural point, ask the owner once, in one short line, whether to keep "
                "each. Yes: confirm_rule / confirm_preference with its #id. No: forget_rule / forget_preference.")

    @staticmethod
    def _mistakes_block(warnings: list) -> str:
        if not warnings:
            return ""
        return ("## Mistakes the owner corrected before (don't repeat them)\n"
                + "\n".join(f"- {w['error'] or w['check']}" + (f" (corrected {w['seen']}x)" if w["seen"] > 1 else "")
                             for w in warnings))

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        try:
            # Hermes's own past replies are stored but not recalled (a wrong reply would come back as "evidence"),
            # nor are scheduled jobs' prompts (cron_* sessions: not the user talking)
            r = _post("/search", {"query": query, "budget": 500, "skip": ["Hermes"], "skip_chats": ["cron_"],
                                  "skip_kinds": ["agent_claim", "chitchat"], "scope": "hermes"}, timeout=2)
        except Exception as exc:
            logger.debug("embed_memory search failed: %s", exc)
            return ""
        ctx = r.get("context", "")
        cur = r.get("current", {})
        parts = [self._rules_block(r.get("rules", [])),
                 self._pending_block(r.get("pending_rules", []), r.get("pending_profile", [])),
                 self._mistakes_block(r.get("warnings", [])),
                 self._profile_block(cur.get("profile", []), cur.get("mood")),
                 self._location_block(cur.get("location")),
                 f"## What you remember (from past conversations)\n{ctx}" if ctx else ""]
        return "\n\n".join(p for p in parts if p)

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "",
                  messages: list | None = None, turn_author: dict | None = None) -> None:
        if not getattr(self, "_write", True) or (session_id or "").startswith("cron_"):
            return
        who = (turn_author or {}).get("name") or "user"

        def save():
            try:
                _post("/add", {"text": user_content, "speaker": who, "chat": session_id or "default",
                               "detect_rule": True}, timeout=90)
                _post("/add", {"text": assistant_content, "speaker": "Hermes", "chat": session_id or "default",
                               "role": "assistant"}, timeout=90)
            except Exception as exc:
                logger.debug("embed_memory save failed: %s", exc)

        threading.Thread(target=save, name="embed-memory-save", daemon=True).start()

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return [{"name": "remember_rule",
                 "description": "Save a standing instruction from the user that must apply to all future replies "
                                "(e.g. 'reply only with on it sir, then the final URL').",
                 "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
                {"name": "forget_rule",
                 "description": "Stop following a standing rule (by its id from list_rules) when the user revokes it.",
                 "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
                {"name": "confirm_rule", "description": "The owner said yes to a pending standing rule (by its #id).",
                 "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
                {"name": "confirm_preference", "description": "The owner said yes to a pending taste (by its #id).",
                 "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
                {"name": "forget_preference", "description": "Drop a taste (pending or kept) by its #id.",
                 "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
                {"name": "list_rules", "description": "List the user's standing rules with their ids.",
                 "parameters": {"type": "object", "properties": {}}},
                {"name": "nearby_food",
                 "description": "Real restaurants, cafes and eateries near the owner's latest shared location "
                                "(OpenStreetMap), nearest first, vegetarian-marked first, with distance and a maps link. "
                                "Use this for every food or place recommendation.",
                 "parameters": {"type": "object", "properties": {
                     "cuisine": {"type": "string", "description": "optional filter, e.g. 'japanese' or 'pizza, italian'"},
                     "radius_m": {"type": "integer", "default": 2000}}}},
                {"name": "correct_memory",
                 "description": "The owner says a remembered fact is wrong: replace memory #id with the right version.",
                 "parameters": {"type": "object", "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                                "required": ["id", "text"]}},
                {"name": "forget_memory", "description": "The owner asks you to forget memory #id.",
                 "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
                {"name": "remember_preference",
                 "description": "Save a lasting taste or lifestyle preference the owner states (diet, cuisines, spice, "
                                "places they love or avoid).",
                 "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
                {"name": "memory_search",
                 "description": "Search everything said in past conversations (any chat, any date) by meaning.",
                 "parameters": {"type": "object", "properties": {"query": {"type": "string"},
                                                                 "budget": {"type": "integer", "default": 800}},
                                "required": ["query"]}}]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs) -> str:
        try:
            if tool_name == "remember_rule":
                return json.dumps(_post("/rule", {"text": args["text"]}, timeout=10))
            if tool_name == "nearby_food":
                return json.dumps(_post("/nearby_food", {"cuisine": args.get("cuisine", ""),
                                                         "radius_m": int(args.get("radius_m", 2000))}, timeout=45))
            if tool_name == "correct_memory":
                return json.dumps(_post("/memory/correct", {"id": int(args["id"]), "text": args["text"]}, timeout=30))
            if tool_name == "forget_memory":
                return json.dumps(_post("/memory/forget", {"id": int(args["id"])}, timeout=10))
            if tool_name == "remember_preference":
                return json.dumps(_post("/profile", {"text": args["text"]}, timeout=10))
            if tool_name in ("confirm_rule", "confirm_preference"):
                path = "/rule/confirm" if tool_name == "confirm_rule" else "/profile/confirm"
                return json.dumps(_post(path, {"id": int(args["id"])}, timeout=10))
            if tool_name == "forget_preference":
                return json.dumps(_post("/profile/forget", {"id": int(args["id"])}, timeout=10))
            if tool_name == "forget_rule":
                return json.dumps(_post("/rule/forget", {"id": int(args["id"])}, timeout=10))
            if tool_name == "list_rules":
                url = os.environ.get("EMBED_MEMORY_URL", "http://127.0.0.1:8765") + "/rules"
                with urllib.request.urlopen(url, timeout=5) as r:
                    return r.read().decode()
            return json.dumps(_post("/search", {"query": args["query"], "budget": int(args.get("budget", 800))},
                                    timeout=5))
        except Exception as exc:
            return json.dumps({"error": f"memory unavailable: {exc}"})


def register(ctx) -> None:
    ctx.register_memory_provider(EmbedMemoryProvider())
