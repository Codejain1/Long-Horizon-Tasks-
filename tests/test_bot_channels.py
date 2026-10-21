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
    wa = C.WhatsApp(allowed="+919800000001", http=http)
    assert [m["text"] for m in wa.poll()] == ["hi", "order food"]
    wa.send("x@lid", "on it")
    assert http.calls[-1] == ("http://127.0.0.1:3000/send", {"chatId": "x@lid", "message": "on it"})


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
