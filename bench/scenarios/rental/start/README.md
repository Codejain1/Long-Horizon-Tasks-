# rentals

RentEase: a no-brokerage rental platform. Owners list properties, tenants find and rent them.

- **Standard library only.**
- **Money is integer paise** (100 paise = 1 rupee), never a float. Fields holding money end in `_cents`.
- **Ids** come from `uuid.uuid4().hex`.
- **Dates** are `datetime.date`s in IST. `rentals.today()` is today's date; tests pin it with `rentals.set_today`.
- **States** are two-letter codes (`KA`, `MH`, `UP`, ...). `rentals.MTA_STATES` lists the states that adopted the Model Tenancy Act.
- **Errors** raise `rentals.RentalError`.
- **Data** lives in `rentals/store.py`; `rentals.reset()` empties it.
- Export every public function from the `rentals` package. Run `./ci.sh` before you finish.
