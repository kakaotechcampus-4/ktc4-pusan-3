from datetime import date, timedelta

import pytest

from app.rules.affinity_reason import is_stale, state_reason

TODAY = date(2026, 10, 7)


@pytest.mark.parametrize(
    ("count", "days_ago", "reason"),
    [
        (1, 0, "아직 한 번 기록됐어요. 마지막은 오늘이에요"),
        (4, 1, "지금까지 4번 기록됐고, 마지막은 어제예요"),
        (3, 214, "지금까지 3번 기록됐고, 마지막은 7개월 전이에요"),  # 낡은 기억은 개월로
    ],
)
def test_state_reason(count: int, days_ago: int, reason: str) -> None:
    last = TODAY - timedelta(days=days_ago)
    assert state_reason(observation_count=count, last_observed_on=last, today=TODAY) == reason


@pytest.mark.parametrize(
    ("last", "today", "stale"),
    [
        (date(2026, 4, 8), TODAY, False),  # 하루 모자람
        (date(2026, 4, 7), TODAY, True),  # 딱 6개월
        (date(2026, 8, 31), date(2027, 2, 28), True),  # 평년 2월 — 말일이 그날
        (date(2027, 8, 31), date(2028, 2, 28), False),  # 윤년 2월 — 29일이 있다
        (date(2027, 8, 31), date(2028, 2, 29), True),
        (date(2026, 12, 31), date(2027, 6, 30), True),  # 30일로 끝나는 달
    ],
)
def test_is_stale_at_six_months(last: date, today: date, stale: bool) -> None:
    assert is_stale(last, today=today) is stale
