"""Gmail Takeout reader for the inbox head-to-head: clean bodies, skip junk labels, oldest email first."""

import mailbox
import sys
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "evals" / "memory"))
import mail_recall  # noqa: E402


def mail(subject, date, text, labels="Inbox", html=False):
    m = EmailMessage()
    m["From"] = "Asha Rao <asha@example.com>"
    m["Subject"] = subject
    m["Date"] = date
    m["X-Gmail-Labels"] = labels
    m.set_content(text, subtype="html" if html else "plain")
    return m


def test_load_cleans_and_orders(tmp_path):
    box = mailbox.mbox(tmp_path / "all.mbox")
    box.add(mail("Invoice", "Tue, 3 Mar 2024 10:00:00 +0000",
                 "Total is 4,200 INR.\n\nOn Mon, 2 Mar 2024, Bob <b@x.com> wrote:\n> old quoted text\n-- \nAsha"))
    box.add(mail("Kickoff", "Mon, 1 Jan 2024 09:00:00 +0000", "<p>Meeting at <b>Taj</b> &amp; lunch</p>", html=True))
    box.add(mail("WIN NOW", "Mon, 1 Jan 2024 09:00:00 +0000", "spam", labels="Spam"))
    box.flush()
    conv = mail_recall.load(str(tmp_path / "all.mbox"))
    assert "session_3" not in conv  # spam skipped
    assert conv["session_1_date_time"] == "1 January 2024"  # oldest first
    first, second = conv["session_1"][0]["text"], conv["session_2"][0]["text"]
    assert first.startswith("Subject: Kickoff.") and "Taj" in first and "&" in first and "<" not in first
    assert "4,200 INR" in second and "quoted" not in second and "wrote" not in second
    assert conv["session_2"][0]["speaker"] == "Asha Rao"


def test_long_bodies_split_into_pieces():
    assert len(mail_recall.chunks("w " * 250)) == 3
