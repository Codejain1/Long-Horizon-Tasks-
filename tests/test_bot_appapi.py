"""The app channel: key required, messages in (with location), replies and choices out by long-poll."""

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
import appapi  # noqa: E402


def call(app, path, body=None, key="k"):
    req = urllib.request.Request(f"http://127.0.0.1:{app.port}{path}", json.dumps(body).encode() if body else None,
                                 {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_key_messages_location_and_events():
    app = appapi.App("k", port=0)
    try:
        assert call(app, "/health", key=None) == (200, {"ok": True})
        assert call(app, "/messages", {"text": "hi"}, key="wrong")[0] == 401
        assert call(app, "/events?after=0&wait=0", key="wrong")[0] == 401
        assert call(app, "/messages", {"text": "dosa near me", "location": {"lat": 12.93, "lng": 77.62}})[0] == 200
        assert app.poll() == [{"channel": "app", "chat": "owner", "text": "dosa near me",
                               "location": {"lat": 12.93, "lng": 77.62}}]
        assert app.poll() == []
        threading.Timer(0.2, lambda: app.send_choice("owner", "Best options", ["1. Dosa ₹90"])).start()
        t0 = time.time()
        code, r = call(app, "/events?after=0&wait=5")  # waits for the reply, returns as soon as it comes
        assert code == 200 and 0.1 < time.time() - t0 < 2
        assert r["events"][0]["text"] == "Best options" and r["events"][0]["choices"] == ["1. Dosa ₹90"]
        app.send("owner", "Looking on Swiggy…")
        assert [e["text"] for e in call(app, f"/events?after={r['events'][0]['id']}&wait=0")[1]["events"]] == ["Looking on Swiggy…"]
    finally:
        app.close()


def test_token_file_is_created_private(tmp_path, monkeypatch):
    monkeypatch.delenv("BOT_APP_TOKEN", raising=False)
    t = appapi.load_token(tmp_path)
    assert len(t) > 30 and appapi.load_token(tmp_path) == t
    assert oct((tmp_path / "app.token").stat().st_mode)[-3:] == "600"
