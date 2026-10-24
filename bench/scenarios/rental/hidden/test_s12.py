from datetime import date

from conftest import funded, home, tenant

import rentals

YEAR_END = date(2027, 1, 1)


def _two_agreements():
    with_consent = funded()
    without = funded(home(title="Other"), consent=False, who=tenant("Kiran", "9000000001"))
    return with_consent, without


def test_the_report_adds_up_the_live_deposits():
    a, b = _two_agreements()
    report = rentals.platform_report(YEAR_END)
    deposits = sum(rentals.get_agreement(x)["deposit_cents"] for x in (a, b))
    assert report["deposits_cents"] == deposits == report["held_cents"] + report["deployed_cents"]
    assert report["earnings_cents"] == sum(rentals.yield_split(x, YEAR_END)["platform_cents"] for x in (a, b))
    rentals.move_out(b, date(2026, 6, 1))
    assert rentals.platform_report(YEAR_END)["deposits_cents"] == rentals.get_agreement(a)["deposit_cents"]


def test_platform_earnings_follow_the_reserve_consent_and_rates():
    """Tickets 1-3's rules and ticket 9's rate change; ticket 12 repeats none of them."""
    _two_agreements()
    assert rentals.platform_report(YEAR_END) == {"deposits_cents": 20000000, "held_cents": 12500000,
                                                 "deployed_cents": 7500000, "earnings_cents": 356506}
