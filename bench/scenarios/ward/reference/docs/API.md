# WardSafe API

Everything `ward` exports, one line each.

- `add_patient(name, mrn, weight_g, allergies=())`: admit a patient; returns their id.
- `get_patient(patient_id)`: the patient as a dict.
- `add_drug(code, name, drug_class, max_daily_mcg)`: add a drug to the catalogue.
- `get_drug(code)`: the drug as a dict.
- `set_now(when)`, `now()`: the ward's clock (UTC).
- `reset()`: forget everything (tests).
- `WardError`: raised for anything refused.
- `prescribe(patient_id, drug_code, dose_mcg, every_hours, prescriber)`, `get_order(order_id)`, `orders_for(patient_id)`, `stop_order(order_id)`, `patient_drugs(patient_id)`: prescriptions.
- `add_nurse(name, badge)`: register a nurse.
- `administer(order_id, nurse_id, witness=None)`, `administer_batch(items)`, `administrations(order_id)`, `correct_administration(event_id, reason)`, `due_doses()`, `max_dose_now(order_id)`: doses.
- `record_egfr(patient_id, value)`, `latest_egfr(patient_id)`, `update_weight(patient_id, weight_g)`, `audit_log(patient_id)`: readings and the audit trail.
- `daily_report(day)`, `export_csv(path)`: reports.
