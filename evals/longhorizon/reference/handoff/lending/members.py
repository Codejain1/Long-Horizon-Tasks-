import uuid

from lending.errors import LendingError
from lending.store import MEMBERS


def add_member(name: str, email: str) -> str:
    if not name.strip() or "@" not in email:
        raise LendingError("a member needs a name and an email address")
    member_id = uuid.uuid4().hex
    MEMBERS[member_id] = {"id": member_id, "name": name, "email": email}
    return member_id


def get_member(member_id: str) -> dict:
    if member_id not in MEMBERS:
        raise LendingError(f"no member {member_id}")
    return dict(MEMBERS[member_id])
