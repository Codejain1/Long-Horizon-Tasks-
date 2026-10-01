from datetime import date, timedelta

from rentals.agreements import AGREEMENTS, get_agreement, months_of
from rentals.clock import today
from rentals.errors import RentalError
from rentals.rules import deployable_cents, partner_rate_pct, shares_pct


def fund_deposit(agreement_id: str) -> dict:
    a = get_agreement(agreement_id)
    if a["funded_on"] is not None:
        raise RentalError("the deposit is already funded")
    deployed = deployable_cents(a["deposit_cents"], a["consent_to_deploy"])
    AGREEMENTS[agreement_id].update(funded_on=today(), deployed_cents=deployed,
                                    held_cents=a["deposit_cents"] - deployed)
    return {"held_cents": a["deposit_cents"] - deployed, "deployed_cents": deployed}


def yield_split(agreement_id: str, on: date) -> dict:
    a = get_agreement(agreement_id)
    if a["funded_on"] is None or on < a["funded_on"]:
        raise RentalError("the deposit wasn't funded by then")
    stop = min(on, a["ended_on"]) if a["ended_on"] else on
    days = (stop - a["funded_on"]).days
    rate_days = sum(partner_rate_pct(a["funded_on"] + timedelta(d)) for d in range(days))
    deployed = a["deployed_cents"]
    tenant_pct, owner_pct = shares_pct()
    total = deployed * rate_days // 36500
    tenant = deployed * tenant_pct * days // 36500
    owner = deployed * owner_pct * days // 36500
    return {"tenant_cents": tenant, "owner_cents": owner, "platform_cents": total - tenant - owner}


def pay_rent(agreement_id: str, month: date) -> None:
    a = get_agreement(agreement_id)
    if month not in months_of(a):
        raise RentalError("that month isn't in the agreement")
    if month in a["rent_paid"]:
        raise RentalError("that month is already paid")
    AGREEMENTS[agreement_id]["rent_paid"].append(month)


def rent_paid(agreement_id: str) -> list:
    return sorted(get_agreement(agreement_id)["rent_paid"])


def move_out(agreement_id: str, on: date, damages_cents: int = 0) -> dict:
    a = get_agreement(agreement_id)
    if a["ended_on"] is not None:
        raise RentalError("the tenant already moved out")
    if a["funded_on"] is None or on < a["funded_on"]:
        raise RentalError("the deposit wasn't funded by then")
    if not isinstance(damages_cents, int) or not 0 <= damages_cents <= a["deposit_cents"]:
        raise RentalError("damages must be between nothing and the deposit")
    shares = yield_split(agreement_id, on)
    AGREEMENTS[agreement_id]["ended_on"] = on
    return {"refund_cents": a["deposit_cents"] - damages_cents + shares["tenant_cents"],
            "owner_cents": damages_cents + shares["owner_cents"]}
