import uuid

from rentals.errors import RentalError
from rentals.kyc import is_verified
from rentals.properties import get_property
from rentals.rules import deposit_months
from rentals.store import PROPERTIES, table

OFFERS = table()


def make_offer(property_id: str, tenant_id: str, rent_cents: int) -> str:
    if not get_property(property_id)["available"]:
        raise RentalError("that property is taken")
    if not is_verified(tenant_id):
        raise RentalError("only verified tenants can make offers")
    if not isinstance(rent_cents, int) or rent_cents <= 0:
        raise RentalError("the rent must be a positive number of paise")
    offer_id = uuid.uuid4().hex
    OFFERS[offer_id] = {"id": offer_id, "property": property_id, "tenant": tenant_id, "rent_cents": rent_cents,
                        "status": "open"}
    return offer_id


def get_offer(offer_id: str) -> dict:
    if offer_id not in OFFERS:
        raise RentalError(f"no offer {offer_id}")
    return dict(OFFERS[offer_id])


def accept_offer(offer_id: str) -> None:
    offer = get_offer(offer_id)
    if offer["status"] != "open" or not PROPERTIES[offer["property"]]["available"]:
        raise RentalError("only an open offer on an available property can be accepted")
    for other in OFFERS.values():
        if other["property"] == offer["property"] and other["status"] == "open":
            other["status"] = "rejected"
    OFFERS[offer_id]["status"] = "accepted"
    PROPERTIES[offer["property"]]["available"] = False


def move_in_quote(offer_id: str) -> dict:
    offer = get_offer(offer_id)
    if offer["status"] != "accepted":
        raise RentalError("only an accepted offer has a move-in quote")
    prop = get_property(offer["property"])
    rent = offer["rent_cents"]
    deposit = rent * deposit_months(prop["state"], prop["kind"])
    return {"rent_cents": rent, "deposit_cents": deposit, "total_cents": rent + deposit}
