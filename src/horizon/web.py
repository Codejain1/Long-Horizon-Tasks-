"""Minimal account web page and JSON API (PROJECT.md §10: account, API keys, credits, usage, savings).

Sign in with an API key; the page then uses a session cookie (a random token, stored hashed, HttpOnly,
SameSite=Strict). No dashboard: the "better view" lives in the agent conversation (§10). Server-rendered HTML,
no JavaScript. The JSON API (`/api/...`) takes the API key as a Bearer token.
"""

from __future__ import annotations

from html import escape
from importlib import resources

from fastapi import FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from horizon.accounts import TOOL_CREDITS, Accounts
from horizon.taskstate.store import TaskStore

COOKIE = "horizon_session"
PRIVACY = (resources.files("horizon").joinpath("data/PRIVACY.md")).read_text()


def markdown_page(md: str) -> str:
    """Just enough Markdown for the privacy page: headings, paragraphs, lists, tables, `code`, **bold**, links."""
    import re

    def inline(text: str) -> str:
        text = escape(text)
        text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
        text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
        return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<a href='\2'>\1</a>", text)

    out, table, items = [], [], []

    def flush():
        if table:
            rows = [r for r in table if not re.fullmatch(r"\|?[\s:|-]+\|?", r)]
            cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
            head = "".join(f"<th>{inline(c)}</th>" for c in cells[0])
            body = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in cells[1:])
            out.append(f"<table><tr>{head}</tr>{body}</table>")
            table.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in md.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        if line.startswith("- "):
            items.append(line[2:])
            continue
        flush()
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{inline(line.lstrip('# '))}</h{level}>")
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    flush()
    return "\n".join(out)

STYLE = """
body{font:15px/1.5 system-ui,sans-serif;max-width:920px;margin:2rem auto;padding:0 1rem;color:#1d1d1f;background:#fff}
h1{font-size:1.4rem}h2{font-size:1.05rem;margin-top:2rem;border-bottom:1px solid #ddd;padding-bottom:.25rem}
table{border-collapse:collapse;width:100%;font-size:.9rem}td,th{padding:.35rem .5rem;border-bottom:1px solid #eee;
text-align:left}th{color:#555;font-weight:600}.num{text-align:right;font-variant-numeric:tabular-nums}
.big{font-size:2rem;font-weight:600}.muted{color:#666;font-size:.85rem}.key{font-family:ui-monospace,monospace;
background:#fff6d6;padding:.5rem;display:block;word-break:break-all}.err{color:#b00020}button{cursor:pointer}
input[type=text],input[type=password]{padding:.4rem;width:22rem;max-width:100%}
@media (prefers-color-scheme:dark){body{background:#111;color:#eee}td,th{border-color:#333}th,.muted{color:#aaa}
.key{background:#3a3200}h2{border-color:#333}}
"""


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport "
                        f"content='width=device-width,initial-scale=1'><title>{escape(title)}</title>"
                        f"<style>{STYLE}</style></head><body>{body}</body></html>", status_code=status)


