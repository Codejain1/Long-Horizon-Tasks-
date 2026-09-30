import json
import re

import invoicing
from conftest import draft, issued, pin


def parse(text):
    """The YAML the ticket asks for: a list of flat mappings; strings double-quoted, numbers bare, null."""
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.strip() == "[]":
            continue
        match = re.fullmatch(r"(- |  )(\w+): (.*)", line)
        assert match, f"unexpected line: {line!r}"
        if match[1] == "- ":
            rows.append({})
        raw = match[3].strip()
        rows[-1][match[2]] = None if raw == "null" else json.loads(raw)
    return rows


def test_invoices_export_as_yaml(tmp_path):
    first = issued(10000, when="2026-03-01T10:00:00Z")
    invoicing.record_payment(first, 2500, "upi")
    second = issued(700, when="2026-03-02T10:00:00Z")
    pin("2026-03-03T10:00:00Z")
    third = draft(300)
    path = tmp_path / "invoices.yaml"
    invoicing.export_yaml(str(path))
    rows = parse(path.read_text())
    customer_of = {i: invoicing.get_invoice(i)["customer_id"] for i in (first, second, third)}
    assert rows[:2] == [
        {"id": first, "number": "INV-0001", "customer_id": customer_of[first], "status": "partially_paid",
         "currency": "INR", "total_cents": 10000, "balance_due_cents": 7500},
        {"id": second, "number": "INV-0002", "customer_id": customer_of[second], "status": "issued",
         "currency": "INR", "total_cents": 700, "balance_due_cents": 700},
    ]
    assert len(rows) == 3 and (rows[2]["id"], rows[2]["number"], rows[2]["status"]) == (third, None, "draft")
