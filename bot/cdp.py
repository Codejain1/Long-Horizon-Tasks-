"""Drive the desktop Chrome (where the owner is logged in) over the DevTools protocol, stdlib only.

Chrome listens on 127.0.0.1:9222 (hermes-chrome.service). The bot works in its own tab, so it never touches the
tabs the owner has open. `Tab.open(url)` waits until the page's text stops changing; `text()` is what a person
sees; `click_text(...)` clicks the smallest element whose text contains all the given strings.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.parse
import urllib.request

DEVTOOLS = os.environ.get("CDP_URL", "http://127.0.0.1:9222")


class WebSocket:
    """Just enough RFC 6455 for CDP: text frames out (masked), text frames in (fragments joined), ping answered."""

    def __init__(self, url: str, timeout: float = 30):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise ConnectionError("websocket handshake: connection closed")
            head += chunk
        status = head.split(b"\r\n")[0]
        if b" 101 " not in status:
            raise ConnectionError(f"websocket handshake refused: {status!r}")
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _read(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                                         if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, text: str) -> None:
        self._frame(0x1, text.encode())

    def recv(self) -> str:
        parts = []
        while True:
            b1, b2 = self._read(2)
            op, n = b1 & 0x0F, b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            data = self._read(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 0x9:
                self._frame(0xA, data)
                continue
            if op == 0x8:
                raise ConnectionError("websocket closed by Chrome")
            parts.append(data)
            if b1 & 0x80:
                return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Tab:
    def __init__(self, devtools: str = DEVTOOLS, ws_factory=WebSocket):
        req = urllib.request.Request(devtools + "/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req, timeout=10) as r:
            info = json.loads(r.read())
        self.id, self.devtools, self.n = info["id"], devtools, 0
        self.ws = ws_factory(info["webSocketDebuggerUrl"])

    def call(self, method: str, **params) -> dict:
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"page script failed: {r['exceptionDetails'].get('text')}")
        return r.get("result", {}).get("value")

    def text(self) -> str:
        return self.js("document.body ? document.body.innerText : ''") or ""

    def settle(self, timeout: float = 20, sleep=time.sleep) -> str:
        """Wait until the page's text stops changing (single-page apps keep loading after 'complete')."""
        last, same, t = "", 0, 0.0
        while t < timeout:
            sleep(0.7)
            t += 0.7
            now = self.text()
            same = same + 1 if now == last and len(now) > 200 else 0
            if same >= 2:
                return now
            last = now
        return last

    def open(self, url: str) -> str:
        self.call("Page.navigate", url=url)
        return self.settle()

    def click_text(self, *needles: str, button: str | None = None) -> bool:
        """Click the smallest element whose text contains every needle (case-insensitive); with `button`, click the
        button/link with that exact text inside it instead. True if something was clicked."""
        js = """((needles, button) => {
          const has = el => needles.every(n => (el.innerText || '').toLowerCase().includes(n.toLowerCase()));
          let best = null;
          for (const el of document.querySelectorAll('body *')) {
            if (el.offsetParent === null || !has(el)) continue;
            if (!best || (el.innerText || '').length < (best.innerText || '').length) best = el;
          }
          if (!best) return false;
          let target = best;
          if (button) {
            let scope = best;
            for (let i = 0; i < 6 && scope; i++, scope = scope.parentElement) {
              const b = [...scope.querySelectorAll('button, [role=button], a, div')].find(
                x => (x.innerText || '').trim().toLowerCase() === button.toLowerCase() && x.offsetParent !== null);
              if (b) { target = b; break; }
            }
            if (target === best) return false;
          }
          target.scrollIntoView({block: 'center'}); target.click(); return true;
        })(%s, %s)""" % (json.dumps(list(needles)), json.dumps(button))
        return bool(self.js(js))

    def scroll_end(self) -> None:
        self.js("window.scrollTo(0, document.body.scrollHeight)")

    def close(self) -> None:
        self.ws.close()
        try:
            urllib.request.urlopen(f"{self.devtools}/json/close/{self.id}", timeout=5).read()
        except Exception:
            pass
