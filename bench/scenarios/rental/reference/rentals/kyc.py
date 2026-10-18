import re

from rentals.errors import RentalError
from rentals.rules import masked_aadhaar, masked_pan
from rentals.store import TENANTS

PAN = re.compile(r"[A-Z]{5}\d{4}[A-Z]")
AADHAAR = re.compile(r"[2-9]\d{11}")


def verify_tenant(tenant_id: str, pan: str, aadhaar: str) -> None:
    if tenant_id not in TENANTS:
        raise RentalError(f"no tenant {tenant_id}")
    pan, aadhaar = pan.strip().upper(), aadhaar.replace(" ", "")
    if not PAN.fullmatch(pan) or not AADHAAR.fullmatch(aadhaar):
        raise RentalError("a PAN looks like ABCDE1234F and an Aadhaar number has 12 digits")
    TENANTS[tenant_id].update(pan=masked_pan(pan), aadhaar=masked_aadhaar(aadhaar), verified=True)


def is_verified(tenant_id: str) -> bool:
    return bool(TENANTS.get(tenant_id, {}).get("verified"))
