"""근거 신선도 — 달력 6개월 판정 하나를 기억 카드 · 추천 근거 · Agent 가 같이 쓴다.

루트 §2 · NF-08.
"""

from datetime import date, timedelta

import pytest

from app.rules import affinity_reason, staleness


@pytest.mark.parametrize(
    ("observed", "today", "stale"),
    [
        (date(2026, 4, 14), date(2026, 10, 14), True),  # 정확히 6개월
        (date(2026, 4, 15), date(2026, 10, 14), False),  # 하루 모자람 — 180일로 세면 낡았다
        (date(2026, 8, 31), date(2027, 2, 28), True),  # 해당일이 없는 달은 말일이 그날
        (date(2026, 8, 31), date(2027, 2, 27), False),  # 180일째 — 180일로 세면 낡았다
        (date(2026, 10, 15), date(2026, 10, 14), False),  # 미래 날짜는 낡지 않았다
    ],
)
def test_6개월은_달력으로_센다(observed: date, today: date, stale: bool) -> None:
    assert staleness.is_stale(observed, today) is stale


def test_기억_카드와_같은_판정이다() -> None:
    today = date(2026, 10, 14)
    for days in range(170, 200):
        observed = today - timedelta(days=days)
        same = affinity_reason.is_stale(observed, today=today)
        assert staleness.is_stale(observed, today) == same, observed
