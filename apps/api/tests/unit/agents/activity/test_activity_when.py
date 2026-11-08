"""후보 · 조회의 "언제" — 보호자가 쓴 날 표현을 날짜로 (#800).

모델은 날짜를 계산하지 않는다. 표현을 그대로 넘기고 코드가 공통 날짜 규칙으로 푼다.
"""

from datetime import date

import pytest

from app.agents.activity.when import MAX_DAYS, PastDay, days_of
from app.agents.common.datetime_rules import DateParseError

SUNDAY = date(2026, 11, 8)
WEDNESDAY = date(2026, 11, 11)
SATURDAY = date(2026, 11, 14)


def _d(month: int, day: int) -> date:
    return date(2026, month, day)


class TestDaysOf:
    @pytest.mark.parametrize("when", ["today", "오늘", "", "  "])
    def test_오늘(self, when):
        assert days_of(when, SUNDAY) == (SUNDAY,)

    @pytest.mark.parametrize("when", ["tomorrow", "내일"])
    def test_내일(self, when):
        assert days_of(when, SUNDAY) == (_d(11, 9),)

    def test_이번_주말은_토일_이틀이다(self):
        assert days_of("this_weekend", WEDNESDAY) == (_d(11, 14), _d(11, 15))
        assert days_of("이번 주말", WEDNESDAY) == (_d(11, 14), _d(11, 15))

    def test_일요일의_이번_주말은_오늘_하루다(self):
        assert days_of("this_weekend", SUNDAY) == (SUNDAY,)

    def test_토요일의_이번_주말은_토일이다(self):
        assert days_of("this_weekend", SATURDAY) == (SATURDAY, _d(11, 15))

    @pytest.mark.parametrize("when", ["다음 주 주말", "다음 주말", "다음주 주말"])
    def test_다음_주_주말(self, when):
        assert days_of(when, SUNDAY) == (_d(11, 14), _d(11, 15))

    def test_월_일(self):
        assert days_of("11월 20일", SUNDAY) == (_d(11, 20),)

    def test_다음_주_요일(self):
        assert days_of("다음 주 토요일", SUNDAY) == (_d(11, 14),)

    def test_모레(self):
        assert days_of("모레", SUNDAY) == (_d(11, 10),)

    def test_긴_범위는_앞쪽만_본다(self):
        days = days_of("다음 달", SUNDAY)
        assert days[0] == _d(12, 1)
        assert len(days) == MAX_DAYS

    def test_지난_날은_잘라낸다(self):
        # "이번 주" 는 월요일(11-02)부터지만 오늘(일)부터만 본다
        assert days_of("이번 주", SUNDAY) == (SUNDAY,)

    def test_다_지난_날이면_PastDay(self):
        with pytest.raises(PastDay):
            days_of("어제", SUNDAY)

    def test_못_읽는_표현은_DateParseError(self):
        with pytest.raises(DateParseError):
            days_of("언젠가", SUNDAY)
