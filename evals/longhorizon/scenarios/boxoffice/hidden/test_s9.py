from conftest import event

import boxoffice


def _office(path, venue, name, qty):
    boxoffice.reset()
    boxoffice.open_box_office(path)
    e = event(seats=50, venue=venue, price="200.00")
    ref = boxoffice.book(e, name, qty)
    boxoffice.close_box_office()
    return e, ref


def _merged(office, tmp_path):
    boxoffice.close_box_office()
    ours = _office(office, "Blue Hall", "Asha", 2)
    theirs = _office(str(tmp_path / "riverside"), "Riverside", "Ravi", 3)
    boxoffice.reset()
    boxoffice.open_box_office(office)
    boxoffice.merge_box_office(str(tmp_path / "riverside"))
    return ours, theirs


def test_merged_references_never_change(office, tmp_path):
    (e1, r1), (e2, r2) = _merged(office, tmp_path)
    assert r1 != r2
    assert boxoffice.get_booking(r1)["name"] == "Asha" and boxoffice.get_booking(r2)["name"] == "Ravi"
    assert boxoffice.get_booking(r2)["event"] == e2


def test_merging_keeps_both_venues_sales(office, tmp_path):
    (e1, _), (e2, _) = _merged(office, tmp_path)
    assert (boxoffice.sold(e1), boxoffice.sold(e2)) == (2, 3)
    assert [e["id"] for e in boxoffice.events_at("Riverside")] == [e2]
