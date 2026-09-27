"""Embedding interface (MEMROUTER.md §4). The model can be swapped behind it."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


class Embedder(Protocol):
    model_name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) float32 array of L2-normalised vectors."""


def _normalise(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32)


class FastEmbedEmbedder:
    """Default: BAAI/bge-small-en-v1.5 via fastembed (CPU-only, 384 dims)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", dim: int = 384):
        from fastembed import TextEmbedding  # optional dependency

        self.model_name = model_name
        self.dim = dim
        self._model = TextEmbedding(model_name=model_name)

    def embed(self, texts: list[str]) -> np.ndarray:
        return _normalise(np.array(list(self._model.embed(texts)), dtype=np.float32))


_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by for from how i in is it of on or should the this to use using we what "
    "which with".split()
)


def _stem(token: str) -> str:
    """Crude suffix stripping, so "dates"/"date" and "parsing"/"parse" share features."""
    for suffix in ("ing", "ed", "es", "s", "e"):
        if len(token) > len(suffix) + 2 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


class HashEmbedder:
    """Deterministic, offline bag-of-words embedder (unigrams + bigrams, feature hashing).

    Used when fastembed or its model download is unavailable, and in tests.
    """

    def __init__(self, dim: int = 384):
        self.model_name = f"hash-bow-v1-{dim}"  # bump the version if features change
        self.dim = dim

    def _index(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "little")
        return value % self.dim, 1.0 if (value >> 63) & 1 else -1.0

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = [_stem(t) for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]
            features = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:])]
            for feature in features:
                idx, sign = self._index(feature)
                out[row, idx] += sign
        return _normalise(out)


def make_embedder(kind: str, dim: int = 384) -> Embedder:
    if kind == "hash":
        return HashEmbedder(dim)
    if kind == "fastembed":
        try:
            return FastEmbedEmbedder(dim=dim)
        except Exception as exc:  # missing package or model download blocked
            log.warning("fastembed unavailable (%s); falling back to the hash embedder", exc)
            return HashEmbedder(dim)
    raise ValueError(f"unknown embedder: {kind!r}")