def _table(rows: list[dict], cols: list[tuple[str, str]], empty: str) -> str:
    if not rows:
        return f"<p class=muted>{escape(empty)}</p>"
    head = "".join(f"<th>{escape(label)}</th>" for _, label in cols)
    body = "".join("<tr>" + "".join(
        f"<td class={'num' if isinstance(r.get(k), (int, float)) else ''}>{escape(str(r.get(k, '')))}</td>"
        for k, _ in cols) + "</tr>" for r in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def create_web_app(accounts: Accounts, tasks: TaskStore, lifespan=None) -> FastAPI:
    app = FastAPI(title="Horizon account", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def session_team(request: Request) -> str | None:
        return accounts.session_team(request.cookies.get(COOKIE))

    def api_team(authorization: str | None) -> str:
        from horizon.server import bearer_token

        team = accounts.verify_key(bearer_token(authorization))
        if team is None:
            raise HTTPException(401, "invalid or missing API key")
        return team

    def summary(team: str) -> dict:
        approvals = [a for a in tasks.approvals(team)]
        return {"team": accounts.team(team), "credits": accounts.balance(team), "keys": accounts.keys(team),
                "usage_30d": accounts.usage(team), "savings_by_session": accounts.savings(team),
                "ledger": accounts.ledger(team), "prices": TOOL_CREDITS,
                "human_approvals": {"asked": len(approvals),
                                    "answered": sum(a["action"] == "accept" for a in approvals)}}

    def login_page(error: str = "", status: int = 200) -> HTMLResponse:
        err = f"<p class=err>{escape(error)}</p>" if error else ""
        return page("Horizon: sign in", f"""<h1>Horizon</h1><p>Sign in with one of your team's API keys.</p>{err}
<form method=post action=/login><input type=password name=api_key placeholder="hzn_..." autocomplete=off required>
<button>Sign in</button></form>
<p class=muted>New team? An operator creates it with <code>horizon create-team NAME</code>, which prints the first
key. <a href=/privacy>Data and privacy</a>: no source code is ever stored.</p>""", status)

    @app.get("/healthz")
    def healthz():
        tasks.db.fetchone("SELECT 1")  # the database answers
        return {"ok": True}

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy():
        return page("Horizon: data and privacy", markdown_page(PRIVACY))

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return RedirectResponse("/account", 303) if session_team(request) else login_page()

    @app.post("/login")
    def login(request: Request, api_key: str = Form(...)):
        team = accounts.verify_key(api_key.strip())
        if team is None:
            return login_page("That key isn't valid (or was revoked).", 401)
        response = RedirectResponse("/account", 303)
        response.set_cookie(COOKIE, accounts.open_session(team), httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", max_age=7 * 24 * 3600)
        return response

    @app.post("/logout")
    def logout(request: Request):
        accounts.close_session(request.cookies.get(COOKIE))
        response = RedirectResponse("/", 303)
        response.delete_cookie(COOKIE)
        return response

    def account_page(team: str, new_key: str | None = None) -> HTMLResponse:
        s = summary(team)
        shown = (f"<h2>New key</h2><p>Copy it now: it won't be shown again.</p><code class=key>{escape(new_key)}</code>"
                 if new_key else "")
        keys = "".join(
            f"<tr><td><code>{escape(k['prefix'])}</code></td><td>{escape(k['label'] or '')}</td>"
            f"<td>{escape(k['created_at'][:16])}</td><td>{escape((k['last_used_at'] or 'never')[:16])}</td><td>"
            + ("revoked" if k["revoked"] else
               f"<form method=post action=/keys/{escape(k['id'])}/revoke><button>Revoke</button></form>")
            + "</td></tr>" for k in s["keys"])
        savings_cols = [("session", "Session"), ("tasks", "Tasks"), ("calls", "Calls"), ("credits", "Credits"),
                        ("decisions_scored", "Decisions scored"), ("settled_from_memory", "Settled from memory"),
                        ("options_eliminated", "Options ruled out"), ("regressions_caught", "Regressions caught"),
                        ("fear_warnings", "Fear warnings"), ("human_approvals", "Human approvals"),
                        ("tokens_saved_estimate", "Tokens saved (reported)")]
        body = f"""<form method=post action=/logout style=float:right><button>Sign out</button></form>
<h1>{escape(s['team']['name'])}</h1><p class=muted>{escape(s['team']['id'])}</p>
<h2>Credits</h2><p class=big>{s['credits']:,}</p>
<p class=muted>Per call: {escape(', '.join(f'{t} {c}' for t, c in s['prices'].items()))}. Inspection is free.
Top-ups are added by an operator for now (<code>horizon add-credits</code>).</p>
{shown}<h2>API keys</h2><table><tr><th>Key</th><th>Label</th><th>Created</th><th>Last used</th><th></th></tr>{keys}
</table><form method=post action=/keys style=margin-top:.75rem><input type=text name=label placeholder="Label (optional)">
<button>Create key</button></form>
<h2>Savings per session</h2><p class=muted>Counted events only: what Horizon did that a plain agent wouldn't have.
Tokens saved are counted only where past spikes reported their token cost.</p>
{_table(s['savings_by_session'], savings_cols, 'No metered calls yet.')}
<h2>Usage, last 30 days</h2>{_table(s['usage_30d'], [('tool', 'Tool'), ('calls', 'Calls'), ('credits', 'Credits')],
                                     'No calls yet.')}
<h2>Credit history</h2>{_table(s['ledger'], [('at', 'When'), ('delta', 'Credits'), ('reason', 'Reason')],
                                'No grants yet.')}
<p class=muted>Human approvals asked: {s['human_approvals']['asked']}, answered: {s['human_approvals']['answered']}.
<a href=/privacy>Data and privacy</a>.</p>"""
        return page(f"Horizon: {s['team']['name']}", body)

    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request):
        team = session_team(request)
        return account_page(team) if team else RedirectResponse("/", 303)

    @app.post("/keys", response_class=HTMLResponse)
    def create_key(request: Request, label: str = Form("")):
        team = session_team(request)
        if not team:
            return RedirectResponse("/", 303)
        return account_page(team, new_key=accounts.create_key(team, label.strip()[:60] or None)[1])

    @app.post("/keys/{key_id}/revoke")
    def revoke_key(request: Request, key_id: str):
        team = session_team(request)
        if team:
            accounts.revoke_key(team, key_id)
        return RedirectResponse("/account" if team else "/", 303)

    # --- hosted hooks (the hook bridge: parsed facts from the user's machine) ----------------------------

    @app.post("/api/hooks/{op}")
    async def hooks_api(op: str, request: Request, authorization: str | None = Header(None)):
        from horizon.hooks import Local
        from horizon.models import Checkpoint

        from horizon.server import hosted_scope

        team = api_team(authorization)
        body = await request.json()
        local = Local(tasks, team)
        project = hosted_scope(team, str(body.get("project") or ""))
        session = str(body.get("session_id") or "")[:100] or None
        if op == "session-start":
            return local.session_start(project)
        if op == "lean-session-start":
            return local.lean_session_start(project, session)
        if op == "user-prompt":  # redacted on the client; again here, in case a client doesn't
            from horizon.hooks import MAX_GOAL, lean_user_prompt

            return lean_user_prompt(tasks, team, project, session, str(body.get("prompt") or "")[:MAX_GOAL * 2],
                                    tools=False)
        if op == "capture":
            cap = body["capture"]
            cap = {"command": str(cap["command"])[:200], "runner": str(cap["runner"])[:20],
                   "passed": int(cap["passed"]), "failed": int(cap["failed"]),
                   "failing": None if cap.get("failing") is None else [str(t)[:300] for t in cap["failing"][:500]]}
            return local.capture(project, str(body.get("session_id") or "")[:100] or None, cap)
        if op == "restore-checks":
            return local.restore_checks(project)
        if op == "checkpoint":
            ckpt = Checkpoint.model_validate(body["checkpoint"])
            ckpt.cwd = hosted_scope(team, ckpt.cwd)  # a client can't put a checkpoint into another team's scope
            local.checkpoint(ckpt)
            return None
        if op == "stop":
            return local.stop(project, str(body.get("session_id") or "")[:100] or None)
        if op == "usage":  # counts and a duration only
            raw = body["usage"]
            usage = {k: max(0, int(raw.get(k) or 0)) for k in ("tokens", "input", "output", "cache_write",
                                                                "cache_read", "turns")}
            usage["latency_ms"] = None if raw.get("latency_ms") is None else max(0, int(raw["latency_ms"]))
            local.usage(project, str(body.get("session_id") or "")[:100] or None, usage)
            return None
        raise HTTPException(404, "unknown hook")

    # --- JSON API ------------------------------------------------------------------------

    @app.get("/api/account")
    def api_account(authorization: str | None = Header(None)):
        return summary(api_team(authorization))

    @app.post("/api/keys")
    def api_create_key(authorization: str | None = Header(None), label: str | None = None):
        key_id, raw = accounts.create_key(api_team(authorization), label)
        return JSONResponse({"id": key_id, "key": raw, "note": "Shown once: store it now."}, status_code=201)

    @app.delete("/api/keys/{key_id}")
    def api_revoke_key(key_id: str, authorization: str | None = Header(None)):
        if not accounts.revoke_key(api_team(authorization), key_id):
            raise HTTPException(404, "no such active key")
        return {"revoked": key_id}

    return app
