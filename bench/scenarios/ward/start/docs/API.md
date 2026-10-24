# WardSafe API

Everything `ward` exports, one line each.

- `add_patient(name, mrn, weight_g, allergies=())`: admit a patient; returns their id.
- `get_patient(patient_id)`: the patient as a dict.
- `add_drug(code, name, drug_class, max_daily_mcg)`: add a drug to the catalogue.
- `get_drug(code)`: the drug as a dict.
- `set_now(when)`, `now()`: the ward's clock (UTC).
- `reset()`: forget everything (tests).
- `WardError`: raised for anything refused.
