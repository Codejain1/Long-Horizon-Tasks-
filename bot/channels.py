"""Where messages come from and go: WhatsApp through Hermes's Node bridge (bridge.js, HTTP on 127.0.0.1) and
Telegram's Bot API. Each `poll()` returns new messages as {"channel", "chat", "text", "sender"}; only the owner's
get through. WhatsApp: the bridge drops senders not in WHATSAPP_ALLOWED_USERS itself (its default DM policy
"open"; its matcher also resolves WhatsApp's @lid ids, which a phone-number check here would wrongly reject), so
here only groups are dropped; under policy "pairing" the bridge forwards strangers, and then the phone check
here applies. Telegram: TELEGRAM_ALLOWED_USERS, private chats only."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


def _http(url: str, body: dict | None = None, timeout: float = 40):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data, {"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def _ids(raw: str) -> set[str]:
    return {x.strip().lstrip("+") for x in (raw or "").replace(";", ",").split(",") if x.strip()}


class WhatsApp:
    name = "whatsapp"

    def __init__(self, url: str = "http://127.0.0.1:3000", allowed: str | None = None, http=_http,
                 policy: str | None = None):
        self.url, self.http = url, http
        self.allowed = _ids(os.environ.get("WHATSAPP_ALLOWED_USERS", "") if allowed is None else allowed)
        self.bridge_filters = (os.environ.get("WHATSAPP_DM_POLICY", "open") if policy is None else policy).lower() != "pairing"

    def _owner(self, m: dict) -> bool:
        if m.get("isGroup"):
            return False
        if self.bridge_filters:
            return True
        who = str(m.get("senderId") or m.get("chatId") or "").split("@")[0].split(":")[0]
        return bool(m.get("fromOwner")) or who in self.allowed

    def poll(self) -> list[dict]:
        out = []
        for m in self.http(self.url + "/messages") or []:
            text = (m.get("body") or "").strip()
            if m.get("mediaType") == "poll_update" and text.startswith("[Poll update"):
                continue  # a vote taken back: nothing chosen
            if text and self._owner(m):
                out.append({"channel": self.name, "chat": m["chatId"], "text": text, "sender": m.get("senderName", "")})
        return out

    def send(self, chat: str, text: str) -> None:
        self.http(self.url + "/send", {"chatId": chat, "message": text})

    def send_choice(self, chat: str, text: str, choices: list[str]) -> None:
        """The details, then a one-choice poll: a tap comes back as a message whose text is the option (checked in
        bridge.js: votes on the bot's own polls are queued with body = the chosen option). The text also lists
        the choices numbered, so replying with a number still works if the poll fails."""
        self.send(chat, text)
        try:
            self.http(self.url + "/send-poll", {"chatId": chat, "question": "Choose one",
                                                "options": [c[:100] for c in choices[:12]], "selectableCount": 1})
        except Exception:
            pass  # the numbered text is enough

    def healthy(self) -> bool:
        try:
            return (self.http(self.url + "/health", timeout=5) or {}).get("status") == "connected"
        except Exception:
            return False


class Telegram:
    name = "telegram"

    def __init__(self, token: str | None = None, allowed: str | None = None, http=_http):
        self.token = token if token is not None else os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.allowed = _ids(os.environ.get("TELEGRAM_ALLOWED_USERS", "") if allowed is None else allowed)
        self.http, self.offset = http, 0

    def _api(self, method: str, **params):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        return self.http(url, params or None, timeout=40)

    def poll(self) -> list[dict]:
        if not self.token:
            return []
        r = self._api("getUpdates", offset=self.offset, timeout=0) or {}
        out = []
        for u in r.get("result", []):
            self.offset = max(self.offset, u["update_id"] + 1)
            m = u.get("message") or {}
            sender = str((m.get("from") or {}).get("id", ""))
            if m.get("text") and sender in self.allowed and (m.get("chat") or {}).get("type") == "private":
                out.append({"channel": self.name, "chat": str(m["chat"]["id"]), "text": m["text"].strip(),
                            "sender": (m.get("from") or {}).get("first_name", "")})
        return out

    def send(self, chat: str, text: str) -> None:
        for i in range(0, len(text), 4000):  # Telegram's limit is 4096 characters
            self._api("sendMessage", chat_id=chat, text=text[i:i + 4000], reply_markup={"remove_keyboard": True})

    def send_choice(self, chat: str, text: str, choices: list[str]) -> None:
        """Buttons under the message: a tap sends the button's text back as the owner's message."""
        self._api("sendMessage", chat_id=chat, text=text[:4000],
                  reply_markup={"keyboard": [[{"text": c}] for c in choices], "one_time_keyboard": True,
                                "resize_keyboard": True})


def deliver(channels: dict, target: str, text: str) -> None:
    """'telegram:1000000001' or 'whatsapp:<chat id>' (Hermes's job.deliver format)."""
    name, _, chat = target.partition(":")
    channels[name].send(chat, text)
