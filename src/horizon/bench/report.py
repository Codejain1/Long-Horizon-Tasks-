"""Summarise one or more runs: success, tokens, cost, steps and time per task, and the
variance of the success rate across repeats (PROJECT.md §18)."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from horizon.bench.runner import read_results


def _mean(xs: list[float]) -> float | None:
    return round(statistics.fmean(xs), 4) if xs else None


def summarise_run(results: list[dict]) -> dict:
    evaluated = [r for r in results if r.get("resolved") is not None]
    return {
        "n_tasks": len(results),
        "evaluated": len(evaluated),
        "resolved": sum(bool(r["resolved"]) for r in evaluated),
        "success_rate": round(sum(bool(r["resolved"]) for r in evaluated) / len(evaluated), 4) if evaluated else None,
        "submitted_rate": round(sum(r["submitted"] for r in results) / len(results), 4) if results else None,
        "mean_tokens": _mean([r["tokens"]["total"] for r in results]),
        "mean_cost_usd": _mean([r["cost_usd"] for r in results]),
        "total_cost_usd": round(sum(r["cost_usd"] for r in results), 4),
        "mean_steps": _mean([r["steps"] for r in results]),
        "mean_wall_time_s": _mean([r["wall_time_s"] for r in results]),
        "exit_statuses": dict(Counter(r["exit_status"] for r in results)),
        "errors": sum(r.get("error") is not None for r in results),
    }


def build_report(run_dirs: list[Path]) -> dict:
    runs, per_task = {}, defaultdict(list)
    dry = set()
    for d in run_dirs:
        meta = json.loads((d / "meta.json").read_text())
        dry.add(meta["dry_run"])
        results = read_results(d)
        runs[meta["run_id"]] = {"stage": meta["stage"], "repeat": meta["repeat"], "dry_run": meta["dry_run"],
                                "environment": meta["environment"], **summarise_run(results)}
        for r in results:
            per_task[r["instance_id"]].append(r)
    rates = [r["success_rate"] for r in runs.values() if r["success_rate"] is not None]
    tasks = {
        iid: {"repo": rs[0]["repo"], "difficulty": rs[0]["difficulty"], "runs": len(rs),
              "resolved": sum(bool(r.get("resolved")) for r in rs),
              "mean_tokens": _mean([r["tokens"]["total"] for r in rs]), "mean_cost_usd": _mean([r["cost_usd"] for r in rs]),
              "mean_steps": _mean([r["steps"] for r in rs]), "mean_wall_time_s": _mean([r["wall_time_s"] for r in rs])}
        for iid, rs in per_task.items()
    }
    return {
        "dry_run": dry.pop() if len(dry) == 1 else "mixed",
        "runs": runs,
        "across_repeats": {
            "n_runs": len(runs),
            "success_rate_mean": _mean(rates),
            "success_rate_stdev": round(statistics.stdev(rates), 4) if len(rates) > 1 else None,
            "success_rate_min": min(rates) if rates else None,
            "success_rate_max": max(rates) if rates else None,
        },
        "tasks": tasks,
    }


def to_markdown(report: dict) -> str:
    lines = []
    if report["dry_run"] is True:
        lines.append("> **DRY RUN** — mock model and mock evaluator. These numbers are not a baseline.\n")
    lines += ["| run | stage | env | tasks | success | submitted | mean tokens | mean cost $ | total cost $ "
              "| mean steps | mean time s | exits |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for run_id, r in report["runs"].items():
        exits = ", ".join(f"{k}: {v}" for k, v in sorted(r["exit_statuses"].items(), key=lambda kv: str(kv[0])))
        lines.append(f"| {run_id} | {r['stage']} | {r['environment']} | {r['n_tasks']} | {r['success_rate']} "
                     f"| {r['submitted_rate']} | {r['mean_tokens']} | {r['mean_cost_usd']} | {r['total_cost_usd']} "
                     f"| {r['mean_steps']} | {r['mean_wall_time_s']} | {exits} |")
    a = report["across_repeats"]
    lines.append(f"\nSuccess rate across {a['n_runs']} run(s): mean {a['success_rate_mean']}, "
                 f"stdev {a['success_rate_stdev']}, range {a['success_rate_min']}–{a['success_rate_max']}.")
    return "\n".join(lines)


def write_report(run_dirs: list[Path], out: Path) -> dict:
    report = build_report(run_dirs)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2))
    (out / "report.md").write_text(to_markdown(report) + "\n")
    return report
