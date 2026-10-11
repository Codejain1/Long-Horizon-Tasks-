"""The memory benchmarks' protocol: arms compared on shared questions only; tuning and test questions disjoint."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "evals" / "memory"))
from compare import compare, mcnemar  # noqa: E402
from longmemeval import split  # noqa: E402


def write(folder, arm, rows):
    (folder / f"{arm}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_a_question_one_arm_failed_is_left_out_of_every_arm(tmp_path):
    write(tmp_path, "horizon", [{"id": "a", "correct": True}, {"id": "b", "correct": True}, {"id": "c", "correct": False}])
    write(tmp_path, "rag", [{"id": "a", "correct": False}, {"id": "c", "correct": False}])  # "b" errored
    out = compare(tmp_path)
    assert "2 questions answered by every arm" in out
    assert "horizon     1/2" in out and "rag         0/2" in out and "wins 1 losses 0" in out


def test_locomo_scores_j_and_keeps_never_mentioned_apart(tmp_path):
    write(tmp_path, "horizon", [{"id": "a", "category": 1, "j": True, "correct": False},
                                {"id": "b", "category": 5, "correct": True}])
    assert "1/1 = 100.0 %   never mentioned 1/1" in compare(tmp_path)


def test_mcnemar_is_exact():
    assert mcnemar(0, 0) == 1.0
    assert round(mcnemar(6, 0), 4) == 0.0312  # 2 * 0.5 ** 6
    assert round(mcnemar(3, 3), 4) == 1.0


def test_tuning_and_test_questions_never_overlap():
    data = [{"question_id": f"q{i}", "question_type": ["a", "b", "c"][i % 3]} for i in range(300)]
    dev, test = split(data, "dev"), split(data, "test")
    assert len(dev) == 60 and len(test) == 240
    assert not {q["question_id"] for q in dev} & {q["question_id"] for q in test}
    assert split(data, "all") == data
