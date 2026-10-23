"""Train search from ConfirmTkt's data API: the shapes seen live on 2026-10-23, picked by code."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))
import travel as T  # noqa: E402

LIVE = {"data": {"errorMessage": None, "trainList": [  # trimmed from the real SBC -> MYS answer for 25 Oct 2026
    {"trainNumber": "22817", "trainName": "SHM MYS EXP", "departureTime": "00:45", "arrivalTime": "03:35",
     "duration": 170, "fromStnCode": "SBC", "toStnCode": "MYS", "availabilityCache": {
         "2A": {"fare": "770", "availabilityDisplayName": "Regret", "predictionPercentage": 0,
                "cacheTime": "2026-10-18T15:41:00.1"},
         "3A": {"fare": "565", "availabilityDisplayName": "AVL 1", "predictionPercentage": 100,
                "cacheTime": "2026-10-22T16:10:01"},
         "SL": {"fare": "180", "availabilityDisplayName": "Not Available", "predictionPercentage": 0,
                "cacheTime": "2026-10-23T20:38:00"}}},
    {"trainNumber": "16021", "trainName": "KAVERI EXPRESS", "departureTime": "03:55", "arrivalTime": "06:40",
     "duration": 165, "fromStnCode": "SBC", "toStnCode": "MYS", "availabilityCache": {
         "1A": {"fare": "1190", "availabilityDisplayName": "WL 3", "predictionPercentage": 82,
                "cacheTime": "2026-10-23T09:45:00"},
         "SL": {"fare": "150", "availabilityDisplayName": "AVL 19", "predictionPercentage": 100,
                "cacheTime": "2026-10-23T20:43:00"}}}]}}


def test_trains_are_read_from_the_data_api():
    asked = []

    def get(url):
        asked.append(url)
        return LIVE
    found = T.trains("SBC", "MYS", date(2026, 10, 25), get)
    assert "sourceStationCode=SBC&destinationStationCode=MYS&dateOfJourney=25-10-2026" in asked[0]
    assert [t["number"] for t in found] == ["22817", "16021"] and found[1]["name"] == "Kaveri Express"
    assert found[0]["classes"]["3A"] == {"fare": 565.0, "status": "AVL 1", "chance": 100, "as_of": "2026-10-22T16:10"}


def test_best_keeps_bookable_seats_in_the_class_asked_surest_first():
    found = T.trains("SBC", "MYS", date(2026, 10, 25), lambda url: LIVE)
    assert [(o["number"], o["class"]) for o in T.best(found, "SL")] == [("16021", "SL")]  # 22817 SL: Not Available
    assert T.best(found, "2A") == []  # Regret only
    anyc = T.best(found, None)  # no class said: each train's cheapest bookable class
    assert [(o["number"], o["class"], o["fare"]) for o in anyc] == [("16021", "SL", 150.0), ("22817", "3A", 565.0)]
    assert T.best(found, None, max_price=500, people=2) == [{**anyc[0], "total": 300.0}]  # budget for both
    assert [o["class"] for o in T.best(found, "1A")] == ["1A"] and T.best(found, "1A")[0]["sure"] == 0  # waitlist


def test_station_and_class_names():
    sugg = {"data": {"stationList": [{"stationCode": "MYS", "stationName": "Mysuru Jn", "city": "Mysuru"},
                                     {"stationCode": "NTW", "stationName": "Nanjangud Town", "city": "Nanjangud"}]}}
    assert T.station("Mysore", lambda url: sugg) == {"code": "MYS", "name": "Mysuru Jn", "city": "Mysuru"}
    assert T.station("Nowhere", lambda url: {"data": {"stationList": []}}) is None
    assert T.station("SBC")["code"] == "SBC"
    assert [T.train_class(x) for x in ("sleeper", "3A", "third AC", "chair car", "window", None)] == \
        ["SL", "3A", "3A", "CC", None, None]
