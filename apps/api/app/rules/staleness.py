"""근거 신선도 판정 — 6개월 이상 지난 기록은 단독 근거로 쓰지 않는다 (루트 §2 · NF-08).

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).

기억 카드(`Affinity.is_stale`) · 추천 근거(`Evidence.is_stale`, #323) · Agent 의 개인화
판정(`build()`)이 이 함수 하나를 쓴다. 개월 수는 `rules/age.months_between`(민법 달력)으로
센다 — 해당일이 없는 달은 말일이 그날이다(8월 31일의 6개월 뒤는 2월 말일).
"""

from datetime import date

from app.rules.age import months_between

STALE_MONTHS = 6


def is_stale(observed_on: date, today: date) -> bool:
    """마지막 기록이 달력 기준 6개월 이상 전이면 True. 미래 날짜는 낡지 않았다.

    today 는 호출하는 쪽이 넘긴다. 이 함수 안에서 date.today() 를 부르지 않는다 —
    서버(UTC)에서 한국 시각 0~9시에 하루 어긋나기 때문이다. rules/age.py 와 같은 원칙.
    """
    if observed_on > today:
        return False
    return months_between(observed_on, today) >= STALE_MONTHS
