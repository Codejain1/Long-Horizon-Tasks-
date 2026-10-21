"""Model calls. Qwen3-14B on the Stockholm vLLM (thinking off: 1 s a step instead of 9-65 s, measured 2026-10-21),
or any OpenAI-compatible endpoint set per call."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

URL = os.environ.get("BOT_MODEL_URL", "http://127.0.0.1:8000/v1")
MODEL = os.environ.get("BOT_MODEL", "local")


def chat(messages: list, tools: list | None = None, url: str = "", model: str = "", key_env: str = "",
         max_tokens: int = 1500, timeout: float = 120) -> dict:
    """One completion -> the assistant message ({"content", "tool_calls"?}). Retries busy servers twice."""
    url, model = url or URL, model or MODEL
    body = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    if url == URL:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if tools:
        body["tools"] = [{"type": "function", "function": t} for t in tools]
    headers = {"Content-Type": "application/json"}
    if key_env and os.environ.get(key_env):
        headers["Authorization"] = f"Bearer {os.environ[key_env]}"
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(body).encode(), headers)
    for wait in (3, 10, None):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            if wait is None or e.code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _json(s: str):
    """json.loads, retried with thousands commas taken out of numbers (Qwen writes "max_price": 7,500)."""
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return json.loads(re.sub(r"(?<=\d),(?=\d{3}\b)", "", s))


def tool_call(msg: dict) -> tuple[str, dict] | None:
    """The first tool call of a reply as (name, args), or None. Qwen sometimes writes the call as text
    (`<tool_call>{...}</tool_call>`, seen live on "under 7,500"): that is read too."""
    calls = msg.get("tool_calls") or []
    if not calls:
        m = re.search(r"<tool_call>\s*(\{.*\})\s*(</tool_call>|$)", msg.get("content") or "", re.S)
        try:
            d = _json(m[1]) if m else None
        except json.JSONDecodeError:
            d = None
        return (d["name"], d.get("arguments") or {}) if isinstance(d, dict) and d.get("name") else None
    f = calls[0]["function"]
    args = f.get("arguments") or {}
    try:
        args = _json(args) if isinstance(args, str) else args
    except json.JSONDecodeError:
        args = {}
    return f["name"], args if isinstance(args, dict) else {}
