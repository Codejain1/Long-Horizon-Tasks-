"""The home side of the bot's home connection (see bot/homeproxy.py): runs on a machine at the owner's home, keeps
idle connections open to the server (through the SSM tunnel home/run.sh starts) and, when the bot asks, opens the
site from here. Standard library only.

    python3 home/agent.py [--server 127.0.0.1:8772] [--pool 16]

Rails, decided here at home and not by the server: only the sites listed in HOME_SITES (default below; a refused
host is printed, so a missing one is easy to add), only ports 80 and 443, and never an address that isn't public
(the home network, the router, this machine).
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import select
import socket
import sys
import threading
import time

SITES = [s.strip().lower() for s in os.environ.get("HOME_SITES", "zomato.com,zmtcdn.com").split(",") if s.strip()]


def allowed(host: str, port: int, sites=SITES, resolve=socket.getaddrinfo) -> tuple[str | None, str]:
    """(public address to dial, "") or (None, why not)."""
    host = host.strip("[]").lower().rstrip(".")
    if port not in (80, 443):
        return None, f"port {port}"
    if not any(host == s or host.endswith("." + s) for s in sites):
        return None, f"{host} isn't a listed site"
    try:
        addrs = [a[4][0] for a in resolve(host, port, type=socket.SOCK_STREAM)]
    except OSError:
        return None, f"{host} doesn't resolve"
    if not addrs or not all(ipaddress.ip_address(a).is_global for a in addrs):
        return None, f"{host} points at a non-public address"
    return addrs[0], ""


def pipe(a: socket.socket, b: socket.socket, idle: float = 300) -> None:
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
        a.close()
        b.close()


def serve_one(server: tuple[str, int], recycle: float = 240) -> bool:
    """One idle connection to the server, then one site for it. False when the server can't be reached."""
    try:
        s = socket.create_connection(server, timeout=10)
    except OSError:
        return False
    s.settimeout(recycle)  # an idle connection is replaced now and then, so a dropped tunnel is noticed
    try:
        line = b""
        while not line.endswith(b"\n"):
            c = s.recv(1)
            if not c:
                s.close()
                return True
            line += c
    except (socket.timeout, OSError):
        s.close()
        return True
    host, _, port = line.decode().strip().rpartition(":")
    addr, why = allowed(host, int(port) if port.isdigit() else 0)
    if not addr:
        print(f"refused {host}:{port}: {why}", file=sys.stderr, flush=True)
        s.sendall(f"NO {why}\n".encode())
        s.close()
        return True
    try:
        d = socket.create_connection((addr, int(port)), timeout=10)
    except OSError as e:
        s.sendall(f"NO can't reach {host} ({type(e).__name__})\n".encode())
        s.close()
        return True
    s.settimeout(None)
    s.sendall(b"OK\n")
    pipe(s, d)
    return True


def worker(server: tuple[str, int]) -> None:
    wait = 1
    while True:
        if serve_one(server):
            wait = 1
        else:
            time.sleep(wait)
            wait = min(wait * 2, 30)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="127.0.0.1:8772")
    ap.add_argument("--pool", type=int, default=16)
    a = ap.parse_args()
    host, _, port = a.server.rpartition(":")
    print(f"home connection: {a.pool} slots to {a.server}; sites: {', '.join(SITES)}", flush=True)
    for _ in range(a.pool):
        threading.Thread(target=worker, args=((host, int(port)),), daemon=True).start()
    while True:
        time.sleep(3600)
