"""조회 기간 라벨 → 날짜 범위. 모델은 라벨만 고르고 날짜는 코드가 계산한다.

범위는 양끝을 포함하고 오늘이 들어 있다. 오늘 이전만 보는 쪽(급식 · 영양 판정)은 호출부가 자른다.
"""

from datetime import date, timedelta

from app.agents.food.schemas.common import MealPeriod


def period_range(period: MealPeriod, today: date) -> tuple[date, date]:
    """`(시작, 끝)` 양끝 포함. 이번 주는 월요일부터 오늘까지다."""
    match period:
        case MealPeriod.TODAY:
            return today, today
        case MealPeriod.YESTERDAY:
            yesterday = today - timedelta(days=1)
            return yesterday, yesterday
        case MealPeriod.LAST_3_DAYS:
            return today - timedelta(days=2), today
        case MealPeriod.THIS_WEEK:
            return today - timedelta(days=today.weekday()), today
        case MealPeriod.LAST_7_DAYS:
            return today - timedelta(days=6), today
    raise AssertionError(f"모르는 MealPeriod: {period}")  # pragma: no cover


def week_end(today: date) -> date:
    """오늘이 든 주의 일요일. 주는 월요일에 시작한다 — 공통 날짜 규칙의 "이번주" 와 같은 주다.

    영양소 분석의 출현 창이 이번 주 남은 급식까지 보는 끝이다. 날짜 계산은 코드가 한다(루트 §2).
    """
    return today + timedelta(days=6 - today.weekday())
