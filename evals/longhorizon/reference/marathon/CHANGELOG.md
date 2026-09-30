# Changelog

Every public function is listed here.

## 2.0

- `add_product` takes a GST rate; `invoice_total`
- `issue_invoice`, `record_payment`, `payments_for`
- `open_store`, `transaction`
- `overdue_invoices`, `void_invoice`, `credit_note`, `balance_due`, `customer_statement`, `apply_discount`
- `set_fx_rate`, `total_in_inr`, `rate_in_ten_thousandths`, `payments_report`
- Backups: format version 2; `upgrade` reads version 1
- `search_invoices`, `apply_late_fees`, `create_schedule`, `run_schedules`
- `refund_payment`, `refunds_for`, `export_yaml`, `audit_log`, `gst_summary`, `merge_customers`, `due_reminders`,
  `write_off`, `aging_report`

## 1.0

- `add_customer`, `get_customer`, `list_customers`
- `add_product`, `get_product`
- `create_invoice`, `get_invoice`, `list_invoices`
- `export_backup`, `import_backup`
- `now`, `set_now`, `iso`
- `round_half_up`, `is_cents`
- `data`, `reset`
