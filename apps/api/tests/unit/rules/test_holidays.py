"""관공서 공휴일 상수.

대체공휴일은 2027년 우주항공청 월력요항 보도자료의 7회와 맞춘다. 공휴일이 빠지면 그날을
"어린이집 가는 날"로 읽는다.
"""

from datetime import date

import pytest

from app.rules.holidays import PUBLIC_HOLIDAYS, holidays_known, is_public_holiday


@pytest.mark.parametrize(
    "day",
    [
        date(2026, 10, 3),  # 개천절 (토)
        date(2026, 10, 5),  # 대체공휴일
        date(2026, 10, 9),  # 한글날
        date(2026, 12, 25),
        date(2027, 2, 9),  # 설날 대체
    ],
)
def test_공휴일(day):
    assert is_public_holiday(day) is True


@pytest.mark.parametrize("day", [date(2026, 10, 6), date(2026, 10, 10), date(2027, 1, 2)])
def test_공휴일이_아닌_날(day):
    assert is_public_holiday(day) is False


def test_2027_대체공휴일은_월력요항의_7회():
    substitutes = {
        date(2027, 2, 9),
        date(2027, 5, 3),
        date(2027, 7, 19),
        date(2027, 8, 16),
        date(2027, 10, 4),
        date(2027, 10, 11),
        date(2027, 12, 27),
    }
    assert substitutes <= PUBLIC_HOLIDAYS[2027]


def test_해마다_날짜는_그해_안에_있다():
    for year, days in PUBLIC_HOLIDAYS.items():
        assert all(day.year == year for day in days)


def test_넣어_두지_않은_해는_모른다고_답한다():
    assert holidays_known(2026) is True
    assert holidays_known(2028) is False
    assert is_public_holiday(date(2028, 1, 1)) is False
