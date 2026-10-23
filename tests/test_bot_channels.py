"""Channels pass only the owner's messages and send in the bridge's and Telegram's formats."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
import channels as C  # noqa: E402


class FakeHTTP:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def __call__(self, url, body=None, timeout=40):
        self.calls.append((url, body))
        return self.replies.pop(0) if self.replies else None


def test_whatsapp_owner_only():
    inbox = [{"chatId": "919800000001@s.whatsapp.net", "senderId": "919800000001@s.whatsapp.net", "body": "hi"},
             {"chatId": "911111111111@s.whatsapp.net", "senderId": "911111111111@s.whatsapp.net", "body": "spam"},
             {"chatId": "x@lid", "fromOwner": True, "body": " order food "},
             {"chatId": "g@g.us", "isGroup": True, "fromOwner": True, "body": "group"},
             {"chatId": "919800000001@s.whatsapp.net", "senderId": "919800000001@s.whatsapp.net", "body": ""}]
    http = FakeHTTP([inbox, None])
    wa = C.WhatsApp(allowed="+919800000001", http=http, policy="pairing")  # bridge forwards strangers: we check
    assert [m["text"] for m in wa.poll()] == ["hi", "order food"]
    wa.send("x@lid", "on it")
    assert http.calls[-1] == ("http://127.0.0.1:3000/send", {"chatId": "x@lid", "message": "on it"})


def test_whatsapp_trusts_the_bridge_allowlist_by_default():
    inbox = [{"chatId": "8123@lid", "senderId": "8123@lid", "body": "owner via lid"},
             {"chatId": "g@g.us", "isGroup": True, "body": "group"}]
    wa = C.WhatsApp(allowed="+919800000001", http=FakeHTTP([inbox]), policy="open")
    assert [m["text"] for m in wa.poll()] == ["owner via lid"]


def test_telegram_owner_private_chats_and_offset():
    upd = {"result": [
        {"update_id": 5, "message": {"text": "hello", "from": {"id": 42}, "chat": {"id": 42, "type": "private"}}},
        {"update_id": 6, "message": {"text": "hi", "from": {"id": 7}, "chat": {"id": 7, "type": "private"}}},
        {"update_id": 7, "message": {"text": "grp", "from": {"id": 42}, "chat": {"id": -1, "type": "group"}}}]}
    http = FakeHTTP([upd, {"result": []}])
    tg = C.Telegram(token="T", allowed="42", http=http)
    assert tg.poll() == [{"channel": "telegram", "chat": "42", "text": "hello", "sender": ""}]
    tg.poll()
    assert http.calls[-1][1]["offset"] == 8
    tg.send("42", "x" * 4500)
    assert [len(c[1]["text"]) for c in http.calls[-2:]] == [4000, 500]
    assert C.Telegram(token="", http=http).poll() == []


def test_deliver_routes_by_prefix():
    got = []

    class Ch:
        def send(self, chat, text):
            got.append((chat, text))
    C.deliver({"telegram": Ch()}, "telegram:1000000001", "digest")
    assert got == [("1000000001", "digest")]


def test_telegram_choices_are_buttons_and_whatsapp_gets_the_numbered_text():
    http = FakeHTTP([])
    tg = C.Telegram(token="T", allowed="42", http=http)
    tg.send_choice("42", "Best options:\n1. Veg Biryani ₹249", ["1. Veg Biryani ₹249", "No"])
    body = http.calls[-1][1]
    assert body["reply_markup"]["keyboard"] == [[{"text": "1. Veg Biryani ₹249"}], [{"text": "No"}]]
    assert body["reply_markup"]["one_time_keyboard"] is True
    wa_http = FakeHTTP([])
    C.WhatsApp(http=wa_http).send_choice("x@lid", "Best options:\n1. Veg Biryani ₹249", ["1. Veg Biryani ₹249"])
    (url1, text), (url2, poll) = wa_http.calls[-2:]
    assert url1.endswith("/send") and text["message"].endswith("1. Veg Biryani ₹249")
    assert url2.endswith("/send-poll") and poll == {"chatId": "x@lid", "question": "Choose one",
                                                    "options": ["1. Veg Biryani ₹249"], "selectableCount": 1}


def test_whatsapp_poll_votes_arrive_as_the_chosen_text_and_unvotes_are_dropped():
    inbox = [{"chatId": "x@lid", "body": "1. Veg Biryani ₹249", "mediaType": "poll_update"},
             {"chatId": "x@lid", "body": "[Poll update: ABC123]", "mediaType": "poll_update"}]
    wa = C.WhatsApp(http=FakeHTTP([inbox]), policy="open")
    assert [m["text"] for m in wa.poll()] == ["1. Veg Biryani ₹249"]


def test_main_sends_choices_as_buttons():
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).parent.parent / "bot"))
    import assistant as A
    import main as M

    class Ch:
        name = "telegram"

        def __init__(self):
            self.inbox, self.plain, self.choices = [{"channel": "telegram", "chat": "42", "text": "hi"}], [], []

        def poll(self):
            msgs, self.inbox = self.inbox, []
            return msgs

        def send(self, chat, text):
            self.plain.append(text)

        def send_choice(self, chat, text, choices):
            self.choices.append(choices)

    class Bot:
        def handle(self, m, notify=None):
            return A.Reply("Pick one", ["1. A", "2. B"])

    class Sched:
        def tick(self):
            pass
    ch = Ch()
    M.serve({"telegram": ch}, Bot(), Sched(), once=True, check=lambda: True, model_check=lambda: True)
    assert ch.choices == [["1. A", "2. B"]] and ch.plain == []
