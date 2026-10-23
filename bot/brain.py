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
DOWN = {"until": 0.0}  # after the model was unreachable, calls fail at once for a minute instead of each waiting


class ModelDown(Exception):
    """The model server (the GPU in Stockholm, or the tunnel to it) can't be reached."""


def model_ok(url: str = "") -> bool:
    try:
        with urllib.request.urlopen((url or URL).rstrip("/") + "/models", timeout=10) as r:
            return r.status == 200
    except Exception:
        return False


def chat(messages: list, url: str = "", model: str = "", key_env: str = "",
         max_tokens: int = 1500, timeout: float = 120, temperature: float = 0.2, schema: dict | None = None,
         think: bool = False) -> dict:
    """One completion -> the assistant message ({"content"}). Retries busy servers twice; an unreachable one raises
    ModelDown (and, for the default model, every call in the next minute does too, without waiting)."""
    url, model = url or URL, model or MODEL
    if url == URL and time.time() < DOWN["until"]:
        raise ModelDown("the model was unreachable a moment ago")
    body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    if url == URL:  # Qwen's reasoning: off by default (1-2 s a step); on where measured to help (assistant routing)
        body["chat_template_kwargs"] = {"enable_thinking": think}
        if think:
            body["max_tokens"] = max_tokens + 2500  # the reasoning comes out of the same budget
    if schema:  # vLLM 0.31 constrains decoding to the schema: only valid JSON of that shape can come out
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
        # measured live: ~30 % of answers stall in endless whitespace (tabs/newlines) until max_tokens, 13 s; these
        # stop it at once (the grammar allows whitespace anywhere, the server rejects disabling it per request)
        if not think:  # (reasoning text has blank lines: the stall stop would cut it)
            body["stop"] = ["\n\n\n", "\t\t\t", "    \n"]
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
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:  # nothing answers: no retry storm
            if url == URL:
                DOWN["until"] = time.time() + 60
            raise ModelDown(f"{type(e).__name__}: {getattr(e, 'reason', e)}") from e
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
    broke json.loads with "Extra data"); an answer cut off by the whitespace stop is closed and read (measured: 5 of 7
    cut-off answers recovered). {} if there is none."""
    s = text or ""
    cut = s.rstrip().rstrip(",").rstrip()
    if cut.startswith("{"):
        for suffix in ("", "}", "}}", '"}', '"}}', "]}", "]}}", '"]}}'):
            try:
                obj = json.loads(cut + suffix)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
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


