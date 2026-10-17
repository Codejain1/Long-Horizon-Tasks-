"""Head-to-head on a real inbox: the self-built engine searches a Gmail Takeout export, fully on this Mac.

    PYTHONPATH=src ~/horizon-evals/reader-venv/bin/python evals/memory/mail_recall.py "~/path/to/All mail.mbox"

Each email becomes a session (date = the email's date, speaker = the sender); its cleaned body (quoted replies,
signatures and HTML stripped) is split into ~120-word pieces, each carrying the subject. Spam, Trash and
Promotions are skipped. Embeddings are cached, so only the first run is slow. Then type questions: for each,
the top emails found and the search time are printed. Nothing is sent anywhere.
"""

from __future__ import annotations

import email.header
import email.utils
import html
import json
import mailbox
import pickle
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))

SKIP = ("Spam", "Trash", "Category Promotions")
RANKER = Path.home() / "horizon-evals" / "ranker-embed-test.pkl"
_QUOTE = re.compile(r"\n[^\n]{0,40}On [^\n]{5,200}\n?[^\n]{0,100}wrote:.*|\n-- \n.*|\n-+ ?Original Message ?-+.*|"
                    r"\nFrom: [^\n]+\n(Sent|Date): .*", re.S)


def header(msg, name: str) -> str:
    try:
        return str(email.header.make_header(email.header.decode_header(msg.get(name, "") or "")))
    except Exception:
        return str(msg.get(name, "") or "")


def body(msg) -> str:
    parts = list(msg.walk()) if msg.is_multipart() else [msg]
    part = next((p for p in parts if p.get_content_type() == "text/plain" and not p.get_filename()), None) or \
        next((p for p in parts if p.get_content_type() == "text/html"), None)
    if part is None:
        return ""
    raw = part.get_payload(decode=True) or b""
    try:
        text = raw.decode(part.get_content_charset() or "utf-8", "replace")
    except LookupError:
        text = raw.decode("utf-8", "replace")
    if part.get_content_type() == "text/html":
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.S | re.I)
        text = html.unescape(re.sub(r"<br\s*/?>|</p>|</div>", "\n", re.sub(r"<(?!br|/p|/div)[^>]+>", " ", text)))
    return clean(text)


def clean(text: str) -> str:
    text = _QUOTE.sub("", "\n" + text.replace("\r\n", "\n"))
    lines = [ln.strip() for ln in text.split("\n") if not ln.lstrip().startswith(">")]
    return re.sub(r"[ \t\xa0]+", " ", "\n".join(ln for ln in lines if ln)).strip()


def chunks(text: str, size: int = 120) -> list[str]:
    w = text.split()
    return [" ".join(w[i:i + size]) for i in range(0, len(w), size)] or [""]


def load(path: str) -> dict:
    """mbox -> the conversation format the engine reads: one session per email, oldest first."""
    mails = []
    for msg in mailbox.mbox(path, create=False):
        if any(s in (msg.get("X-Gmail-Labels", "") or "") for s in SKIP):
            continue
        try:
            when = email.utils.parsedate_to_datetime(msg["Date"])
        except Exception:
            continue
        sender = email.utils.parseaddr(header(msg, "From"))
        subject = header(msg, "Subject")
        mails.append((when.timestamp(), when.strftime("%-d %B %Y"), sender[0] or sender[1], subject, body(msg)))
    mails.sort()
    conv = {"speaker_a": "me", "speaker_b": "them"}
    for i, (_, date, sender, subject, text) in enumerate(mails, 1):
        conv[f"session_{i}"] = [{"speaker": sender, "dia_id": f"M{i}:{k}", "text": f"Subject: {subject}. {c}"}
                                for k, c in enumerate(chunks(text))]
        conv[f"session_{i}_date_time"] = date
    return conv


def ranker(engine):
    import generalize as G
    import locomo
    if RANKER.exists():
        return pickle.loads(RANKER.read_bytes())
    data = json.loads(locomo.DATA.read_text())
    items = locomo.flatten(data)
    ev = {f"{s['sample_id']}:{qa['question'][:48]}": qa.get("evidence", []) for s in data for qa in s["qa"]}
    for it in items:
        it["evidence"] = ev.get(it["id"], [])
    test_ids = {it["id"] for it in locomo.sample_items(list(items), 150)}
    train = [it for it in items if it["id"] not in test_ids and it["evidence"]]
    convs = {s["sample_id"]: G.index(s["conversation"], engine, cache=f"locomo-embed-test-{s['sample_id']}")
             for s in data}
    model = G.train_ranker(train, convs, engine)
    RANKER.write_bytes(pickle.dumps(model))
    return model


def main() -> int:
    import generalize as G  # torch: loaded only for the real run, not for the reader's tests
    import learned_recall as L

    path = Path(sys.argv[1]).expanduser()
    t = time.perf_counter()
    conv_d = load(str(path))
    n = sum(1 for k in conv_d if k.endswith("_date_time"))
    engine = G.Engine(str(Path.home() / "horizon-evals" / "embed-test"))
    conv = G.index(conv_d, engine, cache=f"mail-{path.stat().st_size}")
    model = ranker(engine)
    by_sess = {}
    for j in range(len(conv.turns)):
        by_sess.setdefault(conv.session[j], []).append(j)
    print(f"indexed {n} emails ({len(conv.turns)} pieces) in {time.perf_counter() - t:.0f}s. Ask away (blank to quit).")
    for q in iter(lambda: input("\n? ").strip(), ""):
        t = time.perf_counter()
        qv = engine.question(q)
        probs = model.predict(L.features(conv, q, qv))
        ms = 1000 * (time.perf_counter() - t)
        seen = []
        for j in np.argsort(-probs):
            if conv.session[j] not in seen:
                seen.append(conv.session[j])
            if len(seen) == 5:
                break
        print(f"  search {ms:.0f} ms")
        for rank, s in enumerate(seen, 1):
            first = conv.turns[by_sess[s][0]]
            best = max(by_sess[s], key=lambda j: probs[j])
            print(f"  {rank}. [{first['date']}] {first['speaker']} | {first['text'][:90]}\n"
                  f"     … {conv.turns[best]['text'][:240]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
