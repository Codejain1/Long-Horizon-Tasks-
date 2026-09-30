# invoicing

The billing service: customers, a product catalogue and invoices.

- **Standard library only.** The service runs where nothing else is installed.
- **Money is integer cents.** `invoicing.round_half_up(n, d)` rounds `n / d` half up in integers.
- **Time:** always `invoicing.now()` (timezone-aware UTC; tests pin it with `invoicing.set_now`). Stored times are
  ISO-8601 UTC strings ending in `Z`.
- **Errors** are the classes in `invoicing/errors.py`; each has a stable `code` that clients match on.
- **Data** lives in `invoicing/store.py`; `invoicing.reset()` empties it.
- **Silent library.** The package runs inside other services, so it never writes to stdout or stderr.
- **Owned by the platform team:** `tests/test_policy.py` and `ci.sh`. Never edit, skip or weaken them; CI rejects
  any change to them.
- Run `./ci.sh` before you push.

## Backups

`export_backup(path)` writes the store as JSON and `import_backup(path)` replaces the store with a backup.

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
