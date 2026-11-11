"""급식표 한 구간이 덜 읽혔는지 — 평일 수는 코드가 센다 (CLAUDE.md §3 날짜 계산).

급식표 사진은 출력 상한 때문에 1~15일 · 16~말일 두 호출로 읽는다. 한 호출이 16일 하나만 내고 정상
종료(`stop`)한 적이 있다(2026-11-11 실측). 오류가 아니라서 "○일~○일 급식은 읽지 못했어요" 줄이
나가지 않았고, 21일치 중 11일치만 아무 표시 없이 저장 대상이 됐다. 그래서 (11-11):

- 한 구간에서 **읽은 날 × 2 < 그 구간의 평일 수**(평일의 절반에 못 미친다)면 덜 읽힌 것이다.
  평일은 코드가 정한 연 · 월의 달력으로 월~금, 그 달의 말일까지 센다. 공휴일은 따지지 않는다.
- 🚨 **다른 구간이 충분히(평일의 3/4 이상) 읽혔을 때만** 알린다. 한 주치(평일 5일 + 토요일)는
  어느 구간에서도 평일의 3/4 을 넘지 못한다(구간마다 평일이 9일 이상이라 6/9 < 3/4) — 한 주치 ·
  두 구간에 걸친 두 주치 식단표에서는 뜨지 않는다. 한 달 표라는 근거가 있을 때만 알린다.
- 호출이 실패한 구간(`failed`)은 이미 "읽지 못했어요" 줄이 나갔다. 겹쳐 알리지 않고, 다른 구간을
  알릴 근거로도 쓰지 않는다(한 달 표인지 알 수 없다).

"읽은 날" 이 무엇인지(급식 · 휴일 표시 · 못 읽은 칸)는 부르는 쪽(`agents/notice_ocr`)이 정해 준다.
화면 문구도 부르는 쪽 몫이다 — 이 모듈은 날짜 수만 센다.

순수 함수. 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

import calendar
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ThinRange:
    """덜 읽힌 구간. `last` 는 그 달의 말일에서 자른 값이다(16~31 → 11월이면 30)."""

    first: int
    last: int
    read_days: int
    weekdays: int


def weekdays_between(year: int, month: int, first: int, last: int) -> int:
    """그 달 `first`일~`last`일의 평일(월~금) 수. `last` 는 말일에서 자른다."""
    last = min(last, calendar.monthrange(year, month)[1])
    return sum(1 for d in range(first, last + 1) if date(year, month, d).weekday() < 5)


def _is_thin(r: ThinRange) -> bool:
    return r.read_days * 2 < r.weekdays


def _is_full(r: ThinRange) -> bool:
    return r.read_days > 0 and r.read_days * 4 >= r.weekdays * 3


def thin_ranges(
    year: int,
    month: int,
    ranges: Sequence[tuple[int, int]],
    read_days: Iterable[int],
    *,
    failed: Iterable[tuple[int, int]] = (),
) -> tuple[ThinRange, ...]:
    """나눠 부른 구간(`ranges`)과 읽은 날들 → 알릴 구간. 없으면 빈 튜플.

    `failed` 는 호출이 실패한 구간이다 — `ranges` 의 값 그대로(말일에서 자르기 전) 준다.
    """
    month_last = calendar.monthrange(year, month)[1]
    read = set(read_days)
    failed_set = set(failed)
    checked: list[tuple[ThinRange, bool]] = []
    for first, last in ranges:
        cut = min(last, month_last)
        if first > cut:
            continue
        count = sum(1 for d in read if first <= d <= cut)
        checked.append(
            (
                ThinRange(first, cut, count, weekdays_between(year, month, first, cut)),
                (first, last) in failed_set,
            )
        )
    return tuple(
        r
        for i, (r, r_failed) in enumerate(checked)
        if not r_failed
        and _is_thin(r)
        and any(_is_full(o) and not o_failed for j, (o, o_failed) in enumerate(checked) if j != i)
    )
