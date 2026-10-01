"""attrs' CI requires a news fragment in changelog.d/ for every user-visible change (see .github/CONTRIBUTING.md)."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def changed(path: str) -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all", "--", path], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [line[3:] for line in out.splitlines()]


def test_source_changes_have_a_changelog_fragment():
    if not changed("src"):
        pytest.skip("no source changes")
    fragments = [p for p in changed("changelog.d") if re.search(r"changelog\.d/[^/]+\.(change|breaking|deprecation)\.md$", p)]
    assert fragments, ("Add a news fragment for your change: changelog.d/<issue or PR number>.change.md "
                       "(see .github/CONTRIBUTING.md).")
