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

    p = sub.add_parser("hookd", help="The warm hook daemon hookc.py starts (internal).")
    p.add_argument("--socket", required=True)

    p = sub.add_parser("hook", help="Run a Claude Code hook (reads the hook JSON on stdin).")
    p.add_argument("name", choices=["session-start", "pre-tool-use", "post-tool-use", "stop", "user-prompt",
                                    "pre-compact", "session-end", "sessionStart", "beforeSubmitPrompt", "preToolUse",
                                    "postToolUse", "postToolUseFailure", "preCompact", "sessionEnd"])
    p.add_argument("--remote", help="Hosted server URL: send parsed facts there (key in HORIZON_API_KEY).")
    p.add_argument("--profile", choices=["full", "lean", "observe"], default="full")
    p.add_argument("--host", choices=["claude-code", "cursor"], default="claude-code",
                   help="cursor: the name is a Cursor hook event (lean and observe, local only).")

    sub.add_parser("stats", help="Show invocation reliability stats.")

    p = sub.add_parser("create-team", help="Create a team for the hosted server and print its first API key.")
    p.add_argument("name")
    p.add_argument("--credits", type=int, default=None, help="Starting credits (default: the free starter grant).")

    p = sub.add_parser("add-credits", help="Grant credits to a team (operator; no payments yet).")
    p.add_argument("team_id")
    p.add_argument("credits", type=int)
    p.add_argument("--reason", default="operator top-up")

    sub.add_parser("consolidate", help="Run the memrouter sleep job now (e.g. nightly from cron).")

    p = sub.add_parser("episodes-from-log", help="Write an episode for every ended session in the event log that "
                                                 "has none yet (lean writes them at each session end).")
    p.add_argument("--project", help="Only this project directory (default: every project).")
    p.add_argument("--rejudge", action="store_true", help="Reconsider segments skipped for having no ground truth "
                                                          "(e.g. after configuring HORIZON_SCORER=jev).")

    p = sub.add_parser("eval-forecast", help="Replay past sessions through world model v2: each forecast from "
                                             "strictly earlier sessions, against memory's current baseline.")
    p.add_argument("--jev", action="store_true", help="Use the configured scorer's prior (one call per session).")
    p.add_argument("--limit", type=int, default=500, help="The most recent sessions to replay.")

    p = sub.add_parser("clear-fear", help="Clear a fear lesson. Human only: it records who cleared it.")
    p.add_argument("lesson_id")
    p.add_argument("--by", required=True, help="Your name, recorded on the lesson.")

    p = sub.add_parser("compare-scorers", help="Replay logged decisions through Jev and the small LLM (PROJECT.md §5).")
    p.add_argument("--limit", type=int, default=50, help="Most recent decisions to replay.")

    sub.add_parser("eval-world-model", help="Replay past outcomes through the world model and compare it with "
                                            "the stand-ins (PROJECT.md §6).")

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

    p = sub.add_parser("install-cursor", help="Add Horizon's hooks to a project for Cursor (lean or observe).")
    p.add_argument("--dir", default=".", help="Project directory (default: current).")
    p.add_argument("--profile", choices=["lean", "observe"], default="lean")
    p.add_argument("--no-mcp", action="store_true", help="Hooks only: don't add the horizon MCP server.")
    p.add_argument("--user", action="store_true", help="Install in ~/.cursor, for every project opened in Cursor.")
    p.add_argument("--scorer", choices=["jev"], help="Pick out rules with Jev (key: TYPESAFE_API_KEY or the "
                                                     "macOS Keychain item horizon-typesafe).")

    p = sub.add_parser("install-claude-code", help="Add the MCP server, hooks and CLAUDE.md snippet to a project.")
    p.add_argument("--dir", default=".", help="Project directory (default: current).")
    p.add_argument("--no-claude-md", action="store_true", help="Don't touch CLAUDE.md.")
    p.add_argument("--hosted", metavar="URL", help="Use a hosted Horizon server (key in HORIZON_API_KEY).")
    p.add_argument("--profile", choices=["full", "lean", "observe"], default="lean",
                   help="lean (default): continuity through hooks (goals, constraints, team rules, recurring "
                        "pitfalls), no agent calls. full: the whole workflow (decision scoring, recall per step, "
                        "outcomes per test run, rollback). observe: hooks only record the event log, and the agent "
                        "gets nothing (evaluation baselines; local only).")

    args = parser.parse_args(argv)
    from horizon.config import Settings

    if args.cmd == "serve":
        from horizon.server import serve

        serve(args.transport, args.host, args.port)
    elif args.cmd == "hookd":
        from horizon.hookd import serve

        serve(args.socket)
    elif args.cmd == "hook":
        if args.host == "cursor":
            from horizon.cursor import run_cursor_hook

            out = run_cursor_hook(args.name, sys.stdin.read(), profile=args.profile)
        else:
            from horizon.hooks import run_hook

            out = run_hook(args.name, sys.stdin.read(), remote=args.remote, profile=args.profile)
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
    elif args.cmd == "eval-world-model":
        from horizon.decision.worldmodel import evaluate, make_world_model
        from horizon.server import build_memrouter

        settings = Settings.from_env()
        report = evaluate(make_world_model(settings, build_memrouter(settings)), settings.team_id, settings)
        print(json.dumps({"mode": settings.world_model, **report}, indent=2))
    elif args.cmd == "purge-memory":
        if not args.yes:
            print("This erases the episode for good and can't be undone. Re-run with --yes.", file=sys.stderr)
            return 1
        from horizon.server import build_memrouter

        settings = Settings.from_env()
        print(json.dumps(build_memrouter(settings).purge(settings.team_id, args.episode_id, args.reason, args.by),
                         indent=2))
    elif args.cmd == "episodes-from-log":
        import os

        from horizon.db import connect
        from horizon.eventlog.episodes import (
            SessionEpisodes,
            configured_scorer,
            local_memory,
        )
        from horizon.taskstate.store import TaskStore

        settings = Settings.from_env()
        db = connect(settings.db_url)
        tasks = TaskStore(db)
        project = os.path.realpath(args.project) if args.project else None
        log = SessionEpisodes(db, settings.session_idle_minutes)
        if args.rejudge:
            log.unskip(settings.team_id, project)
        from horizon.eventlog.forecast import configured_predictor

        report = log.write(settings.team_id, lambda: local_memory(settings, db), project,
                           task_for=lambda proj, s: tasks.session_task(settings.team_id, proj, s),
                           judge=configured_scorer(settings), predictor=configured_predictor(settings, log))
        print(json.dumps({"written": len(report["written"]), "skipped": report["skipped"],
                          "failed": report["failed"]}))
    elif args.cmd == "eval-forecast":
        from horizon.db import connect
        from horizon.eventlog.episodes import SessionEpisodes, configured_scorer
        from horizon.eventlog.forecast import SessionWorldModel, replay
        from horizon.memrouter.embedding import make_embedder

        settings = Settings.from_env()
        judge = configured_scorer(settings) if args.jev else None
        if args.jev and judge is None:
            print("--jev needs HORIZON_SCORER=jev (or llm).", file=sys.stderr)
            return 1
        model = SessionWorldModel(SessionEpisodes(connect(settings.db_url)),
                                  make_embedder(settings.embedder, settings.embedding_dim), settings,
                                  judge() if judge else None)
        print(json.dumps({"model": model.name, **replay(model, settings.team_id, args.limit)}, indent=2))
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
    elif args.cmd == "install-cursor":
        from horizon.install import install_cursor

        print("Updated: " + ", ".join(install_cursor(Path(args.dir), profile=args.profile, mcp=not args.no_mcp,
                                                     user=args.user, scorer=args.scorer)))
        print("Then reload Cursor (or restart the CLI) so it picks up the hooks and the MCP server.")
    elif args.cmd == "install-claude-code":
        from horizon.install import install

        try:
            changed = install(Path(args.dir), claude_md=not args.no_claude_md, hosted=args.hosted,
                              profile=args.profile)
        except ValueError as exc:
            raise SystemExit(f"horizon: {exc}") from None
        print("Updated: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
