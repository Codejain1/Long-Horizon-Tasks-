"""Our embedding memory (fine-tuned bge-small + learned ranker) as Claude Code's memory, over MCP (stdio).

    ~/horizon-evals/reader-venv/bin/python claude/embed_memory_mcp.py      # started by Claude Code (.mcp.json)
    ~/horizon-evals/reader-venv/bin/python claude/embed_memory_mcp.py --seed DIR   # import *.md memory files

Tools: remember(text, kind) saves one memory; recall(query, budget) returns the best memories within the token
budget. Data: ~/.embed-memory-claude (SQLite). Same engine as the Hermes service, loaded in-process.
"""

from __future__ import annotations

import pickle
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "memrouter"))
import memory_service as M  # noqa: E402

HOME = Path.home() / ".embed-memory-claude"
MODEL = Path.home() / "horizon-evals" / "embed-test"
RANKER = Path.home() / "horizon-evals" / "ranker-embed-test.pkl"
_mem = None


def memory() -> M.Memory:
    global _mem
    if _mem is None:  # loaded on first use: the model takes a few seconds
        _mem = M.Memory(HOME, M.Encoder(str(MODEL)), pickle.loads(RANKER.read_bytes()))
    return _mem


def seed(folder: Path) -> int:
    """One memory per bullet or paragraph of each memory file (front matter dropped), tagged with the file's type."""
    n = 0
    for f in sorted(folder.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        text = f.read_text()
        kind = (re.search(r"^\s*type:\s*(\w+)", text, re.M) or [None, "note"])[1]
        body = re.sub(r"^---.*?---\s*", "", text, flags=re.S)
        # one memory per bullet (continuation lines joined) or per plain paragraph: one fact each
        items = re.split(r"\n(?=- )|\n\s*\n", body)
        for item in items:
            item = " ".join(item.split()).lstrip("- ")
            if item:
                memory().add(f"[{f.stem}] {item}", kind, "claude-code", f.stat().st_mtime)
                n += 1
    return n


def main() -> int:
    if "--seed" in sys.argv:
        print("seeded", seed(Path(sys.argv[sys.argv.index("--seed") + 1]).expanduser()), "memories")
        return 0
    from mcp.server.mcpserver import MCPServer

    app = MCPServer(name="embed-memory", instructions="Long-term memory: recall before work, remember new facts.")

    @app.tool()
    def remember(text: str, kind: str = "note") -> str:
        """Save one memory (a fact, decision, preference or correction). kind: user | feedback | project | reference | note."""
        return f"saved ({memory().add(text, kind, 'claude-code')} piece(s))"

    @app.tool()
    def recall(query: str, budget: int = 500) -> str:
        """Find the memories most relevant to `query`, packed into about `budget` tokens, oldest first."""
        r = memory().search(query, budget)
        return r["context"] or "(nothing relevant remembered)"

    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
