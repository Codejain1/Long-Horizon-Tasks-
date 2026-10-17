import re
import uuid

from rentals.errors import RentalError
from rentals.store import OWNERS, TENANTS

PHONE = re.compile(r"[6-9]\d{9}")  # an Indian mobile number, without +91


def _person(table: dict, name: str, phone: str) -> str:
    if not name.strip() or not PHONE.fullmatch(phone):
        raise RentalError("a person needs a name and a 10-digit mobile number")
    person_id = uuid.uuid4().hex
    table[person_id] = {"id": person_id, "name": name, "phone": phone}
    return person_id


def add_owner(name: str, phone: str) -> str:
    return _person(OWNERS, name, phone)


def add_tenant(name: str, phone: str) -> str:
    return _person(TENANTS, name, phone)


def get_owner(owner_id: str) -> dict:
    if owner_id not in OWNERS:
        raise RentalError(f"no owner {owner_id}")
    return dict(OWNERS[owner_id])


def get_tenant(tenant_id: str) -> dict:
    if tenant_id not in TENANTS:
        raise RentalError(f"no tenant {tenant_id}")
    return dict(TENANTS[tenant_id])
