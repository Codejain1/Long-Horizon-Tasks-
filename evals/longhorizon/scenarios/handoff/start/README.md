# lending

The library's lending service: members and the book catalogue.

- **Standard library only.**
- **Money is integer paise** (100 paise = 1 rupee), never a float. Fields holding money end in `_cents`.
- **Ids** come from `uuid.uuid4().hex`.
- **Dates** are `datetime.date`s in UTC. `lending.today()` is today's date; tests pin it with `lending.set_today`.
- **Errors** raise `lending.LendingError`.
- **Data** lives in `lending/store.py`; `lending.reset()` empties it.
- Export every public function from the `lending` package. Run `./ci.sh` before you finish.
