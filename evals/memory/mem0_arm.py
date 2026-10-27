"""The `mem0` arm: Mem0 open source (mem0ai) as the memory, under the same protocol as the other arms.

- Write: each session goes through Mem0's own `add` (its single-call additive extraction), with the session's date
  as Mem0's "current date" so relative dates ("yesterday") resolve against it (`ground_dates_at_session`).
- Mem0's full read path: spaCy installed (`mem0ai[nlp]`) for its keyword and entity search.
- Extraction LLM: the arm's answer model (composer-2.5 via Cursor), so no arm gets a different model.
- Embeddings: the same local fastembed model as factmem (bge-small-en-v1.5); store: on-disk Qdrant per
  conversation, built once and reused by every question about it (resume-safe: a `done` marker per conversation).
- Read: Mem0's `search`, top KEEP (12) like every retrieval arm, each memory shown with its session date.
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

os.environ.setdefault("MEM0_TELEMETRY", "False")  # Mem0 reports usage to its servers unless told not to

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBED_DIMS = 384


def first_json(text: str) -> str:
    """The first JSON object in a reply: Cursor's agent wraps answers in prose or ``` fences."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        return m.group(1)
    start = text.find("{")
    if start < 0:
        return '{"memory": []}'
    depth = 0
    for i, ch in enumerate(text[start:], start):
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if depth == 0:
            return text[start:i + 1]
    return '{"memory": []}'


_session = threading.local()  # the date of the session being saved, per building thread


def ground_dates_at_session() -> None:
    """Mem0 OSS dates every extraction "today" (its Platform's `timestamp` is not in OSS), so "yesterday" in a
    2023 session became a 2026 date. Give its extraction prompt the session's date as both the current and the
    observation date, as the Platform would."""
    import mem0.memory.main as main

    if getattr(main.generate_additive_extraction_prompt, "grounded", False):
        return
    original = main.generate_additive_extraction_prompt

    def grounded(*args, **kwargs):
        date = getattr(_session, "date", None)
        if date:
            kwargs["current_date"] = kwargs["timestamp"] = date
        return original(*args, **kwargs)

    grounded.grounded = True
    main.generate_additive_extraction_prompt = grounded


def make_llm_class(complete, model: str):
    """A Mem0 LLM provider backed by our `complete(prompt, model) -> (text, usage)`."""
    from mem0.llms.base import LLMBase

    class CompleteLLM(LLMBase):
        def _validate_config(self):
            pass

        def generate_response(self, messages, tools=None, tool_choice="auto", response_format=None, **kwargs):
            prompt = "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)
            if response_format:
                prompt += "\n\nReply with only the JSON object, no prose or code fences."
            text, _ = complete(prompt, model)
            return first_json(text) if response_format else text

    return CompleteLLM


class Mem0Stores:
    """One Mem0 store per conversation under `root`, built on first use."""

    def __init__(self, root: Path, complete, model: str):
        from mem0 import Memory

        ground_dates_at_session()
        self.llm_class = make_llm_class(complete, model)
        self.root, self.model, self.Memory = root, model, Memory
        self.mems: dict[str, object] = {}
        self.locks: dict[str, threading.Lock] = {}
        self.guard = threading.Lock()

    def _config(self, sid: str) -> dict:
        return {
            # Mem0 only accepts its own provider names: build with a placeholder (never called; the dummy key
            # makes sure of it) and swap in `llm_class` below.
            "llm": {"provider": "openai", "config": {"model": self.model, "api_key": "unused"}},
            "embedder": {"provider": "fastembed", "config": {"model": EMBED_MODEL, "embedding_dims": EMBED_DIMS}},
            "vector_store": {"provider": "qdrant", "config": {
                "collection_name": "locomo", "path": str(self.root / sid / "qdrant"),
                "embedding_model_dims": EMBED_DIMS, "on_disk": True}},
            "history_db_path": str(self.root / sid / "history.db"),
        }

    def get(self, sid: str, sessions: list[tuple[str, list[dict]]]):
        with self.guard:
            lock = self.locks.setdefault(sid, threading.Lock())
        with lock:
            if sid in self.mems:
                return self.mems[sid]
            (self.root / sid).mkdir(parents=True, exist_ok=True)
            mem = self.Memory.from_config(self._config(sid))
            mem.llm = self.llm_class({"model": self.model})
            progress = self.root / sid / "sessions_done"
            done = int(progress.read_text()) if progress.exists() else 0
            for i, (date, turns) in enumerate(sessions[done:], done + 1):
                _session.date = date
                msgs = [{"role": "system", "content": f"This conversation took place on {date}."}]
                msgs += [{"role": "user", "content": f"{t.get('name', t['role'])}: {t['content']}"} for t in turns]
                try:
                    mem.add(msgs, user_id=sid, metadata={"date": date})
                finally:
                    _session.date = None
                progress.write_text(str(i))  # a crash resumes at the next session, not the first
            self.mems[sid] = mem
            return mem

    def search(self, sid: str, sessions, question: str, keep: int) -> list[dict]:
        res = self.get(sid, sessions).search(question, top_k=keep, filters={"user_id": sid})
        return res["results"] if isinstance(res, dict) else res


def context_body(hits: list[dict]) -> str:
    return "\n\n".join(f"DATE: {(h.get('metadata') or {}).get('date', '?')}\n{h['memory']}" for h in hits)



if __name__ == "__main__":  # offline self-check of the reply parsing
    assert json.loads(first_json('Sure:\n```json\n{"memory": [{"text": "a"}]}\n```')) == {"memory": [{"text": "a"}]}
    assert json.loads(first_json('x {"memory": [{"text": "b {c}"}]} y'))["memory"][0]["text"] == "b {c}"
    assert first_json("no json here") == '{"memory": []}'
    print("ok")
