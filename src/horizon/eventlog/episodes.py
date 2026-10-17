"""Session episodes from the event log (v2 Phase 1, track A; MEMROUTER.md v2 note).

Memory learns from each session's ground truth with no call from the agent. A session segment runs up to a
`session_end` event (a resumed session has one per invocation), or up to its last event once the session has
been idle for `session_idle_minutes` (a crash sends no SessionEnd), and becomes one memrouter episode:

- situation: the segment's first prompt (the session's first when a resumed segment has none), already redacted;
- outcome: the domain adapter's, e.g. coding's last test run's pass rate, with its counts, the turns' real token
  usage and the segment's duration;
- no prediction yet, so the write path's similar-episodes fallback sets the surprise baseline (Phase 2 replaces
  it with the world model's forecast).

Segments with no prompt, no ground truth (a coding session without a test run) or no adapter are skipped, and
the reason is kept. `session_episodes` marks every segment written or skipped, so none is done twice; a write
that fails leaves it pending, to be retried at the next session end or by `horizon episodes-from-log`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from horizon.adapters import CodingAdapter
from horizon.adapters.base import DomainAdapter
from horizon.db import Database, load_json
from horizon.eventlog.judge import evidence, judge_outcome
from horizon.eventlog.schema import Event
from horizon.eventlog.store import EventStore
from horizon.models import Actual, Condition, TestResults, now
from horizon.taskstate.store import ts

log = logging.getLogger(__name__)

ADAPTERS: dict[str, DomainAdapter] = {"coding": CodingAdapter()}
CHOSEN = "session"  # the episode's option: the whole session, not one decision inside it
TASK_TYPE = "session"  # predictor stats for session outcomes stay apart from decisions'

_SCHEMA = {
    "sqlite": """CREATE TABLE IF NOT EXISTS session_episodes (
        team_id TEXT NOT NULL, project TEXT NOT NULL, session_id TEXT NOT NULL, through_n INTEGER NOT NULL,
        episode_id TEXT, skipped TEXT, created_at TEXT NOT NULL,
        PRIMARY KEY (team_id, project, session_id, through_n))""",
    "postgres": """CREATE TABLE IF NOT EXISTS session_episodes (
        team_id TEXT NOT NULL, project TEXT NOT NULL, session_id TEXT NOT NULL, through_n BIGINT NOT NULL,
        episode_id TEXT, skipped TEXT, created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (team_id, project, session_id, through_n))""",
}


@dataclass
class Draft:
    """What one session segment tells memory: the arguments of `MemRouter.record`."""

    situation: str
    actual: Actual
    conditions: list[Condition] = field(default_factory=list)
    agent_id: str | None = None


@dataclass
class Segment:
    project: str
    session_id: str
    after_n: int  # the previous segment's end (0 for the first)
    through_n: int  # this segment's session_end, or its last event when it went idle


def draft_episode(events: list[Event], session_prompt: str | None = None,
                  judge: Callable[[], object] | None = None) -> tuple[Draft | None, str | None]:
    """A segment's episode, or why it has none. Pure without a judge: the same events always give the same draft.
    `judge` builds the scorer that judges segments with no test run (track B), only when one needs it."""
    if not events:
        return None, "no events"
    adapter = ADAPTERS.get(events[0].domain)
    if adapter is None:
        return None, f"no adapter for {events[0].domain}"
    situation = adapter.situation(events) or session_prompt
    if not situation:
        return None, "no prompt"
    outcome = adapter.outcome(events)
    if (outcome is None or outcome.success is None) and judge is not None and evidence(events) is not None:
        try:
            outcome, reason = judge_outcome(judge(), events)
        except Exception as exc:  # the judge is down or unconfigured: as if there were none
            log.warning("outcome judge failed: %s", exc)
            return None, "no ground truth (judge failed)"
        if outcome is None:
            return None, reason
    if outcome is None or outcome.success is None:
        return None, "no ground truth"
    tests = outcome.facts.get("tests")
    tokens = outcome.metrics.get("tokens") or None
    latency = (events[-1].at - events[0].at).total_seconds() * 1000
    actual = Actual(success=outcome.success, tokens=tokens, latency_ms=latency if latency > 0 else None,
                    test_results=TestResults(source="hook", **tests) if tests else None, signal_type=outcome.signal)
    conditions = [Condition(key="task_type", value=TASK_TYPE), Condition(key="host", value=events[0].source)]
    return Draft(situation=situation, actual=actual, conditions=conditions, agent_id=events[0].source), None


def configured_scorer(settings) -> Callable[[], object] | None:
    """The owner's scorer (`HORIZON_SCORER`: jev or llm) as a factory, or None when there's none."""
    if settings.scorer not in ("jev", "llm"):
        return None
    from horizon.decision.scorers import make_scorer

    return lambda: make_scorer(settings.scorer, settings.jev_model, settings.llm_scorer_model,
                                           settings.own_judge_url, settings.own_judge_model)


def local_memory(settings, db: Database):
    """The memrouter on the hooks' own connection, built like the MCP server's: the configured scorer is also
    recall's attention filter (is this memory relevant?)."""
    from horizon.memrouter.embedding import make_embedder
    from horizon.memrouter.router import MemRouter
    from horizon.memrouter.store import EpisodeStore

    return MemRouter(EpisodeStore(db, settings.embedding_dim), make_embedder(settings.embedder, settings.embedding_dim),
                     settings, attention=configured_scorer(settings))


