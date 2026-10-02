from conftest import event

import boxoffice


def test_everything_survives_reopening(office):
    e = event(seats=50)
    boxoffice.close_box_office()
    boxoffice.reset()
    boxoffice.open_box_office(office)
    assert boxoffice.get_event(e)["seats"] == 50
