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
    name = s["stationName"]
    if "all stations" in name.lower():  # a city's group of stations (live: "Bengaluru - All stations")
        name = s.get("city") or name.split(" - ")[0]
    return {"code": s["stationCode"], "name": name, "city": s.get("city") or ""}


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


# Buses: abhibus's own data API (plain HTTP, ~0.2 s; checked live 2026-10-23). redbus answers the server with
# Access Denied, so it's not used.
BUS_API = "https://www.abhibus.com/"
BUS_HEADERS = {"content-type": "application/json", "x-app-name": "nextgenweb", "User-Agent": HEADERS["User-Agent"]}
WINDOWS = {"morning": (4, 12), "afternoon": (12, 17), "evening": (17, 22), "night": (18, 28), "overnight": (18, 28)}


def _bus_get(url: str, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data, BUS_HEADERS), timeout=15) as r:
        return json.loads(r.read())


def bus_city(name: str, get=_bus_get) -> dict | None:
    """A city name -> abhibus's city {"id", "name"} (its own first suggestion: Bangalore -> 7, Mysore -> 926)."""
    if not name:
        return None
    got = get(f"{BUS_API}wap/abus-autocompleter/api/v1/results?s={urllib.parse.quote(name.strip())}")
    if not isinstance(got, list) or not got:
        return None
    return {"id": got[0]["id"], "name": got[0]["label"]}


def buses(src: dict, dst: dict, day: date, get=_bus_get) -> list[dict]:
    got = get(f"{BUS_API}buslist/v3/services", {"source": src["name"], "sourceid": src["id"], "destination": dst["name"],
                                                "destinationid": dst["id"], "jdate": f"{day:%Y-%m-%d}", "prd": "mobile",
                                                "filters": "1", "isReturnJourney": "0", "version": "114"})
    out = []
    for s in got.get("services") or []:
        t = s.get("timings") or {}
        h, m, _ = (str(t.get("travelTime") or "0:0:0").split(":") + ["0", "0"])[:3]
        out.append({"operator": s.get("travelerAgentName", ""), "type": s.get("busTypeName", ""),
                    "dep": t.get("startTimeTwfFormat", ""), "arr": t.get("arriveTimeTwfFormat", ""),
                    "minutes": int(h or 0) * 60 + int(m or 0), "fare": float((s.get("fares") or {}).get("fare") or 0),
                    "seats": int((s.get("seatStats") or {}).get("availableSeats") or 0),
                    "rating": s.get("rating"), "ratings": int(s.get("noOfRatings") or 0)})
    return out


def best_buses(found: list[dict], seat: str | None = None, window: str | None = None, max_price=None,
               people: int = 1, n: int = 3) -> list[dict]:
    """Code: enough seats for everyone, the seat kind and time of day asked, within budget, no poorly rated bus
    (under 3.5 from 10+ ratings); then cheapest, then quickest."""
    seat = (seat or "").lower()
    lo, hi = WINDOWS.get((window or "").lower().strip(), (0, 28))
    ok = []
    for b in found:
        kind = b["type"].lower()
        if b["seats"] < people or not b["fare"] or (max_price and b["fare"] * people > float(max_price)):
            continue
        berth = "sleeper" in kind.replace("semi sleeper", "").replace("semi-sleeper", "")  # live: a reclining seat
        if "sleeper" in seat and not berth or "seater" in seat and "seater" not in kind:
            continue
        if re.search(r"\bnon[- ]?ac\b", seat) and not re.search(r"non[- ]?a/?c", kind):
            continue
        if re.search(r"(?<!non )\bac\b", seat) and (re.search(r"non[- ]?a/?c", kind) or not re.search(r"\ba/?c\b", kind)):
            continue
        hour = int(b["dep"][:2] or 0) if b["dep"][:2].isdigit() else 0
        if not (lo <= hour < hi or lo <= hour + 24 < hi):
            continue
        if b["rating"] is not None and b["ratings"] >= 10 and float(b["rating"]) < 3.5:
            continue
        ok.append({**b, "total": b["fare"] * people})
    return sorted(ok, key=lambda b: (b["fare"], b["minutes"]))[:n]


def bus_link(src: dict, dst: dict, day: date) -> str:
    return f"https://www.abhibus.com/bus_search/{src['name']}/{src['id']}/{dst['name']}/{dst['id']}/{day:%d-%m-%Y}/O"
