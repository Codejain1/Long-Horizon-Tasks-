"""``horizon-bench``: select | run | evaluate | report. Dry run unless bench/config.yaml says real_runs: true."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from horizon.bench.config import REPO_ROOT, load_config


def cmd_select(cfg, args) -> None:
    s = cfg.selection
    if args.source == "hf":
        from datasets import load_dataset  # needs huggingface.co

        rows = load_dataset(cfg.dataset.name, split=cfg.dataset.split)
        instances = [{"instance_id": r["instance_id"], "repo": r["repo"], "difficulty": r["difficulty"]} for r in rows]
        dataset = f"{cfg.dataset.name}:{cfg.dataset.split}"
    else:
        from horizon.bench.select import synthetic_verified_like

        instances, dataset = synthetic_verified_like(), "synthetic (dry run only)"
    from horizon.bench.select import select_tasks

    tasks = select_tasks(instances, seed=s.seed, n_tasks=s.n_tasks, smoke_n=s.smoke_n, take_all=s.take_all,
                         weights=s.weights)
    out = Path(args.out) if args.out else cfg.path(s.task_list)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "dataset": dataset, "seed": s.seed, "n_tasks": s.n_tasks, "smoke_n": s.smoke_n, "take_all": s.take_all,
        "weights": s.weights, "created_at": datetime.now(UTC).isoformat(), "tasks": tasks,
    }, indent=2) + "\n")
    print(f"Wrote {len(tasks)} tasks to {out}")


def cmd_run(cfg, args) -> None:
    from horizon.bench.evaluate import evaluate
    from horizon.bench.report import to_markdown, write_report
    from horizon.bench.runner import run

    repeats = args.repeats if args.repeats is not None else (cfg.execution.repeats if args.stage == "full" else 1)
    mode = "DRY RUN (mock model, no paid calls)" if cfg.dry_run else "REAL RUN (paid API calls)"
    n = cfg.selection.smoke_n if args.stage == "smoke" else cfg.selection.n_tasks
    if args.limit:
        n = min(n, args.limit)
    print(f"{mode}: stage={args.stage}, tasks={n}, repeats={repeats}, caps={cfg.limits.step_limit} steps / "
          f"${cfg.limits.cost_limit_usd} per task, worst-case spend ${n * repeats * cfg.limits.cost_limit_usd:.2f}")
    out_root = Path(args.out) if args.out else None
    run_dirs = run(cfg, args.stage, repeats=repeats, out_root=out_root, limit=args.limit)
    if not args.no_eval:
        for d in run_dirs:
            evaluate(cfg, d)
    report_dir = run_dirs[0].parent / f"report-{run_dirs[0].name}"
    report = write_report(run_dirs, report_dir)
    print(to_markdown(report))
    print(f"\nRuns: {', '.join(str(d) for d in run_dirs)}\nReport: {report_dir}")


def cmd_evaluate(cfg, args) -> None:
    from horizon.bench.evaluate import evaluate

    for d in args.run_dirs:
        s = evaluate(cfg, Path(d))
        print(f"{s['run_id']}: {len(s['resolved'])}/{s['n']} resolved ({s['evaluator']})")


def cmd_report(cfg, args) -> None:
    from horizon.bench.report import to_markdown, write_report

    dirs = [Path(d) for d in args.run_dirs]
    report = write_report(dirs, Path(args.out) if args.out else dirs[0].parent / f"report-{dirs[0].name}")
    print(to_markdown(report))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="horizon-bench", description=__doc__)
    p.add_argument("--config", default=None, help="Path to config (default: bench/config.yaml).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("select", help="Choose and write the fixed task list.")
    s.add_argument("--source", choices=["hf", "synthetic"], default="hf")
    s.add_argument("--out", default=None)

    r = sub.add_parser("run", help="Run a stage (smoke = first smoke_n tasks, full = all).")
    r.add_argument("--stage", choices=["smoke", "full"], default="smoke")
    r.add_argument("--repeats", type=int, default=None, help="Default: 1 for smoke, execution.repeats for full.")
    r.add_argument("--limit", type=int, default=None, help="Only the first N tasks of the stage.")
    r.add_argument("--out", default=None, help=f"Output root (default: output_dir, under {REPO_ROOT.name}).")
    r.add_argument("--no-eval", action="store_true")

    e = sub.add_parser("evaluate", help="Evaluate run directories.")
    e.add_argument("run_dirs", nargs="+")

    rp = sub.add_parser("report", help="Summarise run directories.")
    rp.add_argument("run_dirs", nargs="+")
    rp.add_argument("--out", default=None)

    args = p.parse_args(argv)
    cfg = load_config(args.config)
    {"select": cmd_select, "run": cmd_run, "evaluate": cmd_evaluate, "report": cmd_report}[args.cmd](cfg, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
