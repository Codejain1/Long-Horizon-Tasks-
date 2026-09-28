"""Human approvals through MCP user-input requests (PROJECT.md §10: "human approvals via MCP's ability to request
user input mid-task").

Two protocol generations ask differently:
- up to 2025-11-25 the server sends `elicitation/create` mid-call (`ctx.elicit`);
- from 2026-07-28 the server can't send requests: the tool returns an `InputRequiredResult`, the client asks the
  user and retries the call with the answers.

The second form runs the tool twice. Work that must not repeat (scoring, recording) is done in the first round,
and its result travels in `request_state`, which comes back from the client, so it's HMAC-signed: a client can't
forge a decision or approve a different action than the one asked about.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mcp_types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult
from pydantic import BaseModel, ValidationError

from mcp.server.elicitation import render_elicitation_schema

log = logging.getLogger(__name__)
ANSWER = "answer"
_SECRET = os.environ.get("HORIZON_STATE_SECRET", "").encode() or os.urandom(32)  # per process unless configured


@dataclass
class Answer:
    action: str  # accept | decline | cancel | unsupported
    data: BaseModel | None = None

    @property
    def accepted(self) -> bool:
        return self.action == "accept" and self.data is not None


@dataclass
class Approval:
    """One human check. `question(result)` returns (message, schema) or None when nothing needs asking.
    `apply(platform, result, answer)` returns the tool's final result. `before=True` asks before the tool does
    anything (the tool body runs inside `apply`); otherwise the tool's work runs first and `apply` adjusts it."""

    kind: str
    question: Callable[[dict | None], tuple[str, type[BaseModel]] | None]
    apply: Callable[[Any, dict | None, Answer], dict]
    before: bool = False
    required: str | None = None  # error message when no human can be asked (e.g. clear_fear)


def sign(state: dict) -> str:
    raw = json.dumps(state, separators=(",", ":"), default=str).encode()
    mac = hmac.new(_SECRET, raw, hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(raw).decode() + "." + mac


def verify(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    body, mac = token.rsplit(".", 1)
    try:
        raw = base64.urlsafe_b64decode(body.encode())
    except ValueError:
        return None
    if not hmac.compare_digest(mac, hmac.new(_SECRET, raw, hashlib.sha256).hexdigest()):
        return None
    return json.loads(raw)


def can_ask(ctx) -> bool:
    try:
        caps = ctx.client_capabilities
    except Exception:  # no request context
        return False
    return bool(caps and getattr(caps, "elicitation", None) is not None)


def modern(ctx) -> bool:
    try:
        return (ctx.protocol_version or "") >= "2026-07-28"
    except Exception:
        return False


async def ask_inline(ctx, message: str, schema: type[BaseModel]) -> Answer:
    """Legacy sessions: ask mid-call."""
    try:
        r = await ctx.elicit(message, schema)
    except Exception as exc:
        log.warning("elicitation failed: %s", exc)
        return Answer("unsupported")
    return Answer(r.action, getattr(r, "data", None))


def input_required(message: str, schema: type[BaseModel], state: dict) -> InputRequiredResult:
    request = ElicitRequest(params=ElicitRequestFormParams(message=message,
                                                           requestedSchema=render_elicitation_schema(schema)))
    return InputRequiredResult(input_requests={ANSWER: request}, request_state=sign(state))


def answer_from_retry(ctx, schema: type[BaseModel]) -> Answer:
    try:
        response = (ctx.input_responses or {}).get(ANSWER)
    except Exception:
        response = None
    if response is None:
        return Answer("unsupported")
    action = getattr(response, "action", "cancel")
    if action != "accept":
        return Answer(action)
    try:
        return Answer("accept", schema.model_validate(getattr(response, "content", None) or {}))
    except ValidationError:
        return Answer("cancel")


def resumed_state(ctx) -> dict | None:
    try:
        return verify(ctx.request_state)
    except Exception:
        return None
