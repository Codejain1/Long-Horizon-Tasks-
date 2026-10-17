"""Research tasks: the watcher's evidence check catches invented quotes and dead links; tasks score against truth."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent.parent / "runner"), str(Path(__file__).parent.parent / "bench")]
import longrun as LR  # noqa: E402
import web as W  # noqa: E402


def site():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/py":
                body = b"<html><h1>Python 3.14.0</h1><p>Python&nbsp;3.14.0 is the newest   major release.</p></html>"
                self.send_response(200)
            else:
                body, _ = b"not found", self.send_response(404)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}"


def check(tmp_path, claims):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "answer.json").write_text(json.dumps({"answer": {"python": "3.14.0"}, "claims": claims}))
    return LR.Runner("g", LR.LocalSandbox(ws), None, tmp_path / "r.jsonl").check_evidence("answer.json")


def test_evidence_check_passes_real_quotes_and_rejects_invented_ones(tmp_path):
    url = site()
    ok, why = check(tmp_path, [{"claim": "newest Python", "url": f"{url}/py", "quote": "Python 3.14.0 is the NEWEST major"}])
    assert ok, why  # tags, entities, spacing and case don't matter
    ok, why = check(tmp_path, [{"claim": "newest Python", "url": f"{url}/py", "quote": "Python 3.15.0 is the newest"}])
    assert not ok and "the quote is not on" in why
    ok, why = check(tmp_path, [{"claim": "x", "url": f"{url}/gone", "quote": "anything at all here"}])
    assert not ok and ("could not be fetched" in why or "quote is not on" in why)
    ok, why = check(tmp_path, [{"claim": "x", "url": "python.org", "quote": "py"}])
    assert not ok and "needs an http(s) url" in why
    assert not check(tmp_path, [])[0]


def test_research_jobs_plan_with_sources_and_evidence(tmp_path):
    from test_longrun import FakeModel
    m = FakeModel([[{"id": "s1", "kind": "build", "do": "find", "check": {"evidence": "answer.json"}}]], [])
    rn = LR.Runner("find python version", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl", kind="research")
    rn._start()
    rn.plan()
    assert "RESEARCH task" in m.plan_prompts[0] and '{"answer":' in m.plan_prompts[0] and not rn.brownfield


def test_tasks_score_against_independent_truth():
    truth = {"python": "3.14.0", "nodejs": "25.1.0", "postgresql": "18.0"}
    fetch = lambda url, data=None: [{"latest": truth[url.rsplit("/", 1)[1][:-5]]}]  # noqa: E731
    assert W.score_versions({"answer": {"python": "3.14.0", "nodejs": "v25.1.0", "postgresql": "17.6"}}, fetch)[:2] == (2, 3)
    osm = lambda url, data=None: {"elements": [{"tags": {"name": "Sattvam"}}, {"tags": {"name": "Tokyo Table"}}]}  # noqa: E731
    places = {"answer": {"places": [{"name": "sattvam"}, {"name": "Made Up Cafe"}, {"name": "Tokyo Table"}]}}
    assert W.score_places(places, osm)[:2] == (2, 3)
    links = {"answer": {"links": [
        {"site": "Google Flights", "url": "https://www.google.com/travel/flights?q=Flights%20from%20DEL%20to%20BLR%20on%202026-12-15"},
        {"site": "IndiGo", "url": "https://www.goindigo.in/booking?from=DEL&to=BLR&date=15/12/2026"}]}}
    assert W.score_flights(links, check=lambda u: 200)[:2] == (4, 4)
    assert W.score_flights({"answer": {"links": links["answer"]["links"][:1]}}, check=lambda u: 404)[0] == 2


def test_research_job_cannot_finish_on_invented_facts(tmp_path):
    """Live run 2026-10-18: the planner gave every step a `true` check and the model wrote versions from memory;
    a judge accepted them from the doer's own summary. Now trivial checks become evidence checks and the job only
    finishes once every claim is on its page."""
    from test_longrun import FakeModel, call
    url = site()
    invented = json.dumps({"answer": {"python": "3.11.6"},
                           "claims": [{"claim": "py", "url": f"{url}/py", "quote": "Python 3.11.6 is the newest"}]})
    real = json.dumps({"answer": {"python": "3.14.0"},
                       "claims": [{"claim": "py", "url": f"{url}/py", "quote": "Python 3.14.0 is the newest major"}]})
    plan = [{"id": "s1", "kind": "build", "do": "find the version", "check": {"cmd": "true"}}]
    m = FakeModel([plan] * 3, [[call("write_file", path="answer.json", content=invented)], [call("done", summary="3.11.6")],
                               [call("done", summary="escalated, same")], [call("done", summary="again")],
                               [call("done", summary="again")], [call("done", summary="again")],
                               [call("done", summary="again")], [call("done", summary="again")]] * 3)
    rn = LR.Runner("newest python", LR.LocalSandbox(tmp_path / "ws"), m, tmp_path / "r.jsonl", kind="research")
    r = rn.run()
    assert not r["ok"] and rn.steps[0].check == {"evidence": "answer.json"}

    m2 = FakeModel([plan], [[call("write_file", path="answer.json", content=invented)], [call("done", summary="v")],
                            [call("write_file", path="answer.json", content=real)], [call("done", summary="fixed")]])
    rn2 = LR.Runner("newest python", LR.LocalSandbox(tmp_path / "ws2"), m2, tmp_path / "r2.jsonl", kind="research")
    assert rn2.run()["ok"] and "3.14.0" in (tmp_path / "ws2" / "answer.json").read_text()


def test_every_arm_is_told_the_answer_format(tmp_path, monkeypatch):
    from types import SimpleNamespace
    cmds = []

    class P:
        def __init__(self, cmd, **kw):
            cmds.append(cmd)

        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(W.subprocess, "Popen", P)
    a = SimpleNamespace(out=str(tmp_path), model_url="x", max_minutes=1, local=True)
    W.run_job(W.WebTask("t", "Find X.", lambda ans: (0, 1, [])), "plain", 1, a)
    assert cmds[0][2].startswith("Find X. Use live web sources") and '"claims"' in cmds[0][2]
