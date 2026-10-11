"""The retrieval arms every memory benchmark runs, with the same store and the same memory budget.

- `horizon`: factmem candidates (meaning + words), Jev judges each one's relevance.
- `hybrid`: the same candidates, an off-the-shelf cross-encoder judges them instead of Jev (the strong baseline:
  what a careful engineer would build without Jev).
- `rag`: the top candidates by meaning, unjudged (the standard baseline).
- `full`: the whole history in the prompt.
"""

from __future__ import annotations

import math
import os

ARMS = ["horizon", "hybrid", "rag", "full"]
KEEP = 12  # memories given to the reader, the same for every retrieval arm
RERANKER = "Xenova/ms-marco-MiniLM-L-6-v2"  # the standard passage reranker; bge-reranker-base was ~9x slower on CPU


class CrossEncoderJudge:
    """Answers factmem's relevance questions ("m0", "m1", ...) with a cross-encoder's score, mapped into 0.5-1:
    the order is the cross-encoder's, and factmem's floors (0.15; 0.3 for counting questions), set for Jev's
    calibrated probabilities, never cut it, as a reranker is used: top k by score. Any other question
    (supersession pairs) raises, and factmem carries on without it."""

    def __init__(self, model: str = RERANKER):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.encoder = TextCrossEncoder(model_name=model)

    def ask(self, state: dict, questions: dict) -> dict:
        from horizon.decision.scorers import Answer

        if "memories" not in state:
            raise ValueError("the cross-encoder only judges relevance")
        scores = list(self.encoder.rerank(state["question"], [m["text"] for m in state["memories"]]))
        return {f"m{j}": Answer(0.5 + 0.5 / (1 + math.exp(-s))) for j, s in enumerate(scores)}


def make_judge(arm: str):
    if arm == "horizon":
        from horizon.decision.scorers import JevScorer, typesafe_key

        os.environ.setdefault("TYPESAFE_API_KEY", typesafe_key() or "")
        return JevScorer()
    if arm == "hybrid":
        return CrossEncoderJudge()
    return None
