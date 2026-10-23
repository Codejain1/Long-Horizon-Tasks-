"""Travel search. Trains: ConfirmTkt's own data API (the calls its website makes; plain HTTP, ~0.5 s, no browser),
checked live 2026-10-23. IRCTC and redbus answer the server with Access Denied, so they're not used.

Each class comes with its fare, seats ("AVL 12", "WL 3", "Regret"), ConfirmTkt's chance of confirmation and the time
that availability was last seen (it's a cache: shown to the owner, never presented as live).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import date

API = "https://cttrainsapi.confirmtkt.com/api/"
HEADERS = {"ApiKey": "ct-web!2$", "ClientId": "ct-web", "Content-Type": "application/json",
           "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/151.0"}  # what confirmtkt.com itself sends
CLASSES = {"sl": "SL", "sleeper": "SL", "3a": "3A", "3ac": "3A", "third ac": "3A", "ac 3 tier": "3A", "3e": "3E",
           "2a": "2A", "2ac": "2A", "second ac": "2A", "ac 2 tier": "2A", "1a": "1A", "1ac": "1A", "first ac": "1A",
           "cc": "CC", "chair car": "CC", "ac chair car": "CC", "ec": "EC", "executive": "EC", "2s": "2S",
           "second sitting": "2S", "general": "2S"}
GONE = re.compile(r"regret|not available|no more booking|departed|cancel", re.I)


def _get(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=15) as r:
        return json.loads(r.read())


def train_class(said: str | None) -> str | None:
    """'sleeper' / '3A' / 'third AC' -> ConfirmTkt's class code; None when not said or not a class."""
    if not said:
        return None
    s = str(said).strip().lower()
    return CLASSES.get(s) or (s.upper() if s.upper() in set(CLASSES.values()) else None)


def station(name: str, get=_get) -> dict | None:
    """A city or station name -> its main station {"code", "name", "city"} (the city's own first, e.g. Bangalore ->
    KSR Bengaluru, not a suburb)."""
    if not name:
        return None
    if re.fullmatch(r"[A-Z]{2,5}", name.strip()):  # already a station code
        return {"code": name.strip(), "name": name.strip(), "city": ""}
    q = urllib.parse.quote(name.strip())
    got = get(f"{API}v2/trains/stations/auto-suggestion?searchString={q}&sourceStnCode=&popularStnListLimit=15"
              "&preferredStnListLimit=6&channel=mwebd&language=EN")
    stations = (got.get("data") or {}).get("stationList") or []
    if not stations:
        return None
    s = stations[0]
    return {"code": s["stationCode"], "name": s["stationName"], "city": s.get("city") or ""}


def trains(frm: str, to: str, day: date, get=_get) -> list[dict]:
    """Every train between two station codes on a day, with each class's fare, seats, chance and when it was seen."""
    got = get(f"{API}v1/trains/search?sourceStationCode={frm}&destinationStationCode={to}"
              f"&dateOfJourney={day:%d-%m-%Y}&addAvailabilityCache=true&quota=GN")
    out = []
    for t in (got.get("data") or {}).get("trainList") or []:
        classes = {}
        for cls, a in (t.get("availabilityCache") or {}).items():
            try:
                fare = float(a.get("fare") or 0)
            except ValueError:
                fare = 0
            if fare:
                classes[cls] = {"fare": fare, "status": a.get("availabilityDisplayName") or a.get("availability") or "",
                                "chance": a.get("predictionPercentage"), "as_of": (a.get("cacheTime") or "")[:16]}
        out.append({"number": t.get("trainNumber"), "name": (t.get("trainName") or "").title(),
                    "dep": t.get("departureTime"), "arr": t.get("arrivalTime"), "minutes": t.get("duration"),
                    "from": t.get("fromStnCode"), "to": t.get("toStnCode"), "classes": classes})
    return out


def best(found: list[dict], cls: str | None, max_price=None, people: int = 1, n: int = 3) -> list[dict]:
    """Code, not the model: bookable seats only (no Regret / Not Available), in the class asked (else the cheapest
    bookable class of each train), within budget for everyone, surest first (available before waitlist, higher
    chance), then shortest, then cheapest. One option per train."""
    opts = []
    for t in found:
        pool = {cls: t["classes"][cls]} if cls and cls in t["classes"] else ({} if cls else t["classes"])
        ok = [(c, a) for c, a in pool.items() if not GONE.search(a["status"])
              and (max_price is None or a["fare"] * people <= float(max_price))]
        if not ok:
            continue
        c, a = min(ok, key=lambda x: x[1]["fare"])
        sure = 2 if a["status"].upper().startswith("AVL") else (1 if a["status"].upper().startswith("RAC") else 0)
        opts.append({**{k: t[k] for k in ("number", "name", "dep", "arr", "minutes", "from", "to")},
                     "class": c, "fare": a["fare"], "total": a["fare"] * people, "status": a["status"],
                     "chance": a["chance"], "as_of": a["as_of"], "sure": sure})
    opts.sort(key=lambda o: (-o["sure"], -(o["chance"] or 0), o["minutes"] or 9999, o["fare"]))
    return opts[:n]


def link(frm: str, to: str, day: date) -> str:
    return f"https://www.confirmtkt.com/rbooking-d/trains/from/{frm}/to/{to}/{day:%d-%m-%Y}"
