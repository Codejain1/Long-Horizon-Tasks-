"""Evaluate a run's patches: sb-cli (SWE-bench cloud evaluation) for real runs, a mock in dry run.

Writes ``resolved`` into ``results.jsonl`` and saves ``evaluation.json``.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from horizon.bench.config import BenchConfig
from horizon.bench.runner import read_results


def mock_resolved(instance_id: str, repeat: int, patch: str) -> bool:
    """Dry run only: a deterministic stand-in so reports have realistic shape and some variance."""
    if not patch.strip():
        return False
    return int(hashlib.sha256(f"{instance_id}:{repeat}".encode()).hexdigest(), 16) % 3 != 0


def sb_cli_resolved(cfg: BenchConfig, run_dir: Path, run_id: str) -> set[str]:
    """Submit preds.json with sb-cli, wait for the evaluation and return the resolved instance ids."""
    report_dir = run_dir / "sb-cli-reports"
    subprocess.run(
        ["sb-cli", "submit", cfg.evaluation.subset, cfg.evaluation.split,
         "--predictions_path", str(run_dir / "preds.json"), "--run_id", run_id, "--output_dir", str(report_dir)],
        check=True, timeout=6 * 3600,
    )
    reports = sorted(report_dir.glob("*.json"))
    if not reports:
        raise RuntimeError(f"sb-cli wrote no report into {report_dir}")
    return resolved_ids_from_report(json.loads(reports[-1].read_text()))


def resolved_ids_from_report(report: dict) -> set[str]:
    """sb-cli reports list resolved ids under ``resolved_ids`` (older: ``resolved``)."""
    ids = report.get("resolved_ids", report.get("resolved", []))
    return set(ids) if isinstance(ids, list) else set()


def evaluate(cfg: BenchConfig, run_dir: Path) -> dict:
    meta = json.loads((run_dir / "meta.json").read_text())
    if meta["dry_run"] != cfg.dry_run:
        raise SystemExit(f"{run_dir.name} was a {'dry' if meta['dry_run'] else 'real'} run; config says otherwise.")
    results = read_results(run_dir)
    preds = json.loads((run_dir / "preds.json").read_text())
    if cfg.dry_run:
        resolved = {r["instance_id"] for r in results
                    if mock_resolved(r["instance_id"], r["repeat"], preds[r["instance_id"]]["model_patch"])}
        evaluator = "mock (dry run)"
    else:
        cfg.check_real_run_ready()
        resolved = sb_cli_resolved(cfg, run_dir, meta["run_id"])
        evaluator = f"sb-cli {cfg.evaluation.subset}/{cfg.evaluation.split}"
    for r in results:
        r["resolved"] = r["instance_id"] in resolved
    (run_dir / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results))
    summary = {"run_id": meta["run_id"], "evaluator": evaluator, "n": len(results), "resolved": sorted(resolved)}
    (run_dir / "evaluation.json").write_text(json.dumps(summary, indent=2))
    return summary
