import uuid
from datetime import date, timedelta
from typing import List, Optional

from lending.clock import today
from lending.errors import LendingError
from lending.rules import cap_fine
from lending.store import BOOKS, MEMBERS, table

LOANS = table()
RESERVATIONS = table()  # book id -> {"queue": [member ids]}
LOAN_DAYS = 14
MAX_LOANS = 3
MAX_RENEWALS = 2
FINE_PER_DAY_CENTS = 1000


def _active(book_id: str) -> Optional[dict]:
    return next((l for l in LOANS.values() if l["book"] == book_id and l["returned_on"] is None), None)


def _queue(book_id: str) -> List[str]:
    return RESERVATIONS.get(book_id, {}).get("queue", [])


def lend(book_id: str, member_id: str) -> str:
    if book_id not in BOOKS or member_id not in MEMBERS:
        raise LendingError("unknown book or member")
    if _active(book_id):
        raise LendingError("that book is on loan")
    if len(loans_of(member_id)) >= MAX_LOANS:
        raise LendingError(f"a member can hold at most {MAX_LOANS} books")
    queue = _queue(book_id)
    if queue and queue[0] != member_id:
        raise LendingError("that book is reserved for another member")
    if queue:
        queue.pop(0)
    loan_id = uuid.uuid4().hex
    LOANS[loan_id] = {"id": loan_id, "book": book_id, "member": member_id, "lent_on": today(),
                      "due": today() + timedelta(days=LOAN_DAYS), "returned_on": None, "renewals": 0}
    return loan_id


def _loan(loan_id: str) -> dict:
    if loan_id not in LOANS:
        raise LendingError(f"no loan {loan_id}")
    return LOANS[loan_id]


def return_book(loan_id: str) -> None:
    loan = _loan(loan_id)
    if loan["returned_on"] is not None:
        raise LendingError("already returned")
    loan["returned_on"] = today()


def get_loan(loan_id: str) -> dict:
    return dict(_loan(loan_id))


def loans_of(member_id: str) -> List[dict]:
    mine = [dict(l) for l in LOANS.values() if l["member"] == member_id and l["returned_on"] is None]
    return sorted(mine, key=lambda l: l["due"])


def reserve(book_id: str, member_id: str) -> None:
    if book_id not in BOOKS or member_id not in MEMBERS:
        raise LendingError("unknown book or member")
    queue = RESERVATIONS.setdefault(book_id, {"queue": []})["queue"]
    if member_id in queue:
        raise LendingError("already reserved")
    queue.append(member_id)


def renew(loan_id: str) -> date:
    loan = _loan(loan_id)
    if loan["returned_on"] is not None:
        raise LendingError("returned loans can't be renewed")
    if loan["renewals"] >= MAX_RENEWALS:
        raise LendingError(f"at most {MAX_RENEWALS} renewals")
    if _queue(loan["book"]):
        raise LendingError("someone has reserved that book")
    loan["renewals"] += 1
    loan["due"] = loan["due"] + timedelta(days=LOAN_DAYS)
    return loan["due"]


def fine_cents(loan_id: str) -> int:
    loan = _loan(loan_id)
    end = loan["returned_on"] or today()
    days = max(0, (end - loan["due"]).days)
    return cap_fine(days * FINE_PER_DAY_CENTS, BOOKS[loan["book"]]["replacement_cents"])
