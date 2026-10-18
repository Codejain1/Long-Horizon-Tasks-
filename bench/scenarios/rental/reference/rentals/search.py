from typing import Optional

from rentals.store import PROPERTIES


def search_properties(city: Optional[str] = None, max_rent_cents: Optional[int] = None,
                      kind: Optional[str] = None) -> list:
    found = [dict(p) for p in PROPERTIES.values() if p["available"]
             and (city is None or p["city"].lower() == city.lower())
             and (max_rent_cents is None or p["rent_cents"] <= max_rent_cents)
             and (kind is None or p["kind"] == kind)]
    return sorted(found, key=lambda p: (p["rent_cents"], p["title"]))
