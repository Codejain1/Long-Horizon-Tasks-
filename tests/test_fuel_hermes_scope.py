"""Bot fuel loop: hermes-scoped failures from /outcome show up on /search as warnings."""

import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent.parent / "memrouter")]
from test_memory_service import memory  # noqa: E402


def test_hermes_outcome_warning_on_search(tmp_path):
    mem = memory(tmp_path)
    mem.fails.record("hermes", [{"check": "owner_correction", "error": "keep every dish under ₹200"}])
    r = mem.search("order food", 200, scope="hermes")
    assert r["warnings"][0]["error"] == "keep every dish under ₹200"
    assert r["warning_id"]
