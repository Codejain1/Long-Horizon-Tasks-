"""Speech to text for the app's hold-to-talk: a small service on 127.0.0.1 (systemd owner-stt), run with the
venv that has faster-whisper (CPU, int8). Kept out of the bot process so a voice problem never stops chat.

    /opt/stt-venv/bin/python bot/stt.py [--model small] [--port 8790]
    POST /transcribe  (body: the audio file, e.g. .m4a)  -> {"text": "...", "language": "en|hi|...", "seconds": 1.9}
    GET  /health -> {"ok": true, "model": "small"}
"""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BYTES = 10 * 1024 * 1024  # ~10 minutes of AAC; a hold-to-talk note is seconds


def serve(model_name: str, port: int) -> None:
    from faster_whisper import WhisperModel
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    lock = threading.Lock()  # one transcription at a time: 4 CPUs, keep the bot responsive

    class H(BaseHTTPRequestHandler):
        def _send(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"ok": True, "model": model_name} if self.path == "/health" else {"error": "not found"},
                       200 if self.path == "/health" else 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            if self.path != "/transcribe" or not 0 < n <= MAX_BYTES:
                return self._send({"error": "POST /transcribe with the audio (max 10 MB)"}, 400)
            data = self.rfile.read(n)
            t0 = time.time()
            try:
                with tempfile.NamedTemporaryFile(suffix=".m4a") as f, lock:
                    f.write(data)
                    f.flush()
                    # Indian English and Hindi: no language forced, Whisper detects it; a short prompt nudges
                    # spellings of the words the owner uses most
                    segs, info = model.transcribe(f.name, beam_size=1, vad_filter=True,
                                                  initial_prompt="Swiggy, Zomato, biryani, paneer, dosa, IRCTC, ixigo, redbus, Bangalore")
                    text = " ".join(s.text.strip() for s in segs).strip()
            except Exception as e:
                return self._send({"error": f"couldn't read the audio: {type(e).__name__}"}, 422)
            self._send({"text": text, "language": info.language, "seconds": round(time.time() - t0, 2)})

        def log_message(self, *a):
            pass

    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="small")
    ap.add_argument("--port", type=int, default=8790)
    a = ap.parse_args()
    serve(a.model, a.port)
