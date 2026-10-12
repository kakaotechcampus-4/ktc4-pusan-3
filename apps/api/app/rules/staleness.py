"""근거 신선도 판정 — 6개월 이상 지난 기록은 단독 근거로 쓰지 않는다.

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).

CLAUDE.md §2: "6개월 이상 지난 관심 기록은 단독 근거로 쓰지 않는다."
Affinity.is_stale · Evidence.is_stale · Agent 쪽 판정이 같은 기준을 써야 한다.
"""

from datetime import date, timedelta

STALE_DAYS = 180


def is_stale(observed_date: date, today: date) -> bool:
    """observed_date가 today 기준 6개월(180일) 이상 지났는지.

    today 는 호출하는 쪽이 넘긴다. 이 함수 안에서 date.today() 를 부르지 않는다 —
    서버(UTC)에서 한국 시각 0~9시에 하루 어긋나기 때문이다. rules/age.py 와 같은 원칙.
    """
    return (today - observed_date) >= timedelta(days=STALE_DAYS)
