"""The bot's way out through the owner's home internet, for sites that block the server (Zomato: Access Denied to
the data-centre IP, even in real Chrome). Runs on the server (systemd owner-homeproxy):

    agents  127.0.0.1:8772  the home helper (home/agent.py) keeps idle connections open here, through an SSM tunnel
                            the home machine starts (nothing is opened to the internet, on either side)
    proxy   127.0.0.1:8773  an HTTP proxy for the bot's "home" Chrome (owner-home-chrome, CDP 127.0.0.1:9223): each request
                            takes one idle agent connection, which dials the site from home
    GET /status on 8773     {"online": bool, "idle": n, "seen_s": seconds since a helper last connected}

What the home side may open is decided at home (agent.py: listed sites only, ports 80/443, never a home address).
"""

from __future__ import annotations

import json
import queue
import select
import socket
import threading
import time
import urllib.parse


def pipe(a: socket.socket, b: socket.socket, idle: float = 300) -> None:
    """Bytes both ways until either side closes (or nothing moves for `idle` s)."""
    try:
        while True:
            r, _, _ = select.select([a, b], [], [], idle)
            if not r:
                return
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        return
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def _readline(s: socket.socket, limit: int = 1024) -> bytes:
    out = b""
    while not out.endswith(b"\n") and len(out) < limit:
        c = s.recv(1)
        if not c:
            break
        out += c
    return out


class HomeProxy:
    def __init__(self, host: str = "127.0.0.1", agent_port: int = 8772, proxy_port: int = 8773):
        self.idle: queue.Queue = queue.Queue()
        self.seen = 0.0
        self.agents = socket.create_server((host, agent_port))
        self.proxy = socket.create_server((host, proxy_port))
        self.agent_port, self.proxy_port = self.agents.getsockname()[1], self.proxy.getsockname()[1]
        threading.Thread(target=self._accept_agents, daemon=True).start()
        threading.Thread(target=self._accept_clients, daemon=True).start()

    def _accept_agents(self) -> None:
        while True:
            conn, _ = self.agents.accept()
            self.seen = time.time()
            self.idle.put(conn)

    def _accept_clients(self) -> None:
        while True:
            conn, _ = self.proxy.accept()
            threading.Thread(target=self._client, args=(conn,), daemon=True).start()

    def take(self, timeout: float = 3) -> socket.socket | None:
        """An idle agent connection that is still open (the tunnel may have dropped it), or None: home is offline."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                a = self.idle.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                return None
            try:
                a.setblocking(False)
                closed = a.recv(1, socket.MSG_PEEK) == b""
            except BlockingIOError:
                closed = False
            except OSError:
                closed = True
            a.setblocking(True)
            if not closed:
                return a
            a.close()
        return None

    def status(self) -> dict:
        return {"online": not self.idle.empty() and time.time() - self.seen < 600, "idle": self.idle.qsize(),
                "seen_s": round(time.time() - self.seen) if self.seen else None}

    def _client(self, c: socket.socket) -> None:
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 65536:
            chunk = c.recv(4096)
            if not chunk:
                return c.close()
            head += chunk
        line, _, rest = head.partition(b"\r\n")
        try:
            method, target, version = line.decode("latin-1").split(" ", 2)
        except ValueError:
            return self._reply(c, 400, "bad request")
        if target == "/status":
            return self._reply(c, 200, json.dumps(self.status()), "application/json")
        if method == "CONNECT":
            host, _, port = target.rpartition(":")
            first = b""
        else:  # plain http: the request goes on with its path only
            u = urllib.parse.urlsplit(target)
            host, port = u.hostname or "", str(u.port or 80)
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            first = f"{method} {path} {version}\r\n".encode("latin-1") + rest
        for _ in range(3):  # an agent connection can close just as it's taken
            a = self.take()
            if a is None:
                return self._reply(c, 502, "home connection offline")
            try:
                a.sendall(f"{host}:{port}\n".encode())
                answer = _readline(a).decode().strip()
            except OSError:
                a.close()
                continue
            if not answer:
                a.close()
                continue
            if answer != "OK":
                a.close()
                return self._reply(c, 403, f"home refused: {answer}")
            if method == "CONNECT":
                c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                a.sendall(first)
            return pipe(c, a)
        return self._reply(c, 502, "home connection offline")

    @staticmethod
    def _reply(c: socket.socket, code: int, text: str, kind: str = "text/plain") -> None:
        body = text.encode()
        try:
            c.sendall(f"HTTP/1.1 {code} {'OK' if code == 200 else 'Error'}\r\nContent-Type: {kind}\r\n"
                      f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        finally:
            c.close()


def home_status(url: str = "http://127.0.0.1:8773/status") -> dict:
    """For the bot: is the home connection up right now?"""
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return {"online": False, "idle": 0, "seen_s": None}


if __name__ == "__main__":
    HomeProxy()
    while True:
        time.sleep(3600)
