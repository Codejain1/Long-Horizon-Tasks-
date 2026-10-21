"""Model calls (temperature 0.2 by default: reading booking details must be repeatable). Qwen3-14B on the Stockholm vLLM (thinking off: 1 s a step instead of 9-65 s, measured 2026-10-21),
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


def chat(messages: list, url: str = "", model: str = "", key_env: str = "",
         max_tokens: int = 1500, timeout: float = 120, temperature: float = 0.2, schema: dict | None = None) -> dict:
    """One completion -> the assistant message ({"content"}). Retries busy servers twice."""
    url, model = url or URL, model or MODEL
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if url == URL:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    if schema:  # vLLM 0.31 constrains decoding to the schema: only valid JSON of that shape can come out
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
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


def extract(messages: list, schema: dict, chat_fn=None, **kw) -> dict:
    """Structured answer from the model, forced to `schema` (no hand parsing of free text: that was the biggest
    source of live bugs: commas in numbers, calls written as text, a list followed by more text). The parse below
    only matters for endpoints that ignore response_format."""
    fn = chat_fn or chat
    got = first_json(fn(messages, schema=schema, **kw).get("content") or "")
    if not all(k in got for k in schema.get("required", [])):
        # seen live: one run kept generating inside the JSON until max_tokens (29 s, nothing usable): retry once,
        # a little warmer so it doesn't take the same path
        got = first_json(fn(messages, schema=schema, **{**kw, "temperature": 0.5}).get("content") or "")
    return got


def first_json(text: str) -> dict:
    """The first JSON object in a reply, ignoring anything around or after it (seen live: a list, then more text,
    broke json.loads with "Extra data"). {} if there is none."""
    s = text or ""
    for i, ch in enumerate(s):
        if ch == "{":
            for candidate in (s[i:], re.sub(r"(?<=\d),(?=\d{3}\b)", "", s[i:])):
                try:
                    obj, _ = json.JSONDecoder().raw_decode(candidate)
                    if isinstance(obj, dict):
                        return obj
                except json.JSONDecodeError:
                    continue
    return {}


