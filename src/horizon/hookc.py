"""The hook client: what the host runs on every hook, with `python -S` and nothing but the standard library.

A full hook starts Python and imports Horizon (about 0.2 s, mostly pydantic), and lean runs one on almost every
tool call. This client hands the call to a warm `horizon hookd` over a local socket instead, and starts one if
there is none; while it starts (or if anything fails), the call runs the normal way, so the result is the same.
Usage, as the installers write it: `python -S .../horizon/hookc.py hook <name> [--profile ...] [--remote ...]`.
"""

import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# What the daemon needs from the hook's environment (settings, keys, the host's project dir); nothing else.
ENV_PREFIXES = ("HORIZON_", "TYPESAFE_", "ANTHROPIC_", "CLAUDE_", "CURSOR_")
TIMEOUT_S = 25  # under the installers' 30 s hook timeout


def code_stamp() -> str:
    """Changes whenever Horizon's code does, so an upgraded install never talks to an old daemon."""
    newest = 0.0
    for root, _, files in os.walk(HERE):
        for f in files:
            if f.endswith(".py"):
                newest = max(newest, os.path.getmtime(os.path.join(root, f)))
    return str(newest)


def socket_path(env: dict) -> str:
    """One daemon per interpreter, code version and database: separate runs and arms never share one."""
    key = "|".join([sys.executable, code_stamp(), env.get("HORIZON_DB_URL", ""), str(os.getuid())])
    folder = os.path.join(tempfile.gettempdir(), f"horizon-{os.getuid()}")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    return os.path.join(folder, hashlib.sha256(key.encode()).hexdigest()[:16] + ".sock")


def send(sock: socket.socket, data: bytes) -> None:
    sock.sendall(struct.pack(">Q", len(data)) + data)


def recv(sock: socket.socket) -> bytes:
    def exactly(n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    (n,) = struct.unpack(">Q", exactly(8))
    return exactly(n)


def via_daemon(path: str, args: list, stdin: str, env: dict) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(TIMEOUT_S)
        s.connect(path)
        send(s, json.dumps({"args": args, "stdin": stdin, "env": env, "cwd": os.getcwd()}).encode())
        return json.loads(recv(s))["out"]


def start_daemon(path: str) -> None:
    subprocess.Popen([sys.executable, "-m", "horizon", "hookd", "--socket", path], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=os.path.expanduser("~"))


def main() -> int:
    args = sys.argv[1:]
    stdin = sys.stdin.read()
    env = {k: v for k, v in os.environ.items() if k.startswith(ENV_PREFIXES)}
    if os.environ.get("HORIZON_HOOKD") != "off":
        path = socket_path(env)
        try:
            out = via_daemon(path, args, stdin, env)
        except (OSError, ValueError, KeyError):
            if not os.path.exists(path):
                start_daemon(path)  # for the next call; this one runs the normal way
        else:
            if out:
                print(out)
            return 0
    proc = subprocess.run([sys.executable, "-m", "horizon", *args], input=stdin, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
