"""급식 날짜 조회(`daycare_lookup`) — 날짜 풀기 · 잘린 구간 검사 · 화면 문구.

모델은 발화에서 날짜를 가리키는 구간만 고르고(`show_daycare_meal(date_span)`), 어느 날(들)인지는
여기서 코드가 정한다. 기준일(`today`)은 호출하는 쪽이 넘긴다(KST).

갱신 · 삭제의 `meal_dates.resolve_meal_date` 는 하루만 풀고 요일만 말하면 되묻는다(쓰기라서).
조회는 기간("이번 주")도 풀고, 요일만 말하면 **다가올** 요일로 푼다 — 답에 날짜가 적혀 나가서
틀려도 보호자가 바로 안다. 연도 없는 월일은 `nearest` 로 푼다. `future` 로 풀면 지난 월일
("11월 5일 급식 뭐였어?")이 내년이 되어 "없어요" 가 나간다.
"""

import re
from datetime import date, timedelta

from app.agents.common.datetime_rules import resolve_date_range
from app.agents.common.readout import Readout
from app.agents.food.meal_dates import resolve_meal_date
from app.agents.food.readouts import (
    DAYCARE_LOOKUP_EMPTY,
    DAYCARE_LOOKUP_LINE,
    DAYCARE_REFERENCE_MENU,
    READOUTS,
)
from app.agents.food.store.ports import DAYCARE_SLOT_ORDER, DaycareMealRow, DaycareSlot

# 한 번 조회로 보여 주는 최대 일수. "이번 주"(월~일)는 7일이라 통과하고 달은 막힌다
LOOKUP_MAX_DAYS = 7

# 급식 문구가 쓰는 끼니 이름. 갱신 문구(`tools/menu.py`)와 같은 이름을 쓰려고 한 곳에 둔다
SLOT_LABEL: dict[DaycareSlot, str] = {
    "lunch": "점심",
    "snack_am": "오전 간식",
    "snack_pm": "오후 간식",
}

_WEEKDAY_NAMES = "월화수목금토일"
_LAST_WEEKDAY = re.compile(r"^(?:지난|저번)\s*([월화수목금토일]요일)$")
# 잘린 구간을 찾으려고 구간 둘레를 훑는 글자 수 — 이 안의 낱말이 날짜 표현의 길이 한도다
_AROUND = 10


def resolve_lookup_range(span: str | None, *, today: date) -> tuple[date, date]:
    """날짜 구간 → (첫날, 마지막 날). 양끝을 포함한다. 풀 수 없으면 `DateParseError`.

    - 구간이 없으면 오늘이다("급식 뭐야?").
    - `지난 · 저번 {요일}` 은 지난 요일이다(`resolve_meal_date`). 그 밖은 `resolve_date_range` 라
      하루 표현은 하루로, 주 · 주말 · 달은 기간으로 풀린다.
    - 요일만("화요일")은 다가올 요일이다. 오늘이 그 요일이면 다음 주다.
    """
    text = (span or "").strip()
    if not text:
        return today, today
    if _LAST_WEEKDAY.match(text):
        day = resolve_meal_date(text, today=today)
        return day, day
    found = resolve_date_range(text, today=today)
    return found.start, found.end - timedelta(days=1)


def _reachable(char: str) -> bool:
    """날짜 표현에 들어갈 수 있는 글자 — 한글 · 숫자 · 띄어쓰기."""
    return "가" <= char <= "힣" or char.isdigit() or char == " "


