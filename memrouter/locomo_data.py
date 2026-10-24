"""LoCoMo questions for training the recall engine (fast_recall / learned_recall / finetune_embed CLIs only; the
service never imports this). Data: ~/.cache/horizon-bench/locomo/locomo10.json, downloaded on first use."""

from __future__ import annotations

import random
import urllib.request
from collections import defaultdict
from pathlib import Path

DATA = Path.home() / ".cache" / "horizon-bench" / "locomo" / "locomo10.json"
URL = "https://raw.githubusercontent.com/snap-research/LoCoMo/main/data/locomo10.json"


def ensure_data() -> None:
    if DATA.is_file():
        return
    DATA.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(URL, timeout=120) as resp:
        DATA.write_bytes(resp.read())


def flatten(data: list[dict]) -> list[dict]:
    return [{"id": f"{s['sample_id']}:{qa['question'][:48]}", "sample_id": s["sample_id"], "question": qa["question"],
             "answer": str(qa.get("answer", "")), "category": int(qa["category"])}
            for s in data for qa in s["qa"]]


def sample_items(items: list[dict], n: int, seed: int = 11) -> list[dict]:
    """The fixed 150-question test sample (stratified by category); training uses every other question."""
    if n >= len(items):
        return items
    by: dict[int, list[dict]] = defaultdict(list)
    for it in items:
        by[it["category"]].append(it)
    rng = random.Random(seed)
    out: list[dict] = []
    for cat in sorted(by):
        qs = by[cat]
        rng.shuffle(qs)
        out += qs[: max(1, round(n * len(qs) / len(items)))]
    rng.shuffle(out)
    return out[:n]
