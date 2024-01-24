from datetime import date

from app.core.dates import display_date


def test_dates_read_day_month_year() -> None:
    assert display_date(date(2019, 11, 20)) == "20-11-2019"
    assert display_date("2019-11-20") == "20-11-2019"
    assert display_date(None) == ""


def test_moments_are_shown_as_their_malaysian_date() -> None:
    # 18:00 UTC on 20 Nov is already 21 Nov in Kuala Lumpur (UTC+8)
    assert display_date("2018-11-20T18:00:00+00:00") == "21-11-2018"
    assert display_date("2018-11-20T03:00:00Z") == "20-11-2018"
