#!/usr/bin/env bash
# Horizon reliability demo: run 5 small coding tasks as separate headless Claude Code sessions,
# with Horizon declared in the project's .mcp.json and its hooks installed, then report how
# reliably the tools were called.
#
#   demo/reliability/run.sh [OUT_DIR]          # full run (uses your Claude Code login)
#   demo/reliability/run.sh --setup-only DIR   # only prepare DIR/project for a manual session
#   CLAUDE_MODEL=sonnet demo/reliability/run.sh OUT   # pick the host model (default: your Claude Code default)
#
# Needs: `claude` on PATH and logged in; the repo venv (uv venv -p 3.12 && uv pip install -e ".[dev]").
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PY="${PYTHON:-$REPO/.venv/bin/python}"

SETUP_ONLY=0
if [[ "${1:-}" == "--setup-only" ]]; then SETUP_ONLY=1; shift; fi
OUT="${1:-$(mktemp -d)/horizon-reliability}"

[[ -x "$PY" ]] || { echo "No venv at $PY. Run: uv venv -p 3.12 && uv pip install -e '.[dev]'" >&2; exit 1; }
[[ -e "$OUT/project" ]] && { echo "$OUT/project already exists; pick a fresh OUT_DIR" >&2; exit 1; }

# The venv goes first on PATH so `python -m pytest` and `pytest` work inside the demo project.
export PATH="$(dirname "$PY"):$PATH"
# Everything (server and hooks) uses one fresh store for this run.
export HORIZON_DB_URL="sqlite:///$OUT/horizon.db"
export HORIZON_EMBEDDER="${HORIZON_EMBEDDER:-fastembed}"

mkdir -p "$OUT/logs"
cp -r "$HERE/project" "$OUT/project"
cd "$OUT/project"
git init -q
"$PY" -m horizon install-claude-code --dir . >/dev/null
git -c user.name=demo -c user.email=demo@example.invalid add -A
git -c user.name=demo -c user.email=demo@example.invalid commit -qm "demo project with Horizon"
echo "Project ready: $OUT/project (Horizon store: $OUT/horizon.db)"

if [[ $SETUP_ONLY == 1 ]]; then
  echo "Now run 'claude' in $OUT/project (approve the horizon server when asked), paste the prompts from"
  echo "$HERE/TASKS.md one per session, then: $PY $HERE/report.py $OUT"
  exit 0
fi

command -v claude >/dev/null || { echo "claude CLI not found on PATH" >&2; exit 1; }

"$PY" - "$HERE/tasks.json" <<'EOF' > "$OUT/tasks.tsv"
import json, sys
for t in json.load(open(sys.argv[1])):
    print(f"{t['id']}\t{t['check']}\t{t['prompt']}")
EOF

while IFS=$'\t' read -r id check prompt; do
  echo "== $id"
  # One fresh session per task, as a user would start one. --mcp-config loads the same .mcp.json
  # without the interactive approval prompt; hooks come from .claude/settings.json.
  # Isolation: only the project's settings/hooks and only Horizon's MCP server, so the user's own
  # hooks and MCP servers can't change the result. --include-hook-events shows whether hooks fired.
  claude -p "$prompt" \
    --output-format stream-json --verbose --include-hook-events \
    --setting-sources project,local --strict-mcp-config \
    ${CLAUDE_MODEL:+--model "$CLAUDE_MODEL"} \
    --mcp-config .mcp.json \
    --permission-mode acceptEdits \
    --allowedTools "mcp__horizon__start_task" "mcp__horizon__recall_context" "mcp__horizon__record_outcome" \
                   "Bash(python -m pytest:*)" "Bash(python3 -m pytest:*)" "Bash(pytest:*)" "Bash(python:*)" "Bash(python3:*)" "Bash(git -C:*)" "Bash(git restore:*)" "Read" "Grep" "Glob" "Edit" "Write" \
    < /dev/null > "$OUT/logs/$id.jsonl" 2> "$OUT/logs/$id.stderr" || echo "   session exited non-zero (see logs/$id.stderr)"
  if python -m pytest -q "$check" > "$OUT/logs/$id.check.txt" 2>&1; then echo "   tests pass"; else echo "   tests FAIL"; fi
done < "$OUT/tasks.tsv"

echo
"$PY" "$HERE/report.py" "$OUT"
echo "Report: $OUT/report.json"
