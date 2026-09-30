import subprocess
import sys

import invoicing
from conftest import customer, product

WORKER = """
import sys
import invoicing
invoicing.open_store(sys.argv[1])
for _ in range(25):
    invoicing.record_payment(sys.argv[2], 100, "upi")
"""
CHECK = """
import sys
import invoicing
invoicing.open_store(sys.argv[1])
payments = invoicing.payments_for(sys.argv[2])
print(len(payments), sum(p["amount_cents"] for p in payments), invoicing.balance_due(sys.argv[2]),
      invoicing.get_invoice(sys.argv[2])["status"])
"""


def test_eight_processes_record_payments_without_losing_any(tmp_path):
    path = str(tmp_path / "invoicing.db")
    invoicing.open_store(path)
    invoice = invoicing.create_invoice(customer(), [(product(100000), 1)])["id"]
    invoicing.issue_invoice(invoice)
    workers = [subprocess.Popen([sys.executable, "-c", WORKER, path, invoice], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True) for _ in range(8)]
    errors = [w.communicate(timeout=300)[1] for w in workers]
    assert all(w.returncode == 0 for w in workers), [e[-1000:] for e in errors if e]
    check = subprocess.run([sys.executable, "-c", CHECK, path, invoice], capture_output=True, text=True, timeout=120)
    assert check.returncode == 0, check.stderr[-2000:]
    assert check.stdout.split() == ["200", "20000", "80000", "partially_paid"]
