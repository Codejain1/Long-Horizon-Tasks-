"""Backups of the store as JSON (the format is in the README)."""

from __future__ import annotations

import json
from pathlib import Path

from invoicing.errors import ValidationError
from invoicing.store import data

VERSION = 1


def export_backup(path: str) -> None:
    """Write the whole store to `path`."""
    tables = data()
    backup = {
        "version": VERSION,
        "customers": list(tables["customers"].values()),
        "products": list(tables["products"].values()),
        "invoices": [{"id": i["id"], "customer": i["customer_id"], "status": i["status"],
                      "created_at": i["created_at"],
                      "lines": [{"product": line["product_id"], "qty": line["qty"], "unit_cents": line["unit_cents"]}
                                for line in i["lines"]]}
                     for i in tables["invoices"].values()],
    }
    Path(path).write_text(json.dumps(backup, indent=2))


def import_backup(path: str) -> None:
    """Replace the store with the backup at `path`."""
    backup = json.loads(Path(path).read_text())
    if backup.get("version") != VERSION:
        raise ValidationError(f"unsupported backup version: {backup.get('version')!r}")
    tables = data()
    tables["customers"] = {c["id"]: dict(c) for c in backup["customers"]}
    tables["products"] = {p["id"]: dict(p) for p in backup["products"]}
    tables["invoices"] = {
        i["id"]: {"id": i["id"], "customer_id": i["customer"], "status": i["status"], "created_at": i["created_at"],
                  "lines": [{"product_id": line["product"], "qty": line["qty"], "unit_cents": line["unit_cents"]}
                            for line in i["lines"]]}
        for i in backup["invoices"]}
