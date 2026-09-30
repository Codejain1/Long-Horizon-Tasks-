# invoicing

The billing service: customers, a product catalogue and invoices.

- **Standard library only.** The service runs where nothing else is installed.
- **Money is integer cents.** `invoicing.round_half_up(n, d)` rounds `n / d` half up in integers.
- **Time:** always `invoicing.now()` (timezone-aware UTC; tests pin it with `invoicing.set_now`). Stored times are
  ISO-8601 UTC strings ending in `Z`.
- **Errors** are the classes in `invoicing/errors.py`; each has a stable `code` that clients match on.
- **Data** lives in `invoicing/store.py`: in memory, or in a file shared by several processes (`open_store`).
  `invoicing.reset()` empties it.
- Run `./ci.sh` before you push.

## Backups

`export_backup(path)` writes the store as JSON and `import_backup(path)` replaces the store with a backup.

Format version 2 is the whole store: `{"version": 2, "tables": {<table>: {<id>: <record>}}}`.
Version-1 files still import (`invoicing/migrations/v1_to_v2.py`).

Format version 1:

```json
{
  "version": 1,
  "customers": [{"id": "…", "name": "…", "email": "…", "created_at": "2026-01-01T00:00:00Z"}],
  "products": [{"id": "…", "name": "…", "price_cents": 250}],
  "invoices": [{"id": "…", "customer": "<customer id>", "status": "draft", "created_at": "…",
                "lines": [{"product": "<product id>", "qty": 2, "unit_cents": 250}]}]
}
```
