import csv
from datetime import date

from rentals.agreements import AGREEMENTS
from rentals.clock import today
from rentals.money import rent_paid, yield_split
from rentals.people import get_owner, get_tenant
from rentals.properties import get_property
from rentals.rules import masked_phone


def owner_statement_csv(owner_id: str, path: str) -> None:
    get_owner(owner_id)
    rows = sorted((a for a in AGREEMENTS.values() if a["owner"] == owner_id),
                  key=lambda a: (a["start"], get_property(a["property"])["title"]))
    with open(path, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["agreement_id", "property", "tenant_name", "tenant_phone", "rent_paid_cents", "yield_cents"])
        for a in rows:
            tenant = get_tenant(a["tenant"])
            earned = yield_split(a["id"], today())["owner_cents"] if a["funded_on"] else 0
            out.writerow([a["id"], get_property(a["property"])["title"], tenant["name"],
                          masked_phone(tenant["phone"]), len(rent_paid(a["id"])) * a["rent_cents"], earned])


def platform_report(on: date) -> dict:
    funded = [a for a in AGREEMENTS.values() if a["funded_on"] and a["funded_on"] <= on]
    live = [a for a in funded if not a["ended_on"] or a["ended_on"] > on]
    return {"deposits_cents": sum(a["deposit_cents"] for a in live),
            "held_cents": sum(a["held_cents"] for a in live),
            "deployed_cents": sum(a["deployed_cents"] for a in live),
            "earnings_cents": sum(yield_split(a["id"], on)["platform_cents"] for a in funded)}
