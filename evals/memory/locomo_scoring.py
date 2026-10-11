"""LoCoMo official QA scoring (token F1), without extra deps."""

from __future__ import annotations

import re
import string
from collections import Counter


def normalize_answer(s: str) -> str:
    s = s.replace(",", "")

    def remove_articles(text: str) -> str:
        return re.sub(r"\b(a|an|the|and)\b", " ", text)

    def white_space_fix(text: str) -> str:
        return " ".join(text.split())

    def remove_punc(text: str) -> str:
        exclude = set(string.punctuation)
        return "".join(ch for ch in text if ch not in exclude)

    return white_space_fix(remove_articles(remove_punc(s.lower())))


def f1_score(prediction: str, ground_truth: str) -> float:
    prediction_tokens = normalize_answer(prediction).split()
    ground_truth_tokens = normalize_answer(ground_truth).split()
    common = Counter(prediction_tokens) & Counter(ground_truth_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(prediction_tokens)
    recall = num_same / len(ground_truth_tokens)
    return (2 * precision * recall) / (precision + recall)


def f1_multi(prediction: str, ground_truth: str) -> float:
    predictions = [p.strip() for p in prediction.split(",")]
    ground_truths = [g.strip() for g in ground_truth.split(",")]
    return sum(max(f1_score(pred, gt) for pred in predictions) for gt in ground_truths) / len(ground_truths)


def score_locomo(prediction: str, answer: str, category: int) -> tuple[bool, float]:
    """Returns (pass at F1>=0.5 or adversarial rule, f1 or 0/1)."""
    if category == 3:
        answer = answer.split(";")[0].strip()
    if category == 5:
        ok = "no information available" in prediction.lower() or "not mentioned" in prediction.lower()
        return ok, float(ok)
    if category == 1:
        f1 = f1_multi(prediction, answer)
    else:
        f1 = f1_score(prediction, answer)
    return f1 >= 0.5, f1