def cut_from_longer_lookup_span(utterance: str, span: str, *, today: date) -> bool:
    """`span` 이 원문에서 더 긴 날짜 표현의 일부로만 나오고, 그 표현이 다른 날(들)을 가리키나.

    모델이 날짜 표현을 잘라 주면 코드는 잘린 쪽을 푼다 — 월요일에 "다음 주 화요일 급식" 의
    `화요일` 은 다가올 화요일(이번 주)이 되고, "지난 화요일" 의 `화요일` 은 지난 날 대신 다가올
    날이 된다. "내일모레" 의 `내일` · "이번 주말" 의 `이번 주` 도 같다. 구간 둘레(띄어 쓴 앞뒤 낱말
    포함)에서 더 긴 표현을 모두 풀어 보고 하나라도 다른 범위를 가리키면 True 다. 같은 날을 가리키는
    짧은 구간("내일모레" 의 `모레`)은 받는다. `span` 이 원문에 여러 번 나오면 모든 자리에서 그래야
    True 다. 구간을 풀 수 없으면 False 다(어차피 못 푼다고 되묻는다).
    """
    text = span.strip()
    if not text:
        return False
    try:
        own = resolve_lookup_range(text, today=today)
    except ValueError:
        return False
    starts = [found.start() for found in re.finditer(re.escape(text), utterance)]
    return bool(starts) and all(
        _wider_range_differs(utterance, start, len(text), own, today) for start in starts
    )


def _wider_range_differs(
    utterance: str, start: int, size: int, own: tuple[date, date], today: date
) -> bool:
    """`start` 의 구간을 감싼 더 긴 표현 중 다른 범위로 풀리는 것이 있나."""
    left = start
    while left > 0 and start - left < _AROUND and _reachable(utterance[left - 1]):
        left -= 1
    stop = start + size
    right = stop
    while right < len(utterance) and right - stop < _AROUND and _reachable(utterance[right]):
        right += 1
    for begin in range(left, start + 1):
        for end in range(stop, right + 1):
            if (begin, end) == (start, stop):
                continue
            try:
                other = resolve_lookup_range(utterance[begin:end], today=today)
            except ValueError:
                continue
            if other != own:
                return True
    return False


def date_label(day: date) -> str:
    """ "11월 12일(목)". 갱신 문구의 날짜 이름(`11월 12일`)과 달리 요일이 붙는다."""
    return f"{day.month}월 {day.day}일({_WEEKDAY_NAMES[day.weekday()]})"


def _when(date_from: date, date_to: date) -> str:
    if date_from == date_to:
        return date_label(date_from)
    return f"{date_from.month}월 {date_from.day}일 ~ {date_to.month}월 {date_to.day}일"


def lookup_readouts(
    rows: list[DaycareMealRow], *, date_from: date, date_to: date
) -> tuple[Readout, ...]:
    """읽은 급식 행 → 화면 문구. 모두 코드 문구다.

    행마다 한 줄(`daycare.lookup_line`)을 날짜순 · 같은 날이면 끼니 순으로 줄바꿈으로 이어 한
    readout 에 담는다. 행 없는 날은 건너뛴다. 메뉴가 빈 행은 줄을 만들지 않는다. 줄이 하나도
    없으면 `daycare.lookup_empty` 한 줄이다. 지역 표준식단(`center_standard`) 행이 있으면 아이가
    실제로 먹은 것과 다를 수 있다는 안내(`daycare.reference_menu`)를 따로 붙인다.
    """
    shown = sorted(
        (row for row in rows if row.menu_keys),
        key=lambda row: (row.serve_date, DAYCARE_SLOT_ORDER[row.meal_slot]),
    )
    if not shown:
        return (READOUTS.render(DAYCARE_LOOKUP_EMPTY, when=_when(date_from, date_to)),)
    line = READOUTS.get(DAYCARE_LOOKUP_LINE)
    body = "\n".join(
        line.template.format(
            date=date_label(row.serve_date),
            slot=SLOT_LABEL[row.meal_slot],
            menus=", ".join(row.menu_keys),
        )
        for row in shown
    )
    readouts = (Readout(kind=line.kind, body=body, authored_by="code"),)
    if any(row.origin == "center_standard" for row in shown):
        readouts += (READOUTS.render(DAYCARE_REFERENCE_MENU),)
    return readouts
