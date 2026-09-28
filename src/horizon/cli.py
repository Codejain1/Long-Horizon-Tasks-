"""Command line: ``horizon serve | hook | stats | install-claude-code``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="horizon", description="Long-horizon agent platform.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="Run the MCP server.")
    p.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("hook", help="Run a Claude Code hook (reads the hook JSON on stdin).")
    p.add_argument("name", choices=["session-start", "pre-tool-use", "post-tool-use", "stop"])

    sub.add_parser("stats", help="Show invocation reliability stats.")

    p = sub.add_parser("export-decisions", help="Write the world-model decision log as JSON Lines.")
    p.add_argument("--out", default="-", help="Output file (default: stdout).")
    p.add_argument("--with-outcomes-only", action="store_true", help="Only decisions with a recorded outcome.")

    p = sub.add_parser("install-claude-code", help="Add the MCP server, hooks and CLAUDE.md snippet to a project.")
    p.add_argument("--dir", default=".", help="Project directory (default: current).")
    p.add_argument("--no-claude-md", action="store_true", help="Don't touch CLAUDE.md.")

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        from horizon.server import serve

        serve(args.transport, args.host, args.port)
    elif args.cmd == "hook":
        from horizon.hooks import run_hook

        out = run_hook(args.name, sys.stdin.read())
        if out:
            print(out)
    elif args.cmd == "stats":
        from horizon.config import Settings
        from horizon.db import connected
        from horizon.taskstate.store import TaskStore

        with connected(Settings.from_env().db_url) as db:
            print(json.dumps(TaskStore(db).stats(), indent=2))
    elif args.cmd == "export-decisions":
        from horizon.config import Settings
        from horizon.db import connected
        from horizon.decision.log import DecisionLog

        settings = Settings.from_env()
        with connected(settings.db_url) as db:
            out = sys.stdout if args.out == "-" else open(args.out, "w")
            n = 0
            for record in DecisionLog(db).export(settings.team_id, args.with_outcomes_only):
                out.write(json.dumps(record) + "\n")
                n += 1
            if out is not sys.stdout:
                out.close()
                print(f"Wrote {n} decisions to {args.out}")
    elif args.cmd == "install-claude-code":
        from horizon.install import install

        changed = install(Path(args.dir), claude_md=not args.no_claude_md)
        print("Updated: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
