from conftest import event

import boxoffice


def test_sales_report_skips_cancelled():
    e = event(seats=10, price="150.00")
    boxoffice.book(e, "Asha", 2)
    boxoffice.cancel(boxoffice.book(e, "Ravi", 3))
    boxoffice.book(e, "Lena", 1)
    assert boxoffice.sales_report(e) == {"bookings": 2, "seats": 3, "revenue": {"amount": "450.00", "currency": "INR"}}
