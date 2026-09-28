# Connecting Horizon to Codex

Horizon works with Codex the same way it works with Claude Code: an MCP server, hooks, and an instruction snippet. Codex reads **Claude-format hooks** (same events, the same `Bash` and `mcp__server__tool` tool names, the same outputs), so the hooks are identical. Everything in [CLAUDE_CODE.md](CLAUDE_CODE.md) applies unless this page says otherwise.

## Setup

```bash
cd /path/to/your/project
/path/to/horizon/.venv/bin/horizon install-codex            # --no-agents-md to skip AGENTS.md
codex                                                        # trust the project, review hooks with /hooks
```

`install-codex` writes:
- **`.codex/config.toml`** with a `[mcp_servers.horizon]` table:
  - `default_tools_approval_mode = "approve"`, because Horizon's tools only touch Horizon's own store (the human-only actions still ask you through MCP user input);
  - `env_vars` listing Horizon's settings, so the server sees the same `HORIZON_DB_URL`, scorer and keys as the hooks. It lists names only; no values are written.
- **`.codex/hooks.json`** with SessionStart, PreToolUse (on `recall_context`), PostToolUse (on `Bash`) and Stop.
- **The Horizon snippet in `AGENTS.md`**, the same text as the CLAUDE.md snippet.
- **`.horizon/` in `.gitignore`**, for spikes.

Codex loads project config and hooks **only after you trust the project**, and asks you to review new or changed hooks once (`/hooks`). Codex doesn't pass your shell environment to MCP servers; that's why `env_vars` is there.

## Verified with a real Codex session

Codex 0.154, `codex exec`, demo task "fix `parse_duration`" (`demo/reliability`):
- It followed AGENTS.md: `start_task` → baseline `pytest` → `recall_context` → edit → `pytest` → `record_outcome`.
- **Hooks fired:** both test runs were captured with parsed counts. The first became the task's baseline (18 tests already failing, from other demo tasks), and all 3 git checkpoints were taken and attached.
- The final outcome was judged against the baseline: success 1.0 even though 16 unrelated stubs still failed, and no false rollback.

Two problems found in that run are fixed in the installer: MCP calls were blocked until pre-approved, and the server used a different database from the hooks until `env_vars` was added.

## Differences from Claude Code

- **Checkpoints are git only.** The Claude Code `/rewind` reference comes from Claude Code's transcript format, so `restore.claude_code` is empty under Codex.
- **Approvals:** not yet tested with Codex. If Codex can't show an MCP user-input request, every fallback is safe: a high-stakes tie comes back as `ask_human` (the agent asks you in chat), `delete_memory` goes ahead but stays reversible (episodes are archived and every removal keeps a snapshot) and is logged as unconfirmed, and `clear_fear` refuses and points to `horizon clear-fear`. In `codex exec` there's no one to ask, so nothing high-stakes is approved.
- **Automation:** for CI-style runs, `codex exec --dangerously-bypass-hook-trust -c 'projects."<path>".trust_level="trusted"'` skips the trust prompts. Only use it for projects you control.

## Hosted mode

```bash
export HORIZON_API_KEY=hzn_...
horizon install-codex --hosted https://horizon.example.com
```

This writes `url`, `bearer_token_env_var = "HORIZON_API_KEY"` and an `X-Horizon-Project` header (a hash of the project path). The hooks run with `--remote`. See [HOSTING.md](HOSTING.md).
