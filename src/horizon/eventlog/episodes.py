"""Session episodes from the event log (v2 Phase 1, track A; MEMROUTER.md v2 note).

Memory learns from each session's ground truth with no call from the agent. A session segment runs up to a
`session_end` event (a resumed session has one per invocation), and becomes one memrouter episode:

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

from horizon.adapters import CodingAdapter
from horizon.adapters.base import DomainAdapter
from horizon.db import Database, load_json
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
    through_n: int  # this segment's session_end


def draft_episode(events: list[Event], session_prompt: str | None = None) -> tuple[Draft | None, str | None]:
    """A segment's episode, or why it has none. Pure: the same events always give the same draft."""
    if not events:
        return None, "no events"
    adapter = ADAPTERS.get(events[0].domain)
    if adapter is None:
        return None, f"no adapter for {events[0].domain}"
    situation = adapter.situation(events) or session_prompt
    if not situation:
        return None, "no prompt"
    outcome = adapter.outcome(events)
    if outcome is None or outcome.success is None:
        return None, "no ground truth"
    tests = outcome.facts.get("tests")
    tokens = outcome.metrics.get("tokens") or None
    latency = (events[-1].at - events[0].at).total_seconds() * 1000
    actual = Actual(success=outcome.success, tokens=tokens, latency_ms=latency if latency > 0 else None,
                    test_results=TestResults(source="hook", **tests) if tests else None, signal_type=outcome.signal)
    conditions = [Condition(key="task_type", value=TASK_TYPE), Condition(key="host", value=events[0].source)]
    return Draft(situation=situation, actual=actual, conditions=conditions, agent_id=events[0].source), None


def local_memory(settings, db: Database):
    """The memrouter on the hooks' own connection (the MCP server builds its own the same way)."""
    from horizon.memrouter.embedding import make_embedder
    from horizon.memrouter.router import MemRouter
    from horizon.memrouter.store import EpisodeStore

    return MemRouter(EpisodeStore(db, settings.embedding_dim), make_embedder(settings.embedder,
                                                                             settings.embedding_dim), settings)


class SessionEpisodes:
    def __init__(self, db: Database):
        self.db = db
        self.events = EventStore(db)  # creates the events table if no hook has run yet
        db.execute(_SCHEMA[db.kind])

    def pending(self, team_id: str, project: str | None = None, limit: int = 200) -> list[Segment]:
        """Ended segments with no episode and no recorded skip, oldest first."""
        sql = ("SELECT e.project, e.session_id, e.n FROM events e WHERE e.team_id = %s AND e.kind = 'session_end'"
               " AND e.session_id IS NOT NULL")
        params: tuple = (team_id,)
        if project is not None:
            sql, params = sql + " AND e.project = %s", params + (project,)
        sql += (" AND NOT EXISTS (SELECT 1 FROM session_episodes s WHERE s.team_id = e.team_id AND s.project ="
                " e.project AND s.session_id = e.session_id AND s.through_n = e.n) ORDER BY e.n LIMIT %s")
        out = []
        for proj, session, n in self.db.fetchall(sql, params + (limit,)):
            prev = self.db.fetchone(
                "SELECT MAX(n) FROM events WHERE team_id = %s AND project = %s AND session_id = %s"
                " AND kind = 'session_end' AND n < %s", (team_id, proj, session, n))
            out.append(Segment(proj, session, int(prev[0] or 0) if prev else 0, int(n)))
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

    def episodes(self, team_id: str, project: str | None = None) -> list[dict]:
        """What each ended segment became, oldest first (for inspection and the evals)."""
        sql = ("SELECT project, session_id, through_n, episode_id, skipped FROM session_episodes"
               " WHERE team_id = %s")
        params: tuple = (team_id,)
        if project is not None:
            sql, params = sql + " AND project = %s", params + (project,)
        return [{"project": p, "session_id": s, "through_n": int(n), "episode_id": e, "skipped": k}
                for p, s, n, e, k in self.db.fetchall(sql + " ORDER BY through_n", params)]

    def write(self, team_id: str, memory: Callable[[], object], project: str | None = None,
              task_for: Callable[[str, str], str | None] = lambda project, session: None) -> dict:
        """Turn the pending segments into episodes. `memory` builds the memrouter, only when there's an episode
        to write (loading the embedder is the slow part); `task_for` names the task a session worked on."""
        report = {"written": [], "skipped": 0, "failed": 0}
        router = None
        for seg in self.pending(team_id, project):
            draft, reason = draft_episode(self.segment_events(team_id, seg), self.session_prompt(team_id, seg))
            if draft is None:
                self.mark(team_id, seg, skipped=reason)
                report["skipped"] += 1
                continue
            try:
                router = router or memory()
                result = router.record(team_id=team_id, situation=draft.situation, chosen=CHOSEN,
                                       actual=draft.actual, conditions=draft.conditions, project_id=seg.project,
                                       task_id=task_for(seg.project, seg.session_id), agent_id=draft.agent_id)
            except Exception as exc:  # memory is down: leave it pending for the next attempt
                log.warning("session episode for %s not written: %s", seg.session_id, exc)
                report["failed"] += 1
                continue
            self.mark(team_id, seg, episode_id=result.episode.id)
            report["written"].append(result.episode.id)
        return report
