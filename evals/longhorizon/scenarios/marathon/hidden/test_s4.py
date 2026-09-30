import subprocess
import sys

import invoicing
from conftest import customer


def run(path, code):
    """`code` in a fresh process that opened the store at `path`; returns its last line of output."""
    proc = subprocess.run([sys.executable, "-c", f"import invoicing\ninvoicing.open_store({str(path)!r})\n{code}"],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""


def test_everything_survives_a_restart(tmp_path):
    path = tmp_path / "invoicing.db"
    invoice = run(path, 'c = invoicing.add_customer("Asha", "asha@example.com")\n'
                        'p = invoicing.add_product("Tea", 250, tax_bp=0)\n'
                        'i = invoicing.create_invoice(c["id"], [(p["id"], 2)])\n'
                        'invoicing.issue_invoice(i["id"])\n'
                        'invoicing.record_payment(i["id"], 100, "upi")\n'
                        'print(i["id"])')
    out = run(path, f'i = invoicing.get_invoice({invoice!r})\n'
                    'print(i["status"], i["number"], len(invoicing.list_customers()), '
                    'len(invoicing.payments_for(i["id"])))')
    assert out == "partially_paid INV-0001 1 1"


def test_open_store_none_goes_back_to_memory_and_reset_empties_the_open_store(tmp_path):
    path = str(tmp_path / "invoicing.db")
    invoicing.open_store(path)
    customer()
    invoicing.open_store(None)
    assert invoicing.list_customers() == []
    invoicing.open_store(path)
    assert len(invoicing.list_customers()) == 1
    invoicing.reset()
    assert run(path, "print(len(invoicing.list_customers()))") == "0"
