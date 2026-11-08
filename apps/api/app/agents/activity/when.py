"""후보 · 조회의 "언제" — 보호자가 쓴 날 표현을 날짜로 바꾼다 (#800).

모델은 날짜를 계산하지 않는다. 보호자가 쓴 표현("내일" · "다음 주 주말" · "11월 20일")을 그대로
넘기고 코드가 공통 날짜 규칙(`resolve_date_range`)으로 푼다 (루트 CLAUDE.md §2 · §3).

- 예전 라벨(today · tomorrow · this_weekend)도 그대로 받는다 — 같은 뜻이다.
- 이번 주말은 토 · 일 이틀이다. 오늘이 토 · 일이면 오늘부터다 — 지난 토요일은 보지 않는다.
- 오늘 이전 날은 잘라낸다("이번 주" 는 오늘부터). 다 지난 날이면 `PastDay` 다.
- 범위가 길면("다음 달") 앞 `MAX_DAYS` 일만 본다 — 날마다 날씨 · 행사를 조회한다.
- 못 읽는 표현은 공통 규칙의 `DateParseError` 를 그대로 올린다. 호출부가 모델에게 다시 쓰게 한다.
"""

from datetime import date, timedelta

from app.agents.common.datetime_rules import DateParseError, resolve_date_range

MAX_DAYS = 7

_TODAY = frozenset({"today", "오늘", "지금", "오늘밤", "오늘저녁"})
_TOMORROW = frozenset({"tomorrow", "내일"})
_THIS_WEEKEND = frozenset({"this_weekend", "이번주말", "주말", "이번주토일"})


class PastDay(DateParseError):
    """이미 지난 날이다 — 오늘로 바꾸지 않는다."""


def days_of(when: str, today: date) -> tuple[date, ...]:
    """표현이 가리키는 날들(오늘 이후, 최대 `MAX_DAYS` 일)."""
    key = "".join(str(when).split())
    if key in _TODAY or not key:
        return (today,)
    if key in _TOMORROW:
        return (today + timedelta(days=1),)
    if key in _THIS_WEEKEND:
        return _this_weekend(today)

    found = resolve_date_range(str(when), today=today, direction="future")
    start = max(found.start, today)
    end = min(found.end, start + timedelta(days=MAX_DAYS))
    if end <= start:
        raise PastDay(f"이미 지난 날이다: {when!r}")
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days))


def is_today_only(when: str, today: date) -> bool:
    """그 표현이 오늘 하루뿐인가. 못 읽으면 False — 검사는 호출부가 따로 한다."""
    try:
        return days_of(when, today) == (today,)
    except DateParseError:
        return False


def _this_weekend(today: date) -> tuple[date, ...]:
    saturday = today + timedelta(days=5 - today.weekday())
    sunday = saturday + timedelta(days=1)
    return tuple(day for day in (saturday, sunday) if day >= today)
