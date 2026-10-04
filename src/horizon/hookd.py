"""The hook daemon: runs hook calls in a warm process, so each costs milliseconds instead of a Python start.

Started by `hookc.py` on first use, one per interpreter, code version and database (the socket name), listening
on a socket only this user can reach. It handles one call at a time, exactly as `horizon hook ...` would: the
call's environment, working directory and stdin are put in place for it and restored afterwards. It exits after
`IDLE_S` without calls, or when another daemon already holds the socket.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys

from horizon.hookc import ENV_PREFIXES, recv, send

IDLE_S = int(os.environ.get("HORIZON_HOOKD_IDLE_S") or 600)


@contextlib.contextmanager
def call_context(env: dict, cwd: str, stdin: str):
    """The hook call's environment variables (only Horizon's prefixes), working directory and stdin."""
    saved_env = {k: v for k, v in os.environ.items() if k.startswith(ENV_PREFIXES)}
    saved_cwd, saved_in = os.getcwd(), sys.stdin
    for k in saved_env:
        del os.environ[k]
    os.environ.update(env)
    try:
        os.chdir(cwd)
    except OSError:
        pass
    sys.stdin = io.StringIO(stdin)
    try:
        yield
    finally:
        sys.stdin = saved_in
        os.chdir(saved_cwd)
        for k in [k for k in os.environ if k.startswith(ENV_PREFIXES)]:
            del os.environ[k]
        os.environ.update(saved_env)


def handle(request: dict) -> str:
    """One hook call, through the same CLI path the host would run; errors are swallowed like any hook's."""
    from horizon.cli import main

    out = io.StringIO()
    with call_context(request.get("env") or {}, request.get("cwd") or os.getcwd(), request.get("stdin") or ""):
        try:
            with contextlib.redirect_stdout(out):
                main(list(request["args"]))
        except BaseException:  # a hook must never break the host session, nor the daemon
            pass
    return out.getvalue().rstrip("\n")


def serve(path: str) -> None:
    import horizon.hooks  # noqa: F401  (warm: the import is what each cold hook pays for)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(path)
    except OSError:  # another daemon got there first
        return
    os.chmod(path, 0o600)
    server.listen(16)
    server.settimeout(IDLE_S)
    try:
        while True:
            try:
                conn, _ = server.accept()
            except TimeoutError:
                return
            with conn:
                try:
                    conn.settimeout(30)
                    send(conn, json.dumps({"out": handle(json.loads(recv(conn)))}).encode())
                except Exception:
                    continue
    finally:
        server.close()
        with contextlib.suppress(OSError):
            os.unlink(path)
