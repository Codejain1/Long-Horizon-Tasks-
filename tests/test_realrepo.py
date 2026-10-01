"""The real-repo eval's changelog check (evals/realrepo/ci_checks), on a tiny git repo."""

import shutil
import subprocess
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1] / "evals" / "realrepo"


def run_check(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rA", "ci_checks"], cwd=repo,
                          capture_output=True, text=True)


def test_changelog_check_wants_a_fragment_for_source_changes(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "changelog.d").mkdir()
    (repo / "src" / "m.py").write_text("x = 1\n")
    (repo / "changelog.d" / "1.change.md").write_text("old\n")
    shutil.copytree(EVAL / "ci_checks", repo / "ci_checks")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@e"]
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run([*git, "add", "src", "changelog.d"], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=repo, check=True)

    assert "SKIPPED" in run_check(repo).stdout  # no source change yet
    (repo / "src" / "m.py").write_text("x = 2\n")
    out = run_check(repo).stdout
    assert "FAILED ci_checks/test_changelog.py::test_source_changes_have_a_changelog_fragment" in out
    (repo / "changelog.d" / "2.notes.md").write_text("wrong type\n")
    assert "FAILED" in run_check(repo).stdout
    (repo / "changelog.d" / "2.change.md").write_text("x is 2 now\n")
    assert "PASSED" in run_check(repo).stdout
