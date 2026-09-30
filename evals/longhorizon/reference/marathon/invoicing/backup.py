"""Backups of the store as JSON (the format is in the README), and a YAML export for the ops team."""

from __future__ import annotations

import json
from pathlib import Path

from invoicing.errors import ValidationError
from invoicing.invoices import _balance, _totals
from invoicing.migrations.v1_to_v2 import upgrade
from invoicing.store import _complete, transaction

VERSION = 2


def export_backup(path: str) -> None:
    """Write the whole store to `path`."""
    with transaction() as db:
        Path(path).write_text(json.dumps({"version": VERSION, "tables": db}, indent=2))


def import_backup(path: str) -> None:
    """Replace the store with the backup at `path` (version 1 or 2)."""
    backup = json.loads(Path(path).read_text())
    if backup.get("version") == 1:
        tables = upgrade(backup)
    elif backup.get("version") == 2:
        tables = backup["tables"]
    else:
        raise ValidationError(f"unsupported backup version: {backup.get('version')!r}")
    with transaction() as db:
        db.clear()
        db.update(_complete(tables))


def export_yaml(path: str) -> None:
    """Write every invoice to `path` as a YAML list, oldest first."""
    with transaction() as db:
        out = []
        for invoice in sorted(db["invoices"].values(), key=lambda i: i["created_at"]):
            fields = [("id", invoice["id"]), ("number", invoice["number"]), ("customer_id", invoice["customer_id"]),
                      ("status", invoice["status"]), ("currency", invoice["currency"]),
                      ("total_cents", _totals(invoice)["total_cents"]), ("balance_due_cents", _balance(db, invoice))]
            for n, (key, value) in enumerate(fields):
                out.append(f"{'- ' if n == 0 else '  '}{key}: {_yaml(value)}")
        Path(path).write_text("\n".join(out) + "\n" if out else "[]\n")


def _yaml(value: object) -> str:
    if value is None:
        return "null"
    return str(value) if isinstance(value, int) else json.dumps(str(value))
