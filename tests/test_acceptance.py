"""Five-parameter acceptance sheet (evals/acceptance.py)."""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ACCEPT = REPO / "evals" / "acceptance.py"


def test_acceptance_memory_offline():
    proc = subprocess.run(
        [sys.executable, str(ACCEPT), "--memory-only"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_acceptance_decision_offline():
    proc = subprocess.run(
        [sys.executable, str(ACCEPT), "--decision-only"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
