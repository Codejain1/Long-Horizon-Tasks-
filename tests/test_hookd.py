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