class SessionEpisodes:
    def __init__(self, db: Database, idle_minutes: int = 120):
        self.db = db
        self.idle = timedelta(minutes=idle_minutes)
        self.events = EventStore(db)  # creates the events table if no hook has run yet
        db.execute(_SCHEMA[db.kind])

    def pending(self, team_id: str, project: str | None = None, limit: int = 200,
                at: datetime | None = None) -> list[Segment]:
        """Finished segments with no episode and no recorded skip, oldest first. A segment finishes at a
        `session_end`, or when its session has been idle for `idle_minutes` (a crash or a killed terminal never
        sends one); if that session carries on later, the rest is a new segment."""
        where, params = "e.team_id = %s AND e.session_id IS NOT NULL", (team_id,)
        if project is not None:
            where, params = where + " AND e.project = %s", params + (project,)
        not_done = (" AND NOT EXISTS (SELECT 1 FROM session_episodes s WHERE s.team_id = e.team_id AND s.project ="
                    " e.project AND s.session_id = e.session_id AND s.through_n = {n})")
        ends = self.db.fetchall(f"SELECT e.project, e.session_id, e.n FROM events e WHERE {where}"
                                " AND e.kind = 'session_end'" + not_done.format(n="e.n"), params)
        cutoff = ts(self.db, (at or now()) - self.idle)
        # ponytail: groups every session of the team; bound it by date once logs hold months of sessions.
        idle = self.db.fetchall(
            f"SELECT e.project, e.session_id, MAX(e.n) FROM events e WHERE {where} GROUP BY e.project, e.session_id"
            " HAVING MAX(e.created_at) < %s", params + (cutoff,))
        finished = {(p, s, int(n)) for p, s, n in ends}
        for p, s, n in idle:
            last = self.db.fetchone("SELECT kind FROM events WHERE n = %s", (n,))
            done = self.db.fetchone("SELECT 1 FROM session_episodes WHERE team_id = %s AND project = %s AND"
                                    " session_id = %s AND through_n = %s", (team_id, p, s, n))
            if last and last[0] != "session_end" and not done:
                finished.add((p, s, int(n)))
        out = []
        for proj, session, n in sorted(finished, key=lambda f: f[2])[:limit]:
            prev = self.db.fetchone(
                "SELECT MAX(n) FROM events WHERE team_id = %s AND project = %s AND session_id = %s"
                " AND kind = 'session_end' AND n < %s", (team_id, proj, session, n))
            done = self.db.fetchone(
                "SELECT MAX(through_n) FROM session_episodes WHERE team_id = %s AND project = %s AND session_id = %s"
                " AND through_n < %s", (team_id, proj, session, n))
            after = max(int((prev or [0])[0] or 0), int((done or [0])[0] or 0))
            out.append(Segment(proj, session, after, n))
        return out

    def segment_events(self, team_id: str, seg: Segment) -> list[Event]:
        rows = self.db.fetchall(
            "SELECT data FROM events WHERE team_id = %s AND project = %s AND session_id = %s AND n > %s AND n <= %s"
            " ORDER BY n", (team_id, seg.project, seg.session_id, seg.after_n, seg.through_n))
        return [Event.model_validate(load_json(r[0])) for r in rows]

    def session_prompt(self, team_id: str, seg: Segment) -> str | None:
        row = self.db.fetchone(
            "SELECT data FROM events WHERE team_id = %s AND project = %s AND session_id = %s AND kind = 'prompt'"
            " AND n <= %s ORDER BY n LIMIT 1", (team_id, seg.project, seg.session_id, seg.through_n))
        return Event.model_validate(load_json(row[0])).text if row else None

    def mark(self, team_id: str, seg: Segment, episode_id: str | None = None, skipped: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO session_episodes (team_id, project, session_id, through_n, episode_id, skipped, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (team_id, seg.project, seg.session_id, seg.through_n, episode_id, skipped, ts(self.db, now())))

    def unskip(self, team_id: str, project: str | None = None) -> int:
        """Make segments skipped for having no ground truth pending again (for a newly configured judge). An idle
        segment that its session carried on after stays: only segments `pending` can find again are reopened."""
        sql = ("DELETE FROM session_episodes WHERE team_id = %s AND skipped LIKE %s AND (through_n IN (SELECT n FROM"
               " events WHERE kind = 'session_end') OR through_n = (SELECT MAX(e.n) FROM events e WHERE e.team_id ="
               " session_episodes.team_id AND e.project = session_episodes.project AND e.session_id ="
               " session_episodes.session_id))")
        params: tuple = (team_id, "no ground truth%")
        if project is not None:
            sql, params = sql + " AND project = %s", params + (project,)
        before = len(self.episodes(team_id, project))
        self.db.execute(sql, params)
        return before - len(self.episodes(team_id, project))

    def segment_of(self, team_id: str, episode_id: str) -> Segment | None:
        """The segment an episode was written from (every finished segment has a row, so the one before it
        is where this one starts)."""
        row = self.db.fetchone("SELECT project, session_id, through_n FROM session_episodes WHERE team_id = %s"
                               " AND episode_id = %s", (team_id, episode_id))
        if not row:
            return None
        proj, session, n = row
        prev = self.db.fetchone("SELECT MAX(through_n) FROM session_episodes WHERE team_id = %s AND project = %s"
                                " AND session_id = %s AND through_n < %s", (team_id, proj, session, n))
        return Segment(proj, session, int((prev or [0])[0] or 0), int(n))

    def has_episodes(self, team_id: str, project: str) -> bool:
        return self.db.fetchone("SELECT 1 FROM session_episodes WHERE team_id = %s AND project = %s AND"
                                " episode_id IS NOT NULL LIMIT 1", (team_id, project)) is not None

    def episodes(self, team_id: str, project: str | None = None) -> list[dict]:
        """What each ended segment became, oldest first (for inspection and the evals)."""
        sql = ("SELECT project, session_id, through_n, episode_id, skipped FROM session_episodes"
               " WHERE team_id = %s")
        params: tuple = (team_id,)
        if project is not None:
            sql, params = sql + " AND project = %s", params + (project,)
        return [{"project": p, "session_id": s, "through_n": int(n), "episode_id": e, "skipped": k}
                for p, s, n, e, k in self.db.fetchall(sql + " ORDER BY through_n", params)]

    @staticmethod
    def _predicted(cache: list, predictor, router, project: str, situation: str):
        """The forecast an episode is recorded against, or None (memory's fallback). Never stops the write."""
        if predictor is None:
            return None
        try:
            if not cache:
                cache.append(predictor(router))
            return cache[0](project, situation) if cache[0] else None
        except Exception as exc:
            log.warning("no forecast for the session episode: %s", exc)
            return None

    def write(self, team_id: str, memory: Callable[[], object], project: str | None = None,
              task_for: Callable[[str, str], str | None] = lambda project, session: None,
              at: datetime | None = None, judge: Callable[[], object] | None = None,
              predictor: Callable[[object], Callable | None] | None = None) -> dict:
        """Turn the pending segments into episodes. `memory` builds the memrouter, only when there's an episode
        to write (loading the embedder is the slow part); `task_for` names the task a session worked on;
        `judge` builds the outcome judge for segments with no test run. `predictor(router)` gives what an episode
        is predicted against (the session forecast, Phase 2) or None for memory's fallback; built once a batch."""
        report = {"written": [], "skipped": 0, "failed": 0}
        router, scorer, predict = None, [], []

        def once():  # one scorer for the whole batch
            scorer[:] = scorer or [judge()]
            return scorer[0]

        for seg in self.pending(team_id, project, at=at):
            draft, reason = draft_episode(self.segment_events(team_id, seg), self.session_prompt(team_id, seg),
                                          once if judge else None)
            if draft is None:
                self.mark(team_id, seg, skipped=reason)
                report["skipped"] += 1
                continue
            try:
                router = router or memory()
                predicted = self._predicted(predict, predictor, router, seg.project, draft.situation)
                result = router.record(team_id=team_id, situation=draft.situation, chosen=CHOSEN, predicted=predicted,
                                       actual=draft.actual, conditions=draft.conditions, project_id=seg.project,
                                       task_id=task_for(seg.project, seg.session_id), agent_id=draft.agent_id)
            except Exception as exc:  # memory is down: leave it pending for the next attempt
                log.warning("session episode for %s not written: %s", seg.session_id, exc)
                report["failed"] += 1
                continue
            self.mark(team_id, seg, episode_id=result.episode.id)
            report["written"].append(result.episode.id)
        return report
