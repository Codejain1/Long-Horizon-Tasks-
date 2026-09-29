"""The "sleep" job (MEMROUTER.md §7): replay, extract, merge, archive, prune, refresh, export.

Runs every `consolidation_every` episodes (inline, failure-isolated) or from `horizon consolidate` (nightly
cron). Lessons and strategies keep links back to their evidence; episodes are archived, never deleted.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from horizon.memrouter.spikes import same_option
from horizon.models import Condition, Lesson, Scope, TrackRecord, new_id, now

REPLAY_WINDOW = 1000  # most recent episodes replayed per run
CLUSTER_SIMILARITY = 0.8  # episodes this similar (same option) describe the same decision
MERGE_SIMILARITY = 0.85  # an existing lesson this similar (same option and kind) absorbs the new evidence
STRATEGY_RATE = 0.8  # success rate at or above which a cluster becomes a strategy ("what works")
LESSON_RATE = 0.3  # at or below which it becomes a failure lesson ("what is true")


def _common_conditions(episodes) -> list[Condition]:
    first = {(c.key, c.op, json.dumps(c.value)) for c in episodes[0].conditions}
    for e in episodes[1:]:
        first &= {(c.key, c.op, json.dumps(c.value)) for c in e.conditions}
    return [Condition(key=k, op=op, value=json.loads(v)) for k, op, v in sorted(first)]


def _clusters(router, episodes) -> list[list]:
    """Greedy clustering: same option and embedding similarity to the cluster's first episode."""
    vectors = router.store.vectors([e.id for e in episodes])
    clusters: list[list] = []
    for e in sorted(episodes, key=lambda e: e.provenance.created_at):
        v = vectors.get(e.id)
        if v is None:
            continue
        for c in clusters:
            head = c[0]
            if same_option(head.chosen.label, e.chosen.label) and \
                    float(np.dot(vectors[head.id], v)) >= CLUSTER_SIMILARITY:
                c.append(e)
                break
        else:
            clusters.append([e])
    return clusters


def consolidate(router, team_id: str) -> dict:
    graph, store, s = router.graph, router.store, router.settings
    report = {"created": [], "merged": [], "archived": 0, "links_pruned": 0, "export": None}
    episodes = [e for e in store.recent(team_id, REPLAY_WINDOW) if e.severity != "severe"]  # fear handled at once

    for cluster in _clusters(router, episodes):
        if len(cluster) < s.lesson_min_evidence:
            continue
        rate = sum(e.actual.success for e in cluster) / len(cluster)
        kind = "strategy" if rate >= STRATEGY_RATE else "lesson" if rate <= LESSON_RATE else None
        if kind is None:
            continue  # mixed results: conditions probably differ; left to reconsolidation
        head, ids = cluster[0], [e.id for e in cluster]
        wins = sum(e.actual.success >= 0.5 for e in cluster)
        vector = router._embed(head.situation)
        existing = next((les for les, sim in graph.search_lessons(team_id, vector, router.embedder.model_name, 10)
                         if sim >= MERGE_SIMILARITY and les.kind == kind and not les.is_fear
                         and same_option(les.option, head.chosen.label)), None)
        if existing is not None:  # merge and strengthen
            new = [i for i in ids if i not in existing.evidence]
            if new:
                existing.evidence += new
                news = [e for e in cluster if e.id in new]
                existing.track_record.successes += sum(e.actual.success >= 0.5 for e in news)
                existing.track_record.failures += sum(e.actual.success < 0.5 for e in news)
                graph.save_lesson(existing)
                st = graph.states([existing.id])[existing.id]
                st.strength = min(1.0, st.strength + 0.1)
                st.stability = min(20.0, st.stability * 1.2)
                graph.set_state(existing.id, team_id, st, reason=f"sleep job merged {len(new)} new episodes")
                report["merged"].append(existing.id)
            lesson = existing
        else:
            verb = "worked" if kind == "strategy" else "failed"
            lesson = Lesson(scope=Scope(team_id=team_id, project_id=head.scope.project_id), kind=kind,
                            statement=f"{head.chosen.label} {verb} for \"{head.situation}\" ({wins}/{len(cluster)})",
                            situation=head.situation, option=head.chosen.label,
                            conditions=_common_conditions(cluster), evidence=ids,
                            track_record=TrackRecord(successes=wins, failures=len(cluster) - wins,
                                                     human_weighted=sum((2 if e.actual.signal_type == "human" else 1)
                                                                        * (1 if e.actual.success >= 0.5 else -1)
                                                                        for e in cluster)))
            graph.add_lesson(lesson, router.embedder.model_name, vector, strength=0.6, stability=2.0)
            report["created"].append(lesson.id)
        for i in ids:
            if graph.link_weight(i, lesson.id, "derived-from") is None:
                graph.set_link(team_id, i, lesson.id, "derived-from", 0.5)
        # Archive the evidence the lesson now stands for, keeping the newest few in retrieval.
        newest_first = sorted(cluster, key=lambda e: e.provenance.created_at, reverse=True)
        for e in newest_first[s.archive_keep:]:
            graph.archive_episode(e.id)
            report["archived"] += 1

    report["links_pruned"] = graph.prune_links(team_id, s.link_prune_threshold)
    report["predictor_stats"] = graph.predictor_stats(team_id)  # refreshed calibration (§7)
    report["export"] = export_parquet(router, team_id)
    seen = store.count(team_id) + graph.archived_count(team_id)
    graph.log_consolidation(new_id("sleep"), team_id, seen, {k: v for k, v in report.items()
                                                             if k != "predictor_stats"})
    return report


def export_parquet(router, team_id: str) -> str | None:
    """World-model training data (MEMROUTER §4, §14: with each consolidation run): every episode, archived
    ones included. Needs the `[export]` extra (pyarrow); skipped without it."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return None
    rows = []
    for table in ("episodes", "episodes_archive"):
        for (data,) in router.store.db.fetchall(f"SELECT data FROM {table} WHERE team_id = %s", (team_id,)):
            e = json.loads(data) if isinstance(data, (str, bytes)) else data
            rows.append({"id": e["id"], "archived": table == "episodes_archive",
                         "project_id": e["scope"].get("project_id"), "task_id": e["scope"].get("task_id"),
                         "situation": e["situation"], "chosen": e["chosen"]["label"],
                         "predicted_success": e["predicted"].get("success"), "predicted_source": e["predicted"]["source"],
                         "actual_success": e["actual"]["success"], "surprise": e["surprise"],
                         # The world model's efficiency targets (PROJECT.md §6), flat for training.
                         **{f"{side}_{k}": e[side].get(k) for side in ("predicted", "actual")
                            for k in ("tokens", "cost_usd", "latency_ms")},
                         "low_confidence": e["low_confidence"], "severity": e["severity"],
                         "created_at": e["provenance"]["created_at"], "episode_json": json.dumps(e)})
    out = Path(router.settings.export_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"episodes-{team_id}-{now():%Y%m%dT%H%M%S}.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    return str(path)
