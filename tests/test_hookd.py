"""The hook client and daemon: same output as a direct hook, from a warm process after the first call."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import horizon
from horizon.hooks import run_hook

CLIENT = Path(horizon.__file__).with_name("hookc.py")


def call(name, payload, env):
    proc = subprocess.run([sys.executable, "-S", str(CLIENT), "hook", name, "--profile", "lean"],
                          input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60)
    return proc.stdout.strip()


def test_the_client_matches_a_direct_hook_and_warms_a_daemon(settings, tmp_path):
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("HORIZON_")},
           "HORIZON_DB_URL": settings.db_url, "HORIZON_EMBEDDER": "hash", "HORIZON_HOOKD_IDLE_S": "20"}
    sys.path.insert(0, str(CLIENT.parent))
    import hookc

    sock = hookc.socket_path({"HORIZON_DB_URL": settings.db_url})
    project = str(tmp_path / "p")
    os.makedirs(project)
    payload = {"cwd": project, "session_id": "s1", "prompt": "Build the ledger. Money must never be a float."}
    first = call("user-prompt", payload, env)  # no daemon yet: runs the normal way and starts one
    for _ in range(100):
        if os.path.exists(sock):
            break
        time.sleep(0.1)
    assert os.path.exists(sock) and oct(os.stat(sock).st_mode & 0o777) == "0o600"
    start = {"cwd": project, "session_id": "s2", "source": "startup"}
    warm = call("session-start", start, env)
    assert "Money must never be a float." in warm  # the first call's capture, read back through the daemon
    direct = run_hook("session-start", json.dumps(start), settings, profile="lean")
    assert json.loads(warm) == json.loads(direct)
    assert first == "" or "Horizon" in first
    subprocess.run(["pkill", "-f", f"hookd --socket {sock}"])


def test_hosted_hooks_through_the_daemon_match_and_share_team_rules(settings, tmp_path, free_tcp_port):
    """Hosted lean via the client and daemon: a rule one teammate states reaches another's session start, over
    the same kept-alive connection; the output matches a direct hosted hook."""
    import dataclasses
    import threading

    import uvicorn

    from horizon.accounts import Accounts
    from horizon.db import connect
    from horizon.server import http_app

    app = http_app(dataclasses.replace(settings))
    accounts = Accounts(connect(settings.db_url))
    team, key = accounts.create_team("Acme")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=free_tcp_port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    url = f"http://127.0.0.1:{free_tcp_port}"
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("HORIZON_")},
           "HORIZON_API_KEY": key, "HORIZON_HOOKD_IDLE_S": "20"}

    def hosted(name, payload):
        proc = subprocess.run([sys.executable, "-S", str(CLIENT), "hook", name, "--remote", url, "--profile", "lean"],
                              input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60)
        return proc.stdout.strip()

    try:
        a, b = str(tmp_path / "alice"), str(tmp_path / "bob")
        os.makedirs(a), os.makedirs(b)
        hosted("user-prompt", {"cwd": a, "session_id": "a1", "prompt": "Our team's rules for every service we "
                                                                       "build: ids are uuid4 hex."})
        time.sleep(1)  # the daemon the first call started is up
        warm = hosted("session-start", {"cwd": b, "session_id": "b1", "source": "startup"})
        assert "uuid4 hex" in warm
        os.environ["HORIZON_API_KEY"] = key
        try:
            direct = run_hook("session-start", json.dumps({"cwd": b, "session_id": "b2", "source": "startup"}),
                              remote=url, profile="lean")
        finally:
            del os.environ["HORIZON_API_KEY"]
        assert json.loads(warm) == json.loads(direct)
    finally:
        server.should_exit = True
        subprocess.run(["pkill", "-f", "hookd --socket"])
