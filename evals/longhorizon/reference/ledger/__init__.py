import json

RATES = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}


def new_ledger():
    return {"expenses": [], "budgets": {}}


def add_expense(ledger, amount_cents, category, note="", currency="USD"):
    ledger["expenses"].append({"amount": int(amount_cents), "category": category, "note": note, "currency": currency})


def _usd(e):
    return round(e["amount"] * RATES[e["currency"]])


def total(ledger, category=None, currency="USD"):
    usd = sum(_usd(e) for e in ledger["expenses"] if category is None or e["category"] == category)
    return int(round(usd / RATES[currency]))


def save(ledger, path):
    with open(path, "w") as f:
        json.dump(ledger, f)


def load(path):
    with open(path) as f:
        return json.load(f)


def set_budget(ledger, category, amount_cents):
    ledger["budgets"][category] = int(amount_cents)


def over_budget(ledger):
    return sorted(c for c, b in ledger["budgets"].items() if total(ledger, c) > b)
