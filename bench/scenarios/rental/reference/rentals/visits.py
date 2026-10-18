import uuid
from datetime import datetime

from rentals.errors import RentalError
from rentals.kyc import is_verified
from rentals.properties import get_property
from rentals.store import table

VISITS = table()


def schedule_visit(property_id: str, tenant_id: str, when: datetime) -> str:
    get_property(property_id)
    if not is_verified(tenant_id):
        raise RentalError("only verified tenants can visit")
    hour = when.replace(minute=0, second=0, microsecond=0)
    if any(v["property"] == property_id and v["when"].replace(minute=0, second=0, microsecond=0) == hour
           for v in VISITS.values()):
        raise RentalError("that hour is already booked")
    visit_id = uuid.uuid4().hex
    VISITS[visit_id] = {"id": visit_id, "property": property_id, "tenant": tenant_id, "when": when}
    return visit_id


def visits_for(property_id: str) -> list:
    return sorted((dict(v) for v in VISITS.values() if v["property"] == property_id), key=lambda v: v["when"])
