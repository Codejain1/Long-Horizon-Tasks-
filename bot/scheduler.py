"""Hermes's cron jobs, run by code. Three kinds (schedules are UTC cron, as in Hermes):
- script: run it; its output, if any, is the message (empty = silent).
- monitor: run the monitor script; silent while its output is unchanged. On a change (an ERROR line is a change),
  one model call with the job's prompt, the diff and the new output.
- model: code gathers the inputs (`gather`), one model call writes the message.
State (last fire, last monitor output, recent outputs) lives in one JSON file.
"""

from __future__ import annotations

import difflib
import json
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path


def _field(spec: str, lo: int, hi: int) -> set[int]:
    out = set()
    for part in spec.split(","):
        rng, _, step = part.partition("/")
        a, b = (lo, hi) if rng == "*" else (int(rng.split("-")[0]), int(rng.split("-")[-1]))
        out.update(range(a, b + 1, int(step or 1)))
    return out


def due(expr: str, t: datetime) -> bool:
    """5-field cron (minute hour day-of-month month day-of-week; *, a-b, a,b, */n), Sunday = 0."""
    m, h, dom, mon, dow = expr.split()
    return (t.minute in _field(m, 0, 59) and t.hour in _field(h, 0, 23) and t.day in _field(dom, 1, 31)
            and t.month in _field(mon, 1, 12) and (t.isoweekday() % 7) in _field(dow, 0, 6))


class Scheduler:
    def __init__(self, jobs: list[dict], state_path: Path, send, think, gather=None, scripts: Path = Path(".")):
        """send(text, deliver), think(job, context) -> text, gather(job) -> context for model jobs."""
        self.jobs, self.path, self.send, self.think, self.gather, self.scripts = jobs, Path(state_path), send, think, gather, scripts
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.lock = threading.Lock()

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        tmp.replace(self.path)

    def tick(self, now: datetime | None = None, wait: bool = False) -> list[str]:
        """Start every job due this minute (once per minute). Returns the names started."""
        now = (now or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
        stamp, started = now.isoformat(), []
        for job in self.jobs:
            if not job.get("enabled", True) or not due(job["schedule"], now):
                continue
            with self.lock:
                st = self.state.setdefault(job["name"], {})
                if st.get("fired") == stamp:
                    continue
                st["fired"] = stamp
                self._save()
            started.append(job["name"])
            th = threading.Thread(target=self.run, args=(job,), daemon=True)
            th.start()
            if wait:
                th.join()
        return started

    def _script(self, name: str) -> str:
        p = subprocess.run([str(self.scripts / name)], capture_output=True, text=True, timeout=300)
        return (p.stdout or "").strip() or (f"ERROR {name} exited {p.returncode}: {p.stderr.strip()[:300]}"
                                            if p.returncode else "")

    def run(self, job: dict) -> str:
        """Run one job now; returns what was sent ('' = nothing). Failures are sent, never swallowed."""
        try:
            text = ""
            if job["kind"] == "script":
                text = self._script(job["script"])
            elif job["kind"] == "monitor":
                out = self._script(job["script"])
                with self.lock:
                    st = self.state.setdefault(job["name"], {})
                    before, st["last_output"] = st.get("last_output", job.get("seed")), out
                    self._save()
                if before is not None and out != before:
                    diff = "\n".join(difflib.unified_diff(before.splitlines(), out.splitlines(), "before", "now", lineterm=""))
                    text = self.think(job, f"## MONITOR CHANGE DETECTED\n\n```diff\n{diff}\n```\n\nNow:\n{out}")
            else:
                text = self.think(job, self.gather(job) if self.gather else "")
        except Exception as e:  # a job that breaks says so: silence must mean "ran, nothing to say"
            text = f"⚠ job {job['name']} failed: {e!r}"[:500]
        text = (text or "").strip()
        if text:
            with self.lock:
                hist = self.state.setdefault(job["name"], {}).setdefault("sent", [])
                hist.append({"at": datetime.now(timezone.utc).isoformat(timespec="minutes"), "text": text[:1000]})
                del hist[:-10]
                self._save()
            self.send(text, job["deliver"])
        return text
