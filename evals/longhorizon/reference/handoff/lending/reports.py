import csv
from typing import List

from lending.clock import today
from lending.loans import LOANS
from lending.rules import export_date, masked
from lending.store import BOOKS, MEMBERS


def overdue_report() -> List[dict]:
    rows = []
    for loan in LOANS.values():
        late = (today() - loan["due"]).days
        if loan["returned_on"] is None and late > 0:
            member = MEMBERS[loan["member"]]
            rows.append({"title": BOOKS[loan["book"]]["title"], "member_name": member["name"],
                         "email": masked(member["email"]), "due": loan["due"], "days_overdue": late})
    return sorted(rows, key=lambda r: -r["days_overdue"])


def export_loans_csv(path: str) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["loan_id", "title", "member_email", "lent_on", "due", "returned_on"])
        for loan in sorted(LOANS.values(), key=lambda l: (l["lent_on"], l["id"])):
            w.writerow([loan["id"], BOOKS[loan["book"]]["title"], masked(MEMBERS[loan["member"]]["email"]),
                        export_date(loan["lent_on"]), export_date(loan["due"]),
                        export_date(loan["returned_on"]) if loan["returned_on"] else ""])
