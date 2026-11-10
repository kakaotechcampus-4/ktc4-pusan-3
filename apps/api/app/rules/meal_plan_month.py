"""급식표 사진의 연도 · 월 → 확정 연월. 연도는 모델에게 묻지 않는다 (CLAUDE.md §2 · §3).

급식표 제목에는 연도가 없는 일이 많다 — "[11]월 급식계획". 그런데 표 읽기 모델은 `year_month` 를
"YYYY-MM" 으로 내야 해서 연도를 지어낸다. 지어낸 연도가 범위 밖이면 저장 전체가 막혔고(2026-11-11
운영 장애), 범위 안이면 틀린 날짜로 조용히 저장된다. 그래서 (11-11 PM):

- **월**은 모델이 읽은 것을 쓴다. 모양("11" · "11월" · "2026.11" · "26-11")은 가리지 않는다.
- ① 사진 글자(공지 읽기의 옮겨 적은 글)에 연도가 **그 달과 붙어** 적혀 있으면 그 연도다
  ("2025년 1월" · "2025.01"). "2025학년도 1월" 은 연도가 아니다(그 1월은 2026년이다).
  모델이 낸 연도와 같거나 모델이 연도를 안 냈으면 `written`(알리지 않는다).
- ② 적혀 있지 않으면 그 달이 받은 날의 **지난달 · 이번 달 · 다음 달**일 때만 코드가 연도를 정한다.
  `guessed` — 🚨 추정했다는 것을 화면에 알린다(조용히 채우지 않는다).
- ③ 적혀 있지 않고 그 밖의 달이면 정하지 않는다 — `unknown`. 급식을 저장 대상으로 내지 않는다.
- ④ 원문의 연도가 모델과 다르거나 그 달의 연도가 여럿이면, 원문의 연도 중 받은 날에 가장 가까운
  것을 쓰고 알린다(`written_other`). 똑같이 가까운 것이 둘이면 ③이다.

공지 날짜 규칙(`notice_dates._nearest`)을 쓰지 않는 이유 — 그 규칙은 60일 앞까지만 지난 것으로 보고
나머지는 앞으로의 날로 읽는다. 11월에 받은 3월 급식표가 "내년 3월" 이 되고, 1월에 받은 12월
급식표가 11개월 뒤가 된다. 급식표는 한 달 단위로 지난달 · 이번 달 · 다음 달 것이 올라온다.

순수 함수. 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal

YearSource = Literal["written", "written_other", "guessed", "unknown"]

# 모델이 낸 값의 모양. 대괄호 · 공백은 먼저 지운다 ("[11]월" · "2026년 11월")
_YEAR4 = re.compile(r"(?P<y>\d{4})(?:년|[./-])?(?P<m>\d{1,2})월?")
_YEAR2 = re.compile(r"(?P<y>\d{2})(?:년|[./-])(?P<m>\d{1,2})월?")
_MONTH = re.compile(r"(?P<m>\d{1,2})월?")
_NOISE = re.compile(r"[\s\[\]()【】]")
# 받은 날의 달에서 앞뒤로 이만큼까지만 연도를 짐작한다 — 지난달 · 이번 달 · 다음 달
_GUESS_MONTHS = 1


@dataclass(frozen=True)
class MealMonth:
    month: int
    year: int | None
    """`None` 이면 정하지 않았다(`unknown`) — 급식을 날짜로 내보내지 않는다."""
    source: YearSource


def parse_model_month(value: str) -> tuple[int | None, int] | None:
    """모델이 낸 `year_month` → (연도 또는 None, 월). 월을 못 읽으면 None."""
    text = _NOISE.sub("", unicodedata.normalize("NFKC", value or ""))
    for pattern in (_YEAR4, _YEAR2, _MONTH):
        if m := pattern.fullmatch(text):
            month = int(m["m"])
            if not 1 <= month <= 12:
                return None
            year = m.groupdict().get("y")
            if year is None:
                return None, month
            return int(year) + (2000 if len(year) == 2 else 0), month
    return None


def written_years(month: int, transcript: str) -> set[int]:
    """사진 글자에 그 달과 붙어 적힌 연도들 — "2025년 1월" · "2025.01" · "25년 1월"."""
    text = unicodedata.normalize("NFKC", transcript or "")
    tail = rf"\s*[\[(]?\s*0?{month}(?!\d)"
    years = {int(y) for y in re.findall(rf"(?<!\d)(20\d{{2}})\s*(?:년|[./-]){tail}", text)}
    years |= {2000 + int(y) for y in re.findall(rf"(?<!\d)(\d{{2}})\s*년{tail}", text)}
    return years


def _months_from(received_on: date, year: int, month: int) -> int:
    return (year - received_on.year) * 12 + (month - received_on.month)


def _guess_year(month: int, received_on: date) -> int | None:
    """그 달이 받은 날의 지난달 · 이번 달 · 다음 달이면 그 연도. 아니면 None."""
    for year in (received_on.year - 1, received_on.year, received_on.year + 1):
        if abs(_months_from(received_on, year, month)) <= _GUESS_MONTHS:
            return year
    return None


def decide_year_month(
    model_values: Iterable[str], *, transcript: str, received_on: date
) -> MealMonth | None:
    """급식표 호출들이 낸 `year_month` 들 → `MealMonth`. 월을 정할 수 없으면 None(표를 못 쓴다).

    월을 읽은 호출끼리 월이 다르면 None 이다. 어느 쪽이 맞는지 모르고, 한쪽 월로 정하면 다른 쪽
    절반의 날짜가 조용히 틀린다. `received_on` 은 받은 날의 **한국 날짜**다(부르는 쪽이 바꿔 준다).
    """
    parsed = [p for p in (parse_model_month(v) for v in model_values) if p is not None]
    months = {month for _, month in parsed}
    if len(months) != 1:
        return None
    (month,) = months
    model_years = {year for year, _ in parsed if year is not None}
    written = written_years(month, transcript)
    if len(written) == 1 and model_years <= written:
        return MealMonth(month, written.pop(), "written")
    if written:
        ranked = sorted(written, key=lambda y: abs(_months_from(received_on, y, month)))
        gaps = [abs(_months_from(received_on, y, month)) for y in ranked]
        if len(ranked) > 1 and gaps[0] == gaps[1]:
            return MealMonth(month, None, "unknown")
        return MealMonth(month, ranked[0], "written_other")
    guessed = _guess_year(month, received_on)
    if guessed is None:
        return MealMonth(month, None, "unknown")
    return MealMonth(month, guessed, "guessed")
