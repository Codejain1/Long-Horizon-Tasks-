import uuid

from rentals.errors import RentalError
from rentals.people import get_owner
from rentals.store import PROPERTIES

KINDS = ("residential", "commercial")


def add_property(owner_id: str, title: str, city: str, state: str, kind: str, rent_cents: int) -> str:
    """`rent_cents`: the monthly rent the owner asks, in paise."""
    get_owner(owner_id)
    if not title.strip() or not city.strip() or len(state) != 2 or kind not in KINDS:
        raise RentalError("a property needs a title, a city, a two-letter state and a kind")
    if not isinstance(rent_cents, int) or rent_cents <= 0:
        raise RentalError("the rent must be a positive number of paise")
    property_id = uuid.uuid4().hex
    PROPERTIES[property_id] = {"id": property_id, "owner": owner_id, "title": title, "city": city,
                               "state": state.upper(), "kind": kind, "rent_cents": rent_cents, "available": True}
    return property_id


def get_property(property_id: str) -> dict:
    if property_id not in PROPERTIES:
        raise RentalError(f"no property {property_id}")
    return dict(PROPERTIES[property_id])
