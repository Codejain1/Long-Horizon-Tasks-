"""Real places near the owner (OpenStreetMap): a Hermes tool, kept out of the memory service. The owner's location
comes from memory (GET /current); the search itself is here."""

from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request


def _http_json(url: str, data: bytes | None = None, timeout: float = 20) -> dict:
    req = urllib.request.Request(url, data, {"User-Agent": "hermes-embed-memory/0.1 (personal assistant)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def km(a_lat, a_lng, b_lat, b_lng) -> float:
    p = math.pi / 180
    h = (math.sin((b_lat - a_lat) * p / 2) ** 2 +
         math.cos(a_lat * p) * math.cos(b_lat * p) * math.sin((b_lng - a_lng) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(h))


OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")

def _osm_places(loc: dict, radius_m: int, cuisine: str, fetch) -> list[dict]:
    """Eateries around loc as (name, lat, lng, tags). Overpass first; its public servers are often overloaded,
    so fall back to Nominatim's bounded search (same OpenStreetMap data, a few queries, 1 request/s policy)."""
    q = (f'[out:json][timeout:15];nwr(around:{radius_m},{loc["lat"]},{loc["lng"]})'
         f'["amenity"~"^(restaurant|cafe|fast_food|food_court|ice_cream)$"]["name"];out center 200;')
    for url in OVERPASS:
        try:
            els = fetch(url, urllib.parse.urlencode({"data": q}).encode(), timeout=15).get("elements", [])
            return [{"name": e["tags"]["name"], "lat": e.get("lat", e.get("center", {}).get("lat")),
                     "lng": e.get("lon", e.get("center", {}).get("lon")), "tags": e["tags"]}
                    for e in els if e.get("tags", {}).get("name")]
        except Exception:
            continue
    d = radius_m / 111_000  # degrees of latitude per metre (good enough for a box)
    box = f"{loc['lng'] - d},{loc['lat'] + d},{loc['lng'] + d},{loc['lat'] - d}"
    out = []
    for i, term in enumerate(([f"{cuisine} restaurant"] if cuisine else []) + ["restaurant", "cafe", "fast food"]):
        if i:
            time.sleep(1.1)
        try:
            rows = fetch("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                {"q": term, "format": "jsonv2", "limit": 50, "bounded": 1, "extratags": 1, "viewbox": box}))
        except Exception:
            continue
        for x in rows if isinstance(rows, list) else []:
            if x.get("name"):
                out.append({"name": x["name"], "lat": float(x["lat"]), "lng": float(x["lon"]),
                            "tags": {"amenity": x.get("type", ""), **(x.get("extratags") or {})}})
    return out

def nearby_food(loc: dict | None, radius_m: int = 2000, cuisine: str = "", limit: int = 25, fetch=None) -> dict:
    """Real eateries around the owner's latest location (OpenStreetMap), vegetarian-marked first, then nearest."""
    fetch = fetch or _http_json
    if not loc:
        return {"error": "no location shared yet: ask the owner to share it on WhatsApp (attach > Location)"}
    radius_m = max(300, min(int(radius_m), 5000))
    want = [w for w in re.split(r"[,/ ]+", cuisine.lower()) if w]
    places, seen = [], set()
    for p in _osm_places(loc, radius_m, cuisine, fetch):
        t, key = p["tags"], (p["name"].lower(), round(p["lat"] or 0, 4), round(p["lng"] or 0, 4))
        if p["lat"] is None or key in seen:
            continue
        seen.add(key)
        dist = km(loc["lat"], loc["lng"], p["lat"], p["lng"])
        kinds = f"{t.get('cuisine', '')} {p['name']}".lower()
        if dist * 1000 > radius_m * 1.5 or (want and not any(w in kinds for w in want)):
            continue
        places.append({"name": p["name"], "type": t.get("amenity", ""),
                       "cuisine": t.get("cuisine", "").replace(";", ", "),
                       "vegetarian": t.get("diet:vegetarian", ""), "km": round(dist, 2),
                       "opening_hours": t.get("opening_hours", ""),
                       "maps": "https://www.google.com/maps/search/?api=1&query="
                               + urllib.parse.quote(f"{p['name']} {p['lat']},{p['lng']}")})
    places.sort(key=lambda p: (p["vegetarian"] not in ("only", "yes"), p["km"]))
    return {"near": loc.get("area") or f"{loc['lat']},{loc['lng']}", "radius_m": radius_m, "count": len(places),
            "places": places[:limit]}
