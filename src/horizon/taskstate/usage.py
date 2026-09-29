"""What an attempt really cost, read from the host's own transcript (PROJECT.md §9: capture real signals instead
of trusting the model's summary).

An attempt runs from the host's last `recall_context` to the `record_outcome` about to be called. The
PreToolUse hook sums the token usage of the host's model turns in between and the elapsed time, so outcomes
carry actual tokens and latency: the efficiency half of surprise (MEMROUTER §5) and the world model's
targets (§6). Counts and durations only; no transcript text leaves the machine.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from horizon.taskstate.checkpoints import TRANSCRIPT_TAIL_BYTES

USAGE_KEYS = {"input_tokens": "input", "output_tokens": "output", "cache_creation_input_tokens": "cache_write",
              "cache_read_input_tokens": "cache_read"}


def _tool_names(entry: dict) -> list[str]:
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    return [str(b.get("name", "")) for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]


def _when(entry: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(entry.get("timestamp")).replace("Z", "+00:00"))
    except ValueError:
        return None


def attempt_usage(transcript_path: str | None) -> dict | None:
    """Token usage and wall time since the last recall_context, or None when the transcript doesn't show one."""
    if not transcript_path or not os.path.isfile(transcript_path):
        return None
    with open(transcript_path, "rb") as f:
        f.seek(max(0, os.path.getsize(transcript_path) - TRANSCRIPT_TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").splitlines()
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # includes a partial first line from the seek
        if isinstance(entry, dict) and entry.get("type") == "assistant" and not entry.get("isSidechain"):
            entries.append(entry)
    start = next((i for i in range(len(entries) - 1, -1, -1)
                  if any(n.endswith("__recall_context") for n in _tool_names(entries[i]))), None)
    if start is None:
        return None
    start_id = (entries[start].get("message") or {}).get("id")
    # A streamed model turn is written as several entries with the same message id and usage: count it once.
    turns: dict[str, dict] = {}
    for entry in entries[start + 1:]:
        message = entry.get("message") or {}
        mid = message.get("id")
        if mid and mid != start_id and isinstance(message.get("usage"), dict):
            turns[mid] = message["usage"]
    totals = {name: sum(int(u.get(key) or 0) for u in turns.values()) for key, name in USAGE_KEYS.items()}
    began, ended = _when(entries[start]), _when(entries[-1])
    latency = int((ended - began).total_seconds() * 1000) if began and ended and ended >= began else None
    return {"tokens": sum(totals.values()), **totals, "turns": len(turns), "latency_ms": latency}
