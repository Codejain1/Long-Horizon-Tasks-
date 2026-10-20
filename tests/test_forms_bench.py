"""bench/forms.py is sound: a perfect extraction passes every case, and the scorer catches guessing and wrong kinds."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))
import forms as F  # noqa: E402

ORACLE = [
 {"to":"Mumbai","date":"friday"}, {"to":"Delhi","date":"5 Nov"}, {"from":"Bangalore","to":"Goa","date":"tomorrow","time_window":"night"},
 {"dish":"paneer biryani","max_total":350}, {"drop":"airport","when":"tomorrow 5am"}, {"city":"Goa"},
 {"to":"Chennai","date":"2 Nov","passengers":["Kartik","Sunita"]}, {}, {"to":"Mumbai","date":"saturday","max_price":6000},
 {"to":"Jaipur","arrive":"10 Nov","leave":"13 Nov"},
 {"to":"Hyderabad","date":"monday","class":"SL"}, {"to":"Kolkata","date":"tomorrow","max_price":"9k"},
 {"pickup":"office","drop":"home","when":"now","cab_type":"sedan"}, {"diet":"veg","max_total":300},
 {"city":"Udaipur","checkin":"14 Nov","checkout":"16 Nov","max_price_per_night":4000}, {"to":"Pune"},
 {"from":"Bangalore","to":"Hampi","date":"friday","passengers":["Kartik","Rahul"]}, {"to":"Bali"},
 {"dish":"masala dosa","restaurant":"MTR","max_total":250}, {"to":"Chennai","date":"31 Oct","class":"2A"},
 {"city":"Goa","area":"Baga beach","checkin":"7 Nov","checkout":"9 Nov","guests":["Kartik","Sunita"],"max_price_per_night":5000},
 {"drop":"railway station","when":"2 Nov 6am"}, {"to":"Delhi","date":"31 Sep"}, {"to":"Goa"}, {"dish":"pizza"},
 {"from":"Delhi","to":"Agra","date":"15 Nov","passengers":["Sunita"]}, {"from":"Mysore","to":"Bangalore","date":"day after tomorrow","seat":"AC sleeper"},
 {"to":"Chennai","date":"2 Nov","cabin":"business","max_price":20000}, {"drop":"airport"},
 {"city":"Mumbai","checkin":"today","checkout":"friday","max_price_per_night":3000}]


def test_perfect_extraction_passes_every_case(tmp_path):
    assert len(ORACLE) == len(F.CASES)
    for i, (c, o) in enumerate(zip(F.CASES, ORACLE)):
        v = F.B.start(F.seeded(tmp_path / f"{i}.db"), c[2], o, today=F.TODAY)
        assert F.score(c, v["kind"], "summary" in v, v["slots"]) == (True, ""), c[1]


def test_scorer_catches_guesses_wrong_kinds_and_wrong_values():
    flight_delhi, train_mumbai = F.CASES[1], F.CASES[0]
    assert not F.score(flight_delhi, "flight", False, {"to": "Delhi", "max_price": 5000})[0]  # guessed the budget
    assert not F.score(flight_delhi, "flight", True, {"to": "Delhi"})[0]
    assert not F.score(train_mumbai, "bus", True, {})[0]
    good = {"to": "Mumbai", "date": "2026-10-23", "from": "Bangalore", "class": "3A", "passengers": ["Kartik"]}
    assert F.score(train_mumbai, "train", True, good) == (True, "")
    assert not F.score(train_mumbai, "train", True, {**good, "date": "2026-10-30"})[0]
