"""evals/longhorizon/score_h2h.py on a minimal fake run."""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCORE = REPO / "evals" / "longhorizon" / "score_h2h.py"


def test_score_h2h_counts_rules(tmp_path: Path):
    scenario_dir = REPO / "evals" / "longhorizon" / "scenarios" / "rental"
    rules = json.loads((scenario_dir / "scenario.json").read_text())["rule_checks"]
    passed = list(rules.keys())[:2]
    failed = list(rules.keys())[2:]
    h2h = tmp_path / "h2h" / "r1" / "notes" / "rental-notes"
    h2h.mkdir(parents=True)
    (h2h / "results.json").write_text(
        json.dumps(
            {
                "scenario": "rental",
                "arm": "notes",
                "model": "sonnet",
                "sessions": [
                    {
                        "session": 1,
                        "aborted": False,
                        "hidden": {"passed": passed, "failed": failed},
                        "cost_usd": 0.1,
                        "wall_s": 60,
                        "tokens": 1000,
                    }
                ],
            }
        )
    )
    out = subprocess.run(
        [sys.executable, str(SCORE), str(tmp_path / "h2h")],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "2/9" in out.stdout or "2/ 9" in out.stdout.replace(" ", "")
