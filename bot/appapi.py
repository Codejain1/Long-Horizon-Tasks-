"""The bot's own app (Expo, `app/`) as a channel next to WhatsApp and Telegram.

A small HTTP API on 127.0.0.1 (the app reaches it through a tunnel; nothing is public), every request with the
secret key in `Authorization: Bearer <key>`:

    POST /messages   {"text": "...", "location"?: {"lat", "lng"}}   -> {"ok": true}
    POST /voice?lat=&lng=   (body: the recorded audio, .m4a)  -> {"ok": true, "text": "what was heard"}
         transcribed by the speech service (bot/stt.py), then handled exactly like a typed message
    GET  /events?after=<id>&wait=<s>  -> {"events": [{"id", "text", "choices"?, "cards"?, "ts"}]}  (waits up to `wait` s)
         cards: [{"choice", "title", "subtitle", "price", "rating", "image", "note", "veg"}]: the app shows them
    GET  /health                       -> {"ok": true}   (no key needed)

The key is BOT_APP_TOKEN, or BOT_HOME/app.token (created on first start, readable only by the bot's user).
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CHAT = "owner"  # one owner, one conversation


def load_token(home: Path) -> str:
    if os.environ.get("BOT_APP_TOKEN"):
        return os.environ["BOT_APP_TOKEN"]
    p = Path(home) / "app.token"
    if not p.exists():
        p.write_text(secrets.token_urlsafe(32))
        p.chmod(0o600)
    return p.read_text().strip()


class App:
    name = "app"

    def __init__(self, token: str, host: str = "127.0.0.1", port: int = 8770, keep: int = 500,
                 stt_url: str = "http://127.0.0.1:8790"):
        self.token, self.inbox, self.events, self.keep, self.stt_url = token, [], [], keep, stt_url
        self.cond, self.next_id = threading.Condition(), 1
        self.server = ThreadingHTTPServer((host, port), self._handler())
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    # channel interface --------------------------------------------------------------------------------------
    def poll(self) -> list[dict]:
        with self.cond:
            msgs, self.inbox = self.inbox, []
        return msgs

    def send(self, chat: str, text: str) -> None:
        self._emit({"text": text})

    def send_choice(self, chat: str, text: str, choices: list[str]) -> None:
        self._emit({"text": text, "choices": choices})

    def send_cards(self, chat: str, text: str, choices: list[str], cards: list[dict]) -> None:
        self._emit({"text": text, "choices": choices, "cards": cards})

    def _emit(self, ev: dict) -> None:
        with self.cond:
            self.events.append({"id": self.next_id, "ts": time.time(), **ev})
            self.next_id += 1
            del self.events[:-self.keep]
            self.cond.notify_all()

    def close(self) -> None:
        self.server.shutdown()

    # HTTP -----------------------------------------------------------------------------------------------------
    def _handler(self):
        app = self

        class H(BaseHTTPRequestHandler):
            def _send(self, obj, code=200):
                body = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authed(self) -> bool:
                got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                if hmac.compare_digest(got, app.token):
                    return True
                self._send({"error": "unauthorized"}, 401)
                return False

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                if u.path == "/health":
                    return self._send({"ok": True})
                if u.path != "/events" or not self._authed():
                    return None if u.path == "/events" else self._send({"error": "not found"}, 404)
                q = urllib.parse.parse_qs(u.query)
                after, wait = int(q.get("after", ["0"])[0]), min(float(q.get("wait", ["25"])[0]), 55)
                end = time.time() + wait
                with app.cond:
                    while not any(e["id"] > after for e in app.events) and time.time() < end:
                        app.cond.wait(max(0.0, end - time.time()))
                    out = [e for e in app.events if e["id"] > after]
                return self._send({"events": out})

            def do_POST(self):
                u = urllib.parse.urlparse(self.path)
                if u.path == "/voice":
                    return self._voice(urllib.parse.parse_qs(u.query))
                if u.path != "/messages":
                    return self._send({"error": "not found"}, 404)
                if not self._authed():
                    return None
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                except json.JSONDecodeError:
                    return self._send({"error": "bad json"}, 400)
                text = str(body.get("text") or "").strip()[:2000]
                if not text:
                    return self._send({"error": "text is required"}, 400)
                msg = {"channel": "app", "chat": CHAT, "text": text}
                loc = body.get("location") or {}
                if isinstance(loc.get("lat"), (int, float)) and isinstance(loc.get("lng"), (int, float)):
                    msg["location"] = {"lat": float(loc["lat"]), "lng": float(loc["lng"])}
                with app.cond:
                    app.inbox.append(msg)
                return self._send({"ok": True})

            def _voice(self, q):
                if not self._authed():
                    return None
                n = int(self.headers.get("Content-Length", 0))
                if not 0 < n <= 10 * 1024 * 1024:
                    return self._send({"error": "audio required (max 10 MB)"}, 400)
                audio = self.rfile.read(n)
                try:
                    req = urllib.request.Request(app.stt_url + "/transcribe", audio, {"Content-Type": "audio/mp4"})
                    with urllib.request.urlopen(req, timeout=60) as r:
                        text = (json.loads(r.read()).get("text") or "").strip()
                except Exception:
                    return self._send({"error": "speech service unavailable"}, 503)
                if not text:
                    return self._send({"ok": True, "text": ""})  # silence: nothing to send
                msg = {"channel": "app", "chat": CHAT, "text": text[:2000]}
                try:
                    msg["location"] = {"lat": float(q["lat"][0]), "lng": float(q["lng"][0])}
                except (KeyError, ValueError, IndexError):
                    pass
                with app.cond:
                    app.inbox.append(msg)
                return self._send({"ok": True, "text": text})

            def log_message(self, *a):
                pass
        return H
