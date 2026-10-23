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


def test_cards_reach_the_app():
    app = appapi.App("k", port=0)
    try:
        app.send_cards("owner", "Best options", ["1. Veg Biryani ₹249"],
                       [{"choice": "1. Veg Biryani ₹249", "title": "Veg Biryani", "image": "https://img/x", "price": "₹249"}])
        ev = call(app, "/events?after=0&wait=0")[1]["events"][0]
        assert ev["cards"][0]["image"] == "https://img/x" and ev["choices"] == ["1. Veg Biryani ₹249"]
    finally:
        app.close()


def test_voice_is_transcribed_then_handled_like_text():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class FakeSTT(BaseHTTPRequestHandler):
        def do_POST(self):
            audio = self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps({"text": "veg biryani under 300" if audio == b"AUDIO" else ""}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    stt = ThreadingHTTPServer(("127.0.0.1", 0), FakeSTT)
    threading.Thread(target=stt.serve_forever, daemon=True).start()
    app = appapi.App("k", port=0, stt_url=f"http://127.0.0.1:{stt.server_address[1]}")
    try:
        def voice(data, key="k"):
            req = urllib.request.Request(f"http://127.0.0.1:{app.port}/voice?lat=30.44&lng=74.88", data,
                                         {"Authorization": f"Bearer {key}", "Content-Type": "audio/mp4"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        assert voice(b"AUDIO", key="wrong")[0] == 401
        assert voice(b"AUDIO") == (200, {"ok": True, "text": "veg biryani under 300"})
        assert app.poll() == [{"channel": "app", "chat": "owner", "text": "veg biryani under 300",
                               "location": {"lat": 30.44, "lng": 74.88}}]
        assert voice(b"SILENCE") == (200, {"ok": True, "text": ""}) and app.poll() == []
    finally:
        app.close()
        stt.shutdown()


def test_an_otp_box_reaches_the_app():
    app = appapi.App("k", port=0)
    try:
        app.send_input("owner", "Swiggy sent an OTP", {"kind": "otp", "label": "Swiggy OTP"})
        ev = call(app, "/events?after=0&wait=0")[1]["events"][0]
        assert ev["input"] == {"kind": "otp", "label": "Swiggy OTP"} and ev["text"] == "Swiggy sent an OTP"
    finally:
        app.close()
