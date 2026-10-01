"""Basic redaction for free-text decision descriptions (PROJECT.md §12).

We store decision summaries, not raw code. Host-written text (situation,
chosen option, reasons, progress notes) goes through ``redact`` before it is
stored: code blocks and code-like lines are removed and the length is capped.
The task goal is the exception: PROJECT.md §8 keeps it verbatim.
"""

from __future__ import annotations

import re

CODE_REMOVED = "[code removed]"

# Caps, in characters, per field.
MAX_SITUATION = 500
MAX_OPTION = 200
MAX_NOTE = 300
MAX_CONDITION_VALUE = 100

_FENCED = re.compile(r"(```|~~~).*?(\1|\Z)", re.S)
# Every inline span is matched, left to right, so backticks pair up correctly; only long ones are removed.
# (Matching only long spans paired the closing backtick of one span with the next span's opening one,
# and removed the prose between them.) Short inline code (names like `httpx`) is kept.
_INLINE = re.compile(r"`[^`\n]*`")
_CODE_START = re.compile(
    r"""^\s*(
        def\s+\w+\s*\( | class\s+\w+[\s(:] | import\s+[\w.]+ | from\s+[\w.]+\s+import\b |
        return\b | (async\s+)?function\b | (export\s+)?(const|let|var)\s+\w+\s*= |
        (public|private|protected|static)\s | \#include\b | package\s+[\w.]+; |
        (if|for|while|switch)\s*\(.*\)\s*\{? | @\w+(\(.*\))?\s*$ | SELECT\s.+\sFROM\s | INSERT\s+INTO\s |
        >>>\s | \$\s+\S
    )""",
    re.X | re.I,
)
_TRACEBACK = re.compile(
    r"""^\s*(
        Traceback\s\(most\srecent\scall\slast\) | File\s".+",\sline\s\d+ | at\s[\w$.<>]+\(.*\)$ |
        [\w.]+(Error|Exception):\s
    )""",
    re.X,
)
_SYMBOLS = set("{}()[];=<>|&$\\")


def _code_like(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _CODE_START.match(line) or _TRACEBACK.match(line):
        return True
    if re.match(r"^( {4,}|\t)\S", line):  # indented block
        return True
    if stripped.endswith(("{", "};", ");")) or stripped in {"}", ")", "]"}:
        return True
    symbols = sum(ch in _SYMBOLS for ch in stripped)
    # At least 3 symbols: a short label like "dbm (stdlib)" is 17% parentheses but not code (seen live).
    return len(stripped) >= 12 and symbols >= 3 and symbols / len(stripped) > 0.15


def redact(text: str | None, max_len: int = MAX_NOTE) -> str | None:
    """Strip code and cap length. Returns None for None; never returns an empty string."""
    if text is None:
        return None
    out = _FENCED.sub(f" {CODE_REMOVED} ", text)
    out = _INLINE.sub(lambda m: CODE_REMOVED if len(m.group()) > 42 else m.group(), out)
    lines = [CODE_REMOVED if _code_like(line) else line.strip() for line in out.splitlines()]
    out = re.sub(r"\s+", " ", " ".join(ln for ln in lines if ln)).strip()
    out = re.sub(rf"(\s*{re.escape(CODE_REMOVED)})+", f" {CODE_REMOVED}", out).strip()
    if len(out) > max_len:
        out = out[: max_len - 1].rstrip() + "…"
    return out or CODE_REMOVED


def redact_list(items: list[str] | None, max_len: int = MAX_OPTION) -> list[str] | None:
    return None if items is None else [redact(i, max_len) for i in items]
