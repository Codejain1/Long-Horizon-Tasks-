import io
import json

import pytest

from textkit.cli import main


def test_slugify_subcommand(capsys):
    assert main(["slugify", "Hello, World!"]) == 0
    assert capsys.readouterr().out.strip() == "hello-world"


def test_title_from_a_file(tmp_path, capsys):
    f = tmp_path / "in.txt"
    f.write_text("the lord of the rings")
    assert main(["title", "--file", str(f)]) == 0
    assert capsys.readouterr().out.strip() == "The Lord of the Rings"


def test_words_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("one two  three\n"))
    assert main(["words"]) == 0
    assert capsys.readouterr().out.strip() == "3"


def test_json_output(capsys):
    assert main(["words", "a b c", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"command": "words", "result": 3}


def test_cached_word_counts(tmp_path, capsys):
    from textkit.cache import WordCountCache

    assert main(["words", "alpha beta", "--cache", str(tmp_path / "wc")]) == 0
    assert WordCountCache(tmp_path / "wc").get("alpha beta") == 2


def test_unknown_subcommand_exits_with_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["shout", "hi"])
    assert exc.value.code == 2
