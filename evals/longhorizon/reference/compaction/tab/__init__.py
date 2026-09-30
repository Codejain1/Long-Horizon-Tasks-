"""Share expenses within a group and settle up. Money is integer cents; times are timezone-aware UTC."""

import csv
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal


def new_group(name, members):
    if len(set(members)) != len(members) or not members:
        raise ValueError("members must be distinct and non-empty")
    return {"name": name, "members": list(members), "expenses": []}


def add_expense(group, payer, amount_cents, participants, at):
    known = set(group["members"])
    if payer not in known or not participants or any(p not in known for p in participants):
        raise ValueError("unknown member")
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents <= 0:
        raise ValueError("amount_cents must be a positive integer")
    if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be a timezone-aware datetime")
    expense = {"payer": payer, "amount_cents": amount_cents, "participants": list(participants),
               "at": at.astimezone(timezone.utc)}
    group["expenses"].append(expense)
    return expense


def balances(group):
    net = {m: 0 for m in group["members"]}
    for e in group["expenses"]:
        share, left = divmod(e["amount_cents"], len(e["participants"]))
        net[e["payer"]] += e["amount_cents"]
        for i, p in enumerate(e["participants"]):
            net[p] -= share + (1 if i < left else 0)
    return net


def net_balances(group):
    return balances(group)


def settle_up(group):
    net = balances(group)
    order = {m: i for i, m in enumerate(group["members"])}
    transfers = []
    while True:
        debtors = [m for m in net if net[m] < 0]
        creditors = [m for m in net if net[m] > 0]
        if not debtors or not creditors:
            return transfers
        debtor = min(debtors, key=lambda m: (net[m], order[m]))
        creditor = min(creditors, key=lambda m: (-net[m], order[m]))
        cents = min(-net[debtor], net[creditor])
        transfers.append((debtor, creditor, cents))
        net[debtor] += cents
        net[creditor] -= cents


def average_expense(group):
    amounts = [e["amount_cents"] for e in group["expenses"]]
    if not amounts:
        return 0
    return (2 * sum(amounts) + len(amounts)) // (2 * len(amounts))


def _stamp(at):
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def export_csv(group, path):
    with open(path, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["payer", "amount_cents", "participants", "at"])
        for e in group["expenses"]:
            out.writerow([e["payer"], e["amount_cents"], ";".join(e["participants"]), _stamp(e["at"])])


def expenses_on(group, day=None):
    day = day or datetime.now(timezone.utc).date()
    if not isinstance(day, date):
        raise ValueError("day must be a date")
    return [e for e in group["expenses"] if e["at"].astimezone(timezone.utc).date() == day]


def in_currency(amount_cents, rate):
    value = Decimal(amount_cents) * Decimal(str(rate))
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
