from conftest import event

import boxoffice


def test_jpy_and_kwd_use_their_own_decimals():
    yen = event(price="1500", currency="JPY")
    assert boxoffice.quote(yen, 2) == {"amount": "3000", "currency": "JPY"}
    dinar = event(price="1.255", currency="KWD")
    assert boxoffice.quote(dinar, 3) == {"amount": "3.765", "currency": "KWD"}
    ref = boxoffice.book(dinar, "Asha", 2)
    assert boxoffice.get_booking(ref)["amount"] == "2.510"
