from datetime import timedelta

from conftest import WHEN, event

import boxoffice


def test_events_at_a_venue_soonest_first():
    late = event(venue="Blue Hall", when=WHEN + timedelta(days=2))
    soon = event(venue="blue hall", when=WHEN)
    event(venue="Red Hall")
    assert [e["id"] for e in boxoffice.events_at("BLUE HALL")] == [soon, late]
