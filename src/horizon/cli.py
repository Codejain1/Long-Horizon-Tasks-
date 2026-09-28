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
    p.add_argument("--remote", help="Hosted server URL: send parsed facts there (key in HORIZON_API_KEY).")

    sub.add_parser("stats", help="Show invocation reliability stats.")

    p = sub.add_parser("create-team", help="Create a team for the hosted server and print its first API key.")
    p.add_argument("name")
    p.add_argument("--credits", type=int, default=None, help="Starting credits (default: the free starter grant).")

    p = sub.add_parser("add-credits", help="Grant credits to a team (operator; no payments yet).")
    p.add_argument("team_id")
    p.add_argument("credits", type=int)
    p.add_argument("--reason", default="operator top-up")

    sub.add_parser("consolidate", help="Run the memrouter sleep job now (e.g. nightly from cron).")

    p = sub.add_parser("clear-fear", help="Clear a fear lesson. Human only: it records who cleared it.")
    p.add_argument("lesson_id")
    p.add_argument("--by", required=True, help="Your name, recorded on the lesson.")

    p = sub.add_parser("compare-scorers", help="Replay logged decisions through Jev and the small LLM (PROJECT.md §5).")
    p.add_argument("--limit", type=int, default=50, help="Most recent decisions to replay.")

    p = sub.add_parser("purge-memory", help="Operator only: erase one episode for good (e.g. a legal request).")
    p.add_argument("episode_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True, help="Who authorised the erasure (recorded).")
    p.add_argument("--yes", action="store_true", help="Confirm: this can't be undone.")

    p = sub.add_parser("export-decisions", help="Write the world-model decision log as JSON Lines.")
    p.add_argument("--out", default="-", help="Output file (default: stdout).")
    p.add_argument("--with-outcomes-only", action="store_true", help="Only decisions with a recorded outcome.")

    p = sub.add_parser("install-codex", help="Add the MCP server, hooks and AGENTS.md snippet for Codex.")
    p.add_argument("--dir", default=".", help="Project directory (default: current).")
    p.add_argument("--no-agents-md", action="store_true", help="Don't touch AGENTS.md.")
    p.add_argument("--hosted", metavar="URL", help="Use a hosted Horizon server (key in HORIZON_API_KEY).")

    p = sub.add_parser("install-claude-code", help="Add the MCP server, hooks and CLAUDE.md snippet to a project.")
    p.add_argument("--dir", default=".", help="Project directory (default: current).")
    p.add_argument("--no-claude-md", action="store_true", help="Don't touch CLAUDE.md.")
    p.add_argument("--hosted", metavar="URL", help="Use a hosted Horizon server (key in HORIZON_API_KEY).")

    args = parser.parse_args(argv)
    from horizon.config import Settings

    if args.cmd == "serve":
        from horizon.server import serve

        serve(args.transport, args.host, args.port)
    elif args.cmd == "hook":
        from horizon.hooks import run_hook

        out = run_hook(args.name, sys.stdin.read(), remote=args.remote)
        if out:
            print(out)
    elif args.cmd == "stats":
        from horizon.db import connected
        from horizon.memrouter.graph import Graph
        from horizon.memrouter.store import EpisodeStore
        from horizon.taskstate.store import TaskStore

        settings = Settings.from_env()
        with connected(settings.db_url) as db:
            stats = TaskStore(db).stats()
            graph = Graph(db, settings.embedding_dim)
            stats["memory"] = {"lessons": len(graph.lessons(settings.team_id)),
                               "fear_lessons": len(graph.lessons(settings.team_id, fear_only=True)),
                               "links": graph.link_count(settings.team_id),
                               "archived_episodes": graph.archived_count(settings.team_id),
                               "predictor_trust": graph.predictor_stats(settings.team_id),
                               "removed": len(graph.removals(settings.team_id)),
                               **EpisodeStore(db, settings.embedding_dim).context_tokens(settings.team_id)}
            from horizon.decision.log import DecisionLog

            stats["decisions"] = DecisionLog(db).stats(settings.team_id)
            print(json.dumps(stats, indent=2))
    elif args.cmd in ("create-team", "add-credits"):
        from horizon.accounts import FREE_STARTER_CREDITS, Accounts
        from horizon.db import connected

        with connected(Settings.from_env().db_url) as db:
            accounts = Accounts(db)
            if args.cmd == "create-team":
                team_id, key = accounts.create_team(args.name, FREE_STARTER_CREDITS if args.credits is None
                                                    else args.credits)
                print(f"Team {team_id} ({args.name}), {accounts.balance(team_id)} credits.")
                print(f"API key (shown once, store it now): {key}")
            else:
                if accounts.team(args.team_id) is None:
                    print(f"No team {args.team_id}", file=sys.stderr)
                    return 1
                accounts.grant(args.team_id, args.credits, args.reason)
                print(f"{args.team_id}: {accounts.balance(args.team_id)} credits")
    elif args.cmd == "compare-scorers":
        from horizon.db import connected
        from horizon.decision.compare import compare
        from horizon.decision.log import DecisionLog
        from horizon.decision.scorers import make_scorer

        settings = Settings.from_env()
        scorers = {"jev": make_scorer("jev", settings.jev_model, settings.llm_scorer_model),
                   "llm": make_scorer("llm", settings.jev_model, settings.llm_scorer_model)}
        with connected(settings.db_url) as db:
            print(json.dumps(compare(DecisionLog(db), settings.team_id, scorers, settings, args.limit), indent=2))
    elif args.cmd == "purge-memory":
        if not args.yes:
            print("This erases the episode for good and can't be undone. Re-run with --yes.", file=sys.stderr)
            return 1
        from horizon.server import build_memrouter

        settings = Settings.from_env()
        print(json.dumps(build_memrouter(settings).purge(settings.team_id, args.episode_id, args.reason, args.by),
                         indent=2))
    elif args.cmd in ("consolidate", "clear-fear"):
        from horizon.server import build_memrouter

        settings = Settings.from_env()
        router = build_memrouter(settings)
        if args.cmd == "consolidate":
            report = router.consolidate(settings.team_id)
            print(json.dumps(report, indent=2, default=str))
        else:
            les = router.clear_fear(settings.team_id, args.lesson_id, args.by)
            print(f"Cleared {les.id} ({les.statement}) by {les.cleared_by}")
    elif args.cmd == "export-decisions":
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
    elif args.cmd == "install-codex":
        from horizon.install import install_codex

        changed = install_codex(Path(args.dir), agents_md=not args.no_agents_md, hosted=args.hosted)
        print("Updated: " + ", ".join(changed))
        print("Then in Codex: trust the project and review its hooks with /hooks (Codex asks once).")
    elif args.cmd == "install-claude-code":
        from horizon.install import install

        changed = install(Path(args.dir), claude_md=not args.no_claude_md, hosted=args.hosted)
        print("Updated: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
