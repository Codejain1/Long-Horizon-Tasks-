"""Checkpoint references from the host's own mechanisms (PROJECT.md §8).

We record references, never code: a git commit whose tree is the working tree
at checkpoint time, and the Claude Code prompt to pick in ``/rewind``. Capture
runs on the user's machine (the PreToolUse hook); restoring is done by the host
(git) or the user (``/rewind``), from the steps built here.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess

from horizon.models import Checkpoint, ClaudeCodeRef, GitRef
from horizon.redact import redact

TRANSCRIPT_TAIL_BYTES = 2_000_000  # the last prompt is near the end; don't read a huge transcript whole
PROMPT_SNIPPET = 120


def _git(cwd: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def git_snapshot(cwd: str) -> GitRef | None:
    """Snapshot tracked files without touching the working tree, index, HEAD or stash list.

    `git stash create` writes a commit of the current working tree and returns its id (empty when the
    tree is clean, then HEAD is the snapshot). Untracked files are not included.
    """
    # ponytail: the stash commit is unreferenced, so `git gc` may prune it after gc.pruneExpire
    # (2 weeks by default); pin it under refs/ if tasks ever need rollbacks older than that.
    root = _git(cwd, "rev-parse", "--show-toplevel")
    if root is None:
        return None
    commit = _git(cwd, "stash", "create") or _git(cwd, "rev-parse", "--verify", "HEAD")
    return GitRef(repo=root, commit=commit) if commit else None


def _prompt_text(entry: dict) -> str | None:
    """The text of a real user prompt, or None for tool results, meta and command entries."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return None
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    # "<command-name>…" entries are slash commands; "[Request interrupted by user]" is written by Claude Code.
    if not isinstance(content, str) or content.lstrip().startswith(("<", "[Request interrupted")):
        return None
    if not content.strip():
        return None
    return content


def claude_code_ref(session_id: str | None, transcript_path: str | None) -> ClaudeCodeRef | None:
    """Claude Code checkpoints at each user prompt; the latest prompt is the one to /rewind to."""
    if not session_id or not transcript_path or not os.path.isfile(transcript_path):
        return None
    with open(transcript_path, "rb") as f:
        f.seek(max(0, os.path.getsize(transcript_path) - TRANSCRIPT_TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").splitlines()
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # includes a partial first line from the seek
        text = _prompt_text(entry) if isinstance(entry, dict) else None
        if text:
            return ClaudeCodeRef(session_id=session_id, message_uuid=entry.get("uuid"),
                                 prompt=redact(text, PROMPT_SNIPPET), at=entry.get("timestamp"))
    return None


def capture(cwd: str, session_id: str | None = None, transcript_path: str | None = None) -> Checkpoint | None:
    git = git_snapshot(cwd)
    cc = claude_code_ref(session_id, transcript_path)
    if git is None and cc is None:
        return None
    return Checkpoint(cwd=cwd, session_id=session_id, git=git, claude_code=cc)


def restore_steps(ckpt: Checkpoint | None) -> dict:
    """How to get back to a checkpoint. Git is preferred: the host can run it itself."""
    if ckpt is None or (ckpt.git is None and ckpt.claude_code is None):
        return {"checkpoint_id": None, "git": None, "claude_code": None,
                "note": "No checkpoint was recorded for this attempt. Undo the failed change yourself "
                        "before retrying."}
    out: dict = {"checkpoint_id": ckpt.id, "git": None, "claude_code": None}
    if ckpt.git:
        # Restores tracked files (and the index) to the snapshot, including deleting files added since.
        # HEAD and history are untouched; untracked files are left alone.
        out["git"] = (f"git -C {shlex.quote(ckpt.git.repo)} restore --source={ckpt.git.commit} "
                      f"--staged --worktree -- :/")
    if ckpt.claude_code:
        cc = ckpt.claude_code
        when = f" (sent {cc.at:%Y-%m-%d %H:%M} UTC)" if cc.at else ""
        out["claude_code"] = f'Ask the user to run /rewind and choose the prompt "{cc.prompt}"{when}.'
    return out
