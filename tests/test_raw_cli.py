"""The benchmark's raw-CLI switch: clean config folders for Cursor and Codex only when switched on."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "evals" / "memory"))
import longmemeval  # noqa: E402


def test_raw_env_is_off_without_the_switch_and_clean_with_it(tmp_path, monkeypatch):
    monkeypatch.setattr(longmemeval, "RAW", tmp_path)
    assert longmemeval.raw_env("cursor") is None and longmemeval.raw_env("codex") is None

    (tmp_path / "ON").touch()
    assert longmemeval.raw_env("cursor")["HOME"] == str(tmp_path / "cursor-home")
    codex = longmemeval.raw_env("codex")
    assert codex["CODEX_HOME"] == str(tmp_path / "codex") and "HOME" in codex  # only Codex's folder moves
