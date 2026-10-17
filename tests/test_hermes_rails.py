"""Hermes rails: money, orders, secrets and public links decided in code."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "hermes"))
import rails as R  # noqa: E402

SWIGGY_CART = """- heading "Checkout" [ref=e1]
- radio "Cash on Delivery" [ref=e7]
- radio "UPI" [ref=e8]
- button "Place Order ₹412" [ref=e12]
- link "Help" [ref=e13]"""
MMT_REVIEW = """- button "Continue" [ref=e3]
- button "Proceed to Pay ₹8,940" [ref=e9]"""


def act(tool, args, url="", page=""):
    d = R.decide(tool, args, {"url": url, "page": page})
    return d["action"] if d else "allow"


def test_food_cod_only_and_order_needs_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "HOME", tmp_path)
    url = "https://www.swiggy.com/checkout"
    assert act("browser_click", {"ref": "@e7"}, url, SWIGGY_CART) == "allow"      # choose COD
    assert act("browser_click", {"ref": "@e8"}, url, SWIGGY_CART) == "block"      # UPI
    assert act("browser_click", {"ref": "@e12"}, url, SWIGGY_CART) == "approve"   # place order -> owner YES
    assert act("browser_click", {"ref": "@e13"}, url, SWIGGY_CART) == "allow"
    assert act("computer_use", {"action": "left_click", "coordinate": [500, 600]}, url) == "approve"


def test_travel_never_books(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "HOME", tmp_path)
    url = "https://www.makemytrip.com/flight/review"
    assert act("browser_click", {"ref": "@e3"}, url, MMT_REVIEW) == "allow"
    assert act("browser_click", {"ref": "@e9"}, url, MMT_REVIEW) == "block"
    assert act("browser_click", {"ref": "@e3"}, "https://www.makemytrip.com/payment/abc", MMT_REVIEW) == "block"
    assert act("browser_navigate", {"url": "https://www.makemytrip.com/flights"}) == "allow"  # searching is fine


def test_payment_pages_and_card_details(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "HOME", tmp_path)
    assert act("browser_click", {"ref": "@e1"}, "https://api.razorpay.com/v1/checkout") == "block"
    assert act("browser_type", {"ref": "@e4", "text": "4111 1111 1111 1111"}, "https://example.com") == "block"
    assert act("browser_type", {"ref": "@e4", "text": "4111 1111 1111 1112"}, "https://example.com") == "allow"
    assert act("browser_type", {"ref": "@e5", "text": "482913"}, "https://x.com", '- textbox "Enter OTP" [ref=e5]') == "block"
    assert act("browser_type", {"ref": "@e6", "text": "Indiranagar"}, "https://www.zomato.com", '- textbox "Search" [ref=e6]') == "allow"


def test_secrets_tunnels_and_generic_buy(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "HOME", tmp_path)
    assert act("read_file", {"path": "/home/hermes/.hermes/.env"}) == "block"
    assert act("terminal", {"command": "cat ~/.hermes/whatsapp/session/creds.json"}) == "block"
    assert act("terminal", {"command": "pip install flask && python app.py"}) == "allow"
    assert act("terminal", {"command": "cloudflared tunnel --url http://localhost:5000"}) == "approve"
    assert act("browser_click", {"ref": "@e2"}, "https://shop.example.com", '- button "Buy Now" [ref=e2]') == "approve"


def test_stop_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "HOME", tmp_path)
    assert "Stopped" in R.pre_llm_call(user_message="stop")["context"]
    assert act("web_search", {"query": "pizza"}) == "block"
    assert R.pre_llm_call(user_message="Stop the music please") is None  # only the bare word
    assert "Resumed" in R.pre_llm_call(user_message="RESUME")["context"]
    assert act("web_search", {"query": "pizza"}) == "allow"


def test_hooks_track_page_and_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "HOME", tmp_path)
    R.pre_tool_call("browser_navigate", {"url": "https://www.swiggy.com/checkout"}, session_id="s9")
    R.post_tool_call("browser_snapshot", {}, SWIGGY_CART, session_id="s9")
    assert R.pre_tool_call("browser_click", {"ref": "@e12"}, session_id="s9")["action"] == "approve"
    assert R.pre_tool_call("browser_click", {"ref": "@e7"}, session_id="s9") is None
    log = (tmp_path / "audit.jsonl").read_text()
    assert '"directive": "approve"' in log and '"tool": "browser_snapshot"' in log
