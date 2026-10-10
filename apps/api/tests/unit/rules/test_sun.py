"""일출 · 일몰 계산.

기대값은 한국천문연구원이 공개한 서울의 하지 · 동지 일출몰 시각이다.
계산식 특성상 1–2분 차이는 날 수 있어 3분까지 허용한다 — 야외 판정에는 충분하다.
"""

from datetime import date, datetime, time

import pytest

from app.rules.sun import sun_times

SEOUL = (37.5665, 126.9780)
TOLERANCE_MIN = 3


def minutes(t: time) -> int:
    return t.hour * 60 + t.minute


@pytest.mark.parametrize(
    ("day", "sunrise", "sunset"),
    [
        (date(2026, 6, 21), time(5, 11), time(19, 57)),
        (date(2026, 12, 22), time(7, 43), time(17, 17)),
    ],
    ids=["하지", "동지"],
)
def test_서울_하지_동지(day, sunrise, sunset):
    rise, set_ = sun_times(day, *SEOUL)
    assert abs(minutes(rise) - minutes(sunrise)) <= TOLERANCE_MIN
    assert abs(minutes(set_) - minutes(sunset)) <= TOLERANCE_MIN


def test_동쪽일수록_해가_일찍_진다():
    """부산은 서울보다 동쪽이라 같은 날 해가 먼저 진다."""
    day = date(2026, 9, 29)
    _, seoul_set = sun_times(day, *SEOUL)
    _, busan_set = sun_times(day, 35.1796, 129.0756)
    assert minutes(busan_set) < minutes(seoul_set)


def test_하루_안에서_일출이_일몰보다_먼저다():
    rise, set_ = sun_times(date(2026, 3, 20), *SEOUL)
    assert datetime.combine(date.min, rise) < datetime.combine(date.min, set_)


def test_극지방_백야면_None():
    assert sun_times(date(2026, 6, 21), 78.2, 15.6) is None


def test_범위_밖이면_거절하고_좌표를_싣지_않는다():
    with pytest.raises(ValueError) as exc:
        sun_times(date(2026, 1, 1), 95.0, 127.0)
    assert "95" not in str(exc.value)
