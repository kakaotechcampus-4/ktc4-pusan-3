"""요일 반복 일정의 회차 날짜와 반복 문구 (#738).

순수 함수. LLM·DB·외부 I/O 없음 — 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).

반복은 승인할 때 회차마다 일정 행으로 펼쳐진다. 어느 날이 회차인지는 모델이 아니라 이 함수가
정한다 — 날짜 계산은 규칙(코드)의 몫이다 (루트 CLAUDE.md §3).

요일은 ISO 정수다 (1=월 … 7=일). 기간은 첫 회차를 포함해 `MAX_REPEAT_DAYS` 일까지다. 세는 첫 날은
**첫 회차 날짜**다 — 묶음에 저장되는 날짜가 그것이라, 만들 때와 고칠 때가 같은 기준이 된다.
"""

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Literal

MAX_REPEAT_DAYS = 90
"""첫 회차 포함. `until - 첫 회차 <= MAX_REPEAT_DAYS - 1`."""

RepeatCode = Literal["no_weekday", "bad_weekday", "until_before_start", "too_long", "no_occurrence"]

_NAMES = {1: "월", 2: "화", 3: "수", 4: "목", 5: "금", 6: "토", 7: "일"}
_WEEKDAYS = (1, 2, 3, 4, 5)
_EVERY_DAY = (1, 2, 3, 4, 5, 6, 7)


class RepeatError(ValueError):
    """반복 규칙이 성립하지 않는다. `code` 로 이유를 가른다."""

    def __init__(self, code: RepeatCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def occurrence_dates(first: date, weekdays: Iterable[int], until: date) -> list[date]:
    """`[first, until]` 에서 요일이 맞는 날을 오름차순으로. 같은 요일이 겹쳐 와도 하나로 본다.

    90일은 `first` 가 아니라 그 뒤 처음 맞는 날(첫 회차)에서 센다.
    """
    days = _weekday_set(weekdays)
    if until < first:
        raise RepeatError("until_before_start", "끝 날짜가 시작 날짜보다 앞이다.")
    start = next(
        (
            first + timedelta(days=offset)
            for offset in range(7)
            if (first + timedelta(days=offset)).isoweekday() in days
        ),
        None,
    )
    if start is None or start > until:
        raise RepeatError("no_occurrence", "시작 날짜와 끝 날짜 사이에 그 요일이 없다.")
    if (until - start).days > MAX_REPEAT_DAYS - 1:
        raise RepeatError("too_long", f"반복은 첫 회차부터 {MAX_REPEAT_DAYS}일 안에서만 된다.")
    return [
        start + timedelta(days=offset)
        for offset in range((until - start).days + 1)
        if (start + timedelta(days=offset)).isoweekday() in days
    ]


def repeat_label(weekdays: Iterable[int], until: date) -> str:
    """화면에 내는 반복 문구. 예: "12월 20일까지 매주 월, 수"."""
    days = tuple(sorted(_weekday_set(weekdays)))
    if days == _EVERY_DAY:
        which = "매일"
    elif days == _WEEKDAYS:
        which = "평일"
    else:
        which = "매주 " + ", ".join(_NAMES[day] for day in days)
    return f"{until.month}월 {until.day}일까지 {which}"


def _weekday_set(weekdays: Iterable[int]) -> set[int]:
    days = set(weekdays)
    if not days:
        raise RepeatError("no_weekday", "반복할 요일이 없다.")
    if not days <= _NAMES.keys():
        raise RepeatError("bad_weekday", "요일은 1(월)부터 7(일)까지다.")
    return days
