"""In-memory storage."""

PATIENTS = {}
DRUGS = {}


def reset():
    """Forget everything (for tests)."""
    PATIENTS.clear()
    DRUGS.clear()
