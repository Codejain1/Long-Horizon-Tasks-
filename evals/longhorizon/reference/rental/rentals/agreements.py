import calendar
import uuid
from datetime import date, timedelta

from rentals.errors import RentalError
from rentals.offers import get_offer, move_in_quote
from rentals.properties import get_property
from rentals.rules import max_term_months
from rentals.store import table

AGREEMENTS = table()


def _end(start: date, months: int) -> date:
    y, m = divmod(start.month - 1 + months - 1, 12)
    year, month = start.year + y, m + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def create_agreement(offer_id: str, start: date, months: int, consent_to_deploy: bool = False) -> str:
    offer = get_offer(offer_id)
    quote = move_in_quote(offer_id)
    if start.day != 1:
        raise RentalError("agreements start on the 1st of a month")
    if not isinstance(months, int) or not 1 <= months <= max_term_months():
        raise RentalError("that term isn't allowed")
    if any(a["offer"] == offer_id for a in AGREEMENTS.values()):
        raise RentalError("that offer already has an agreement")
    agreement_id = uuid.uuid4().hex
    AGREEMENTS[agreement_id] = {
        "id": agreement_id, "offer": offer_id, "property": offer["property"], "tenant": offer["tenant"],
        "owner": get_property(offer["property"])["owner"], "rent_cents": quote["rent_cents"],
        "deposit_cents": quote["deposit_cents"], "start": start, "end": _end(start, months), "months": months,
        "consent_to_deploy": bool(consent_to_deploy), "funded_on": None, "held_cents": 0, "deployed_cents": 0,
        "rent_paid": [], "ended_on": None}
    return agreement_id


def get_agreement(agreement_id: str) -> dict:
    if agreement_id not in AGREEMENTS:
        raise RentalError(f"no agreement {agreement_id}")
    a = dict(AGREEMENTS[agreement_id])
    a["rent_paid"] = list(a["rent_paid"])
    return a


def months_of(agreement: dict) -> list:
    out, day = [], agreement["start"]
    while day <= agreement["end"]:
        out.append(day)
        day = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out
