"""Two minutes before any long run: every way a run broke on us before the model did anything.

    python bench/preflight.py --model-url URL [--image IMG] [--scenario rental]

Each line is one check; exit 1 if any fails. What it catches, each from a real lost run:
  ._ metadata files in the code (v3m: every runner job crashed at startup)
  the model endpoint down or not answering a chat
  the sandbox image can't start, or can't reach the task sites with the runner's user agent (Nominatim 403)
  the runner can't read the scenario project (brownfield start: existing tests found and passing)
  the hidden tests don't fail on the start (a scenario that is already solved measures nothing)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runner"))
sys.path.insert(0, str(ROOT / "bench"))
import longrun as LR  # noqa: E402

SITES = ["https://nominatim.openstreetmap.org/search?q=cafe&format=jsonv2&limit=1", "https://www.python.org/downloads/",
         "https://nodejs.org/en/download", "https://www.postgresql.org/", "https://www.google.com/travel/flights"]


def checks(args):
    dot = [str(p.relative_to(ROOT)) for p in ROOT.rglob("._*")]
    yield not dot, f"no ._ files in the code ({len(dot)}: {dot[:3]})"
    try:
        r = LR.chat(args.model_url, [{"role": "user", "content": "Say ok."}], timeout=60)
        yield bool(r.get("content") is not None), "the model answers a chat"
    except Exception as e:
        yield False, f"the model answers a chat: {e!r}"[:200]
    box = None
    try:
        tmp = Path(tempfile.mkdtemp(prefix="preflight-"))
        box = LR.LocalSandbox(tmp) if args.local else LR.PodmanSandbox(tmp, image=args.image)
        for url in SITES:
            code, out = box.run(f"curl -sL -o /dev/null -w '%{{http_code}}' --max-time 20 -A '{LR.UA}' '{url}'", timeout=40)
            yield out.strip().startswith(("2", "3")), f"sandbox reaches {url[:60]} (HTTP {out.strip()[-3:]})"
    except Exception as e:
        yield False, f"the sandbox starts: {e!r}"[:200]
    finally:
        if box:
            box.close()
    import backlog as BL
    import tickets as TK
    sdir, sc = BL.scenario(args.scenario)
    with tempfile.TemporaryDirectory() as t:
        repo = TK.build_state(sdir, sc, 1, Path(t) / "repo")
        box = LR.LocalSandbox(repo) if args.local else LR.PodmanSandbox(repo, image=args.image)  # as the jobs run
        rn = LR.Runner("preflight", box, None, Path(t) / "r.jsonl")
        if args.local:
            rn.PYTEST = f"{sys.executable} -m pytest"
        try:
            rn._start()
            yield rn.brownfield and bool(rn.passing_before), (f"the runner reads {args.scenario}: {len(rn.base_names)} "
                                                              f"modules, {len(rn.passing_before)} existing tests passing")
        except Exception as e:
            yield False, f"the runner reads {args.scenario}: {e!r}"[:200]
        finally:
            box.close()
        passed, failed, _ = BL.hidden(sdir, 1, repo, Path(t) / "h", args.image)
        yield bool(failed) and not passed, f"ticket 1's hidden tests fail on the start ({len(failed)} failing)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-url", required=True)
    ap.add_argument("--image", default="docker.io/nikolaik/python-nodejs:python3.11-nodejs20")
    ap.add_argument("--scenario", default="rental")
    ap.add_argument("--local", action="store_true", help="no podman (the Mac)")
    args = ap.parse_args()
    bad = 0
    for ok, what in checks(args):
        print(("ok    " if ok else "FAIL  ") + what, flush=True)
        bad += not ok
    print(json.dumps({"preflight": "pass" if not bad else f"{bad} failed"}))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
