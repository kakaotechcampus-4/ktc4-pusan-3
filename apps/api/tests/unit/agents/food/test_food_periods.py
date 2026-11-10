"""이번 주 끝 날짜 — 영양소 분석의 출현 창이 이번 주 급식까지 보는 데 쓴다."""

from datetime import date, timedelta

import pytest

from app.agents.common.datetime_rules import resolve_date_range
from app.agents.food.periods import week_end


@pytest.mark.parametrize(
    ("today", "sunday"),
    [
        (date(2026, 11, 9), date(2026, 11, 15)),  # 월
        (date(2026, 11, 11), date(2026, 11, 15)),  # 수
        (date(2026, 11, 14), date(2026, 11, 15)),  # 토
        (date(2026, 11, 15), date(2026, 11, 15)),  # 일 — 오늘이 그 주 마지막 날
        (date(2026, 11, 16), date(2026, 11, 22)),  # 다음 월
    ],
)
def test_이번_주_일요일을_돌려준다(today: date, sunday: date) -> None:
    assert week_end(today) == sunday


@pytest.mark.parametrize("offset", range(14))
def test_공통_날짜_규칙의_이번주와_같은_주다(offset: int) -> None:
    today = date(2026, 11, 2) + timedelta(days=offset)
    this_week = resolve_date_range("이번주", today=today)

    # DateRange 는 [시작, 끝) 이라 끝 하루 전이 일요일이다
    assert week_end(today) == this_week.end - timedelta(days=1)
