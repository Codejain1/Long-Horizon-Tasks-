"""In-memory storage."""

PATIENTS = {}
DRUGS = {}
NURSES = {}
ORDERS = {}
EVENTS = []  # the audit trail: append-only


def reset():
    """Forget everything (for tests)."""
    for table in (PATIENTS, DRUGS, NURSES, ORDERS):
        table.clear()
    EVENTS.clear()
