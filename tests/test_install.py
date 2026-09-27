import json

from horizon.install import MARK_END, MARK_START, install


def read(p):
    return json.loads(p.read_text())


def test_install_writes_mcp_hooks_and_snippet(tmp_path):
    changed = install(tmp_path, command=["/venv/bin/python", "-m", "horizon"])
    assert changed == [".mcp.json", ".claude/settings.json", "CLAUDE.md"]

    mcp = read(tmp_path / ".mcp.json")["mcpServers"]["horizon"]
    assert mcp == {"command": "/venv/bin/python", "args": ["-m", "horizon", "serve"]}

    settings = read(tmp_path / ".claude" / "settings.json")
    hooks = settings["hooks"]
    assert hooks["SessionStart"][0]["hooks"][0]["command"] == "/venv/bin/python -m horizon hook session-start"
    assert hooks["PostToolUse"][0]["matcher"] == "Bash"
    assert hooks["PreToolUse"][0]["matcher"] == "mcp__horizon__recall_context"
    assert hooks["PreToolUse"][0]["hooks"][0]["command"].endswith("hook pre-tool-use")
    assert hooks["Stop"][0]["hooks"][0]["command"].endswith("hook stop")
    assert settings["enabledMcpjsonServers"] == ["horizon"]

    md = (tmp_path / "CLAUDE.md").read_text()
    assert MARK_START in md and MARK_END in md and "record_outcome" in md


def test_install_is_idempotent_and_preserves_existing_config(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-send done"}]}]},
    }))
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    (tmp_path / "CLAUDE.md").write_text("# My project\n\nExisting rules.\n")

    install(tmp_path, command=["/old/python", "-m", "horizon"])
    install(tmp_path, command=["/new/python", "-m", "horizon"])
    install(tmp_path, command=["/new/python", "-m", "horizon"])

    settings = read(tmp_path / ".claude" / "settings.json")
    assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}
    stop_cmds = [h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"]]
    assert stop_cmds == ["notify-send done", "/new/python -m horizon hook stop"]
    assert len(settings["hooks"]["SessionStart"]) == 1
    assert settings["enabledMcpjsonServers"] == ["horizon"]

    assert set(read(tmp_path / ".mcp.json")["mcpServers"]) == {"other", "horizon"}

    md = (tmp_path / "CLAUDE.md").read_text()
    assert md.startswith("# My project") and "Existing rules." in md
    assert md.count(MARK_START) == 1


def test_install_can_skip_claude_md(tmp_path):
    assert "CLAUDE.md" not in install(tmp_path, command=["py"], claude_md=False)
    assert not (tmp_path / "CLAUDE.md").exists()
