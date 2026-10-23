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
    group = {"data": {"stationList": [{"stationCode": "SBC", "stationName": "Bengaluru - All stations", "city": "Bengaluru"}]}}
    assert T.station("Bangalore", lambda url: group)["name"] == "Bengaluru"  # seen live
    assert [T.train_class(x) for x in ("sleeper", "3A", "third AC", "chair car", "window", None)] == \
        ["SL", "3A", "3A", "CC", None, None]


BUSES = {"status": "success", "services": [  # trimmed from the real Bangalore -> Mysore answer for 25 Oct 2026
    {"travelerAgentName": "KSRTC Kerala", "busTypeName": "Super Fast Non AC Seater (3+2)", "fares": {"fare": 219},
     "timings": {"startTimeTwfFormat": "08:00", "arriveTimeTwfFormat": "10:20", "travelTime": "02:20:00"},
     "seatStats": {"availableSeats": 35}, "rating": 4.1, "noOfRatings": 30},
    {"travelerAgentName": "Jayavin Tours and Travels", "busTypeName": "AC Seater/Sleeper (2 + 1)", "fares": {"fare": 400},
     "timings": {"startTimeTwfFormat": "05:00", "arriveTimeTwfFormat": "07:25", "travelTime": "02:25:00"},
     "seatStats": {"availableSeats": 32}, "rating": 4.4, "noOfRatings": 446},
    {"travelerAgentName": "Cheap Bad Travels", "busTypeName": "Non AC Sleeper (2+1)", "fares": {"fare": 150},
     "timings": {"startTimeTwfFormat": "23:00", "arriveTimeTwfFormat": "02:00", "travelTime": "03:00:00"},
     "seatStats": {"availableSeats": 20}, "rating": 2.9, "noOfRatings": 120},
    {"travelerAgentName": "Night Rider", "busTypeName": "A/C Sleeper (2+1)", "fares": {"fare": 650},
     "timings": {"startTimeTwfFormat": "22:30", "arriveTimeTwfFormat": "01:30", "travelTime": "03:00:00"},
     "seatStats": {"availableSeats": 1}, "rating": None, "noOfRatings": 0}]}


def test_buses_are_read_and_picked_by_code():
    sent = []

    def get(url, body=None):
        sent.append((url, body))
        return BUSES
    found = T.buses({"id": 7, "name": "Bangalore"}, {"id": 926, "name": "Mysore"}, date(2026, 10, 25), get)
    assert sent[0][1]["sourceid"] == 7 and sent[0][1]["destinationid"] == 926 and sent[0][1]["jdate"] == "2026-10-25"
    assert found[0] == {"operator": "KSRTC Kerala", "type": "Super Fast Non AC Seater (3+2)", "dep": "08:00",
                        "arr": "10:20", "minutes": 140, "fare": 219.0, "seats": 35, "rating": 4.1, "ratings": 30}
    assert [b["operator"] for b in T.best_buses(found)] == ["KSRTC Kerala", "Jayavin Tours and Travels", "Night Rider"]  # 2.9★ out
    assert [b["operator"] for b in T.best_buses(found, seat="sleeper")] == ["Jayavin Tours and Travels", "Night Rider"]
    assert [b["operator"] for b in T.best_buses(found, seat="AC sleeper", window="night")] == ["Night Rider"]
    assert [b["operator"] for b in T.best_buses(found, seat="non ac")] == ["KSRTC Kerala"]
    semi = {**found[3], "operator": "NueGo", "type": "AC Semi Sleeper Electric (2 + 2)", "seats": 30}
    assert [b["operator"] for b in T.best_buses(found + [semi], seat="sleeper", window="night")] == ["Night Rider"]
    assert T.best_buses(found, window="night", people=2) == []  # one seat left on the only night bus
    assert [b["total"] for b in T.best_buses(found, max_price=500, people=2)] == [438.0]


def test_bus_city():
    sugg = [{"id": 926, "label": "Mysore", "city": "Mysuru"}, {"id": 1, "label": "Other"}]
    assert T.bus_city("Mysore", lambda url, body=None: sugg) == {"id": 926, "name": "Mysore"}
    assert T.bus_city("Nowhere", lambda url, body=None: []) is None


EMT = {"C": {"9I": "AllianceAir", "6E": "IndiGo", "AI": "Air India"},  # the shape seen live, BLR -> GOI, 30 Oct 2026
       "dctFltDtl": {"0": {"OG": "BLR", "DT": "GOI", "DTM": "14:50", "ATM": "16:25", "FN": "547", "AC": "9I"},
                     "1": {"OG": "BLR", "DT": "GOI", "DTM": "06:10", "ATM": "07:25", "FN": "6101", "AC": "6E"},
                     "2": {"OG": "BLR", "DT": "BOM", "DTM": "21:00", "ATM": "22:45", "FN": "602", "AC": "AI"},
                     "3": {"OG": "BOM", "DT": "GOI", "DTM": "23:40", "ATM": "00:50", "FN": "681", "AC": "AI"}},
       "j": [{"s": [{"TF": 5494, "TotalJyTm": 95, "b": [{"FL": [0]}]},
                    {"TF": 6120, "TotalJyTm": 75, "b": [{"FL": [1]}]},
                    {"TF": 4990, "TotalJyTm": 230, "b": [{"FL": [2, 3]}]},
                    {"TF": 7020, "TotalJyTm": 75, "b": [{"FL": [1]}]}]}]}


def test_flights_are_read_and_picked_by_code():
    found = T.read_flights(EMT)
    assert found[2] == {"airline": "Air India", "flight": "AI 602, AI 681", "from": "BLR", "to": "GOI", "dep": "21:00",
                        "arr": "00:50", "stops": 1, "via": ["BOM"], "minutes": 230, "total": 4990.0, "fare": 4990}
    assert [f["flight"] for f in T.best_flights(found)] == ["AI 602, AI 681", "9I 547", "6E 6101"]  # one fare per flight
    assert [f["flight"] for f in T.best_flights(found, max_price=6000, window="afternoon")] == ["9I 547"]
    assert [f["flight"] for f in T.best_flights(found, avoid="Air India")] == ["9I 547", "6E 6101"]
    assert T.read_flights(EMT, adults=2)[0]["fare"] == 2747  # TF is for everyone
    assert [T.airport(x) for x in ("Bangalore", "goa", "IXZ", "Nowhere")] == ["BLR", "GOI", "IXZ", None]


def test_flights_open_the_results_page_and_read_its_own_data():
    opened = []

    class Tab:
        def net_start(self):
            pass

        def open(self, url):
            opened.append(url)

        def settle(self, timeout=0):
            return ""

        def net_json(self, contains, limit=20):
            return [{"url": "https://flightservice-node.easemytrip.com/AirAvail_Lights/AirBus_New", "body": EMT}]

        def close(self):
            pass
    got = T.flights("BLR", "GOI", date(2026, 10, 30), 1, tab=Tab)
    assert "srch=BLR-BLR-India|GOI-GOI-India|30/10/2026&px=1-0-0" in opened[0] and len(got) == 4
