"""급식표 사진의 연도 · 월 → 확정 연월. 연도는 모델에게 묻지 않는다 (CLAUDE.md §2 · §3).

급식표 제목에는 연도가 없는 일이 많다 — "[11]월 급식계획". 그런데 표 읽기 모델은 `year_month` 를
"YYYY-MM" 으로 내야 해서 연도를 지어낸다. 지어낸 연도가 범위 밖이면 저장 전체가 막혔고(2026-11-11
운영 장애), 범위 안이면 틀린 날짜로 조용히 저장된다. 그래서:

- **월**은 모델이 읽은 것을 쓴다. 모양("11" · "11월" · "2026.11" · "26-11")은 가리지 않는다.
- **연도**는 모델이 낸 연도가 사진 글자(공지 읽기의 옮겨 적은 글)에 **그 달과 붙어** 적혀 있을
  때만 믿는다 — "2025년 1월" · "2025.01". "2025학년도 1월" 은 연도가 아니다(그 1월은 2026년이다).
- 아니면 **받은 날에 가장 가까운 그 달**로 정한다.

공지 날짜 규칙(`notice_dates._nearest`)을 그대로 쓰지 않는 이유 — 그 규칙은 받은 해와 다음 해만
본다. 1월 5일에 받은 12월 급식표가 11개월 뒤의 12월이 된다. 공지의 날짜는 대개 앞으로의 일이라
그래도 되지만, 급식표는 한 달 단위로 지난달 · 이번 달 · 다음 달 것이 올라온다.

순수 함수. 표준 라이브러리만 쓴다 (apps/api/CLAUDE.md 레이어 경계).
"""

import re
import unicodedata
from collections.abc import Iterable
from datetime import date

# 모델이 낸 값의 모양. 대괄호 · 공백은 먼저 지운다 ("[11]월" · "2026년 11월")
_YEAR4 = re.compile(r"(?P<y>\d{4})(?:년|[./-])?(?P<m>\d{1,2})월?")
_YEAR2 = re.compile(r"(?P<y>\d{2})(?:년|[./-])(?P<m>\d{1,2})월?")
_MONTH = re.compile(r"(?P<m>\d{1,2})월?")
_NOISE = re.compile(r"[\s\[\]()【】]")
# 반년 앞뒤가 같으면 지난 것으로 본다. 반년 뒤의 급식표는 아직 만들어지지 않는다
_HALF_YEAR = 6


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


def year_written(year: int, month: int, transcript: str) -> bool:
    """사진 글자에 그 연도가 그 달과 붙어 적혀 있는가 — "2025년 1월" · "2025.01" · "25년 1월"."""
    text = unicodedata.normalize("NFKC", transcript or "")
    short = f"{year % 100:02d}"
    pattern = rf"(?<!\d)(?:{year}\s*(?:년|[./-])|{short}\s*년)\s*[\[(]?\s*0?{month}(?!\d)"
    return re.search(pattern, text) is not None


def nearest_year(month: int, received_on: date) -> int:
    """받은 날에 가장 가까운 그 달의 연도. 반년 앞뒤가 같으면 지난 쪽이다."""
    gap = month - received_on.month  # -11 ~ 11
    if gap >= _HALF_YEAR:
        return received_on.year - 1
    if gap < -_HALF_YEAR:
        return received_on.year + 1
    return received_on.year


def decide_year_month(
    model_values: Iterable[str], *, transcript: str, received_on: date
) -> tuple[int, int] | None:
    """급식표 호출들이 낸 `year_month` 들 → (연도, 월). 정할 수 없으면 None(표를 못 쓴다).

    - 월을 읽은 호출끼리 월이 다르면 None 이다. 어느 쪽이 맞는지 모르고, 한쪽 월로 정하면 다른 쪽
      절반의 날짜가 조용히 틀린다.
    - 연도는 원문에 그 달과 붙어 적힌 것 하나일 때만 믿는다. 아니면 받은 날로 정한다.
    """
    parsed = [p for p in (parse_model_month(v) for v in model_values) if p is not None]
    months = {month for _, month in parsed}
    if len(months) != 1:
        return None
    (month,) = months
    written = {
        year for year, _ in parsed if year is not None and year_written(year, month, transcript)
    }
    if len(written) == 1:
        return written.pop(), month
    return nearest_year(month, received_on), month
