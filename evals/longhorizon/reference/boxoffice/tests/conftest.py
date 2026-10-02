import pytest

import boxoffice


@pytest.fixture(autouse=True)
def clean(tmp_path):
    boxoffice.open_box_office(str(tmp_path / "office"))
    yield
    boxoffice.close_box_office()
