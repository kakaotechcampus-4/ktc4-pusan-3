"""Memory Agent가 추출한 날짜·시각 표현을 확정 값으로 변환하는 규칙 모듈.

LLM은 자연어의 의미 해석까지만 담당한다.
예를 들어 "금요일 오전 10시에 물놀이가 있어"라는 입력에서
"금요일", "오전 10시" 같은 표현과 과거/미래 문맥을 추출한다.

이 모듈은 그 결과를 AgentContext의 현재 시각과 서비스 timezone을 기준으로
실제 date / time / datetime 값으로 계산한다.

역할 분리 원칙:
- LLM: 날짜·시각 표현 추출, 과거/미래 문맥 판단
- 이 모듈: 날짜 덧셈·뺄셈, 요일/연도 계산, 24시간제 변환, timezone 적용, 유효성 검증
- DB/tool: 계산된 값을 실제 observation / event 필드에 저장
"""

import re
from dataclasses import dataclass
from datetime import MAXYEAR, MINYEAR, date, datetime, time, timedelta, tzinfo
from typing import Literal

TemporalDirection = Literal["past", "future", "nearest"]

_MIN_YEAR, _MAX_YEAR = MINYEAR + 1, MAXYEAR - 1


class DateParseError(ValueError):
    """날짜·시각 표현을 해석하지 못함. tool 은 DATE_UNPARSEABLE 로 바꿔 모델에 되돌린다."""


@dataclass(frozen=True)
class DateRange:
    """조회에 사용할 [start, end) 날짜 범위."""

    start: date
    end: date


_DAY_OFFSETS = {  # 오늘 기준 며칠 차이인지
    "그저께": -2,
    "그제": -2,
    "재작일": -2,
    "어제": -1,
    "작일": -1,
    "어저께": -1,
    "오늘": 0,
    "금일": 0,
    "내일": 1,
    "명일": 1,
    "낼": 1,
    "모레": 2,
    "내일모레": 2,
    "낼모레": 2,
    "글피": 3,
}
_YEAR_OFFSETS = {"올해": 0, "금년": 0, "작년": -1, "지난해": -1, "전년": -1, "재작년": -2}

# 기준 일정이 있어야 뜻이 정해지는 표현
_ANCHOR_RELATIVE = {"전날", "전일", "다음날", "익일", "당일", "하루전", "이틀전", "하루뒤"}

_WEEKDAY_INDEX = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_WEEK_OFFSET = {
    "이번주": 0,
    "금주": 0,
    "다음주": 1,
    "담주": 1,
    "차주": 1,
    "지난주": -1,
    "저번주": -1,
    "전주": -1,
}

_YEAR_RE = re.compile(r"^(?P<year>\d{4})년$")
_YEAR_MONTH_RE = re.compile(r"^(?P<year>\d{4})년(?P<month>\d{1,2})월$")
_YEAR_MONTH_DAY_RE = re.compile(r"^(?P<year>\d{4})년(?P<month>\d{1,2})월(?P<day>\d{1,2})일$")
_WEEKDAY_RE = re.compile(
    r"^(?P<week>이번주|금주|다음주|담주|차주|지난주|저번주|전주)?(?P<day>[월화수목금토일])요일$"
)
_MONTH_DAY_RE = re.compile(r"^(?P<month>\d{1,2})월(?P<day>\d{1,2})일$")

_NAMED_TIMES = {"정오": time(12, 0), "자정": time(0, 0), "한밤중": time(0, 0)}
# 시각 자리에 올 수 있는 "하루 종일". 일정에서만 뜻이 있다 (00:00~23:59)
_ALL_DAY = {"하루종일", "종일", "온종일", "하루온종일"}
ALL_DAY_START = time(0, 0)
ALL_DAY_END = time(23, 59)
_TIME_RE = re.compile(
    r"^(?P<mer>새벽|아침|오전|낮|점심|오후|저녁|밤)?"
    r"(?P<hour>\d{1,2})(?::|시)"
    r"(?:(?P<minute>\d{1,2})분?|(?P<half>반))?$"
)
_TIME_TAIL_RE = re.compile(r"(에|쯤|경|정각|께)$")
# 구간을 여닫는 조사
_RANGE_TAIL_RE = re.compile(r"(?:부터|까지)$")

_PM_MERIDIEMS = {"오후", "저녁", "낮", "점심"}
_AM_MERIDIEMS = {"오전", "아침", "새벽"}


def today_of(now: datetime, tz: tzinfo) -> date:
    return now.astimezone(tz).date()


def resolve_date(value: str, *, today: date, direction: TemporalDirection = "nearest") -> date:
    """하루를 가리키는 날짜 표현 또는 ISO 문자열을 확정 날짜로 바꾼다.
    명시적 연도는 그대로 사용하고, 연도 생략 월일·요일만 direction을 적용한다."""
    if not isinstance(value, str) or not value.strip():
        raise DateParseError("날짜 표현이 비어 있다.")

    # "어제부터 계속 기침" 의 "어제부터" 처럼 구간을 여는 표현은 그 날짜 하나로 본다.
    # (관찰은 하루 단위 — build_observed_range)
    text = _RANGE_TAIL_RE.sub("", _normalize(value)) or _normalize(value)
    iso = _try_iso_date(text)
    if iso is not None:
        _check_year(iso.year, text)
        return iso

    key = text.replace(" ", "")
    if key in _ANCHOR_RELATIVE:
        raise DateParseError(
            f"'{text}' 는 기준 일정이 있어야 풀리는 날짜다. 사용자에게 날짜를 되묻는다."
        )
    if key in _DAY_OFFSETS:
        return today + timedelta(days=_DAY_OFFSETS[key])

    resolved = _try_year_month_day(key)
    if resolved is not None:
        return resolved
    resolved = _try_weekday(key, today, direction)
    if resolved is not None:
        return resolved
    resolved = _try_month_day(key, today, direction)
    if resolved is not None:
        return resolved

    raise DateParseError(f"해석할 수 없는 날짜 표현: {text!r}")


def resolve_date_range(
    value: str, *, today: date, direction: TemporalDirection = "nearest"
) -> DateRange:
    """조회용 날짜 표현을 [start, end) 범위로 바꾼다.
    연도·연월·상대연도는 기간으로, 하루 표현은 1일 범위로 만든다."""
    if not isinstance(value, str) or not value.strip():
        raise DateParseError("조회 날짜 표현이 비어 있다.")
    text = _normalize(value)
    key = text.replace(" ", "")

    if key in _YEAR_OFFSETS:
        year = today.year + _YEAR_OFFSETS[key]
        return DateRange(start=date(year, 1, 1), end=date(year + 1, 1, 1))
    matched = _YEAR_RE.match(key)
    if matched is not None:
        year = int(matched.group("year"))
        _check_year(year, text)
        return DateRange(start=date(year, 1, 1), end=date(year + 1, 1, 1))
    matched = _YEAR_MONTH_RE.match(key)
    if matched is not None:
        year, month = int(matched.group("year")), int(matched.group("month"))
        _check_year(year, text)
        if not 1 <= month <= 12:
            raise DateParseError(f"존재하지 않는 월: {text!r}")
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return DateRange(start=start, end=end)

    day = resolve_date(text, today=today, direction=direction)
    return DateRange(start=day, end=day + timedelta(days=1))


def resolve_query_bound(
    value: str | None, *, today: date, direction: TemporalDirection, is_end: bool
) -> date | None:
    """조회 경계를 확정한다. 표현이 없으면 경계도 없다(None).

    resolve_date_range 는 [start, end) 열린 끝을 주는데 store 는 양쪽 닫힌 구간을 본다.
    그래서 끝 경계는 하루 당긴다. 덕분에 "2026년 9월" 하나로 월 전체를 가리킬 수 있다.

    direction 은 호출하는 쪽이 정한다 — 관찰 조회는 과거, 일정 조회는 미래가 기본이다.
    "금요일" 같은 표현이 지난 금요일인지 다가올 금요일인지는 계산으로 못 정한다.
    """
    if value is None:
        return None
    span = resolve_date_range(value, today=today, direction=direction)
    return span.end - timedelta(days=1) if is_end else span.start


def is_all_day(value: str | None) -> bool:
    """시각 자리에 "하루 종일" 이 온 경우. 일정은 00:00~23:59 · all_day 로 저장한다.

    시각을 모르는 것(=되묻는다)과 하루 종일인 것(=아는 것)은 다르다. 그래서 값으로 받는다.
    """
    if value is None:
        return False
    return _normalize(value).replace(" ", "") in _ALL_DAY


def resolve_time(value: str | None) -> time | None:
    """시각 표현을 확정 시각으로 바꾼다. 표현이 없으면 임의 시각을 만들지 않고 None으로 둔다."""
    if value is None:
        return None

    text = _normalize(value)
    if not text:
        return None

    key = _TIME_TAIL_RE.sub("", text.replace(" ", ""))
    if key in _NAMED_TIMES:
        return _NAMED_TIMES[key]

    matched = _TIME_RE.match(key)
    if matched is None:
        raise DateParseError(f"해석할 수 없는 시각 표현: {text!r}")

    hour = _to_24_hour(int(matched.group("hour")), matched.group("mer"))
    minute = 30 if matched.group("half") else int(matched.group("minute") or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise DateParseError(f"범위를 벗어난 시각: {text!r}")
    return time(hour, minute)


def combine(day: date, moment: time | None, tz: tzinfo) -> datetime:
    """날짜와 시각을 timezone 이 붙은 datetime 으로 합친다. 시각이 없으면 자정."""
    return datetime.combine(day, moment or time(0, 0), tzinfo=tz)


def build_observed_range(day: date) -> DateRange:
    """observation의 관찰 구간. 관찰 1건은 하루로 보고 항상 [d, d+1) 반닫힘으로 만든다.
    daterange 리터럴 문자열을 만들지 않는다. DB 표현으로 바꾸는 건 store어댑터 몫이다.
    """
    return DateRange(start=day, end=day + timedelta(days=1))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _try_iso_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).date()  # LLM 이 ISO datetime 을 줄 때도 있음
    except ValueError:
        return None


def _try_weekday(key: str, today: date, direction: TemporalDirection) -> date | None:
    matched = _WEEKDAY_RE.match(key)
    if matched is None:
        return None

    target = _WEEKDAY_INDEX[matched.group("day")]
    week = matched.group("week")
    if week is not None:  # "다음 주 목요일" 처럼 주가 명시된 경우
        monday = today - timedelta(days=today.weekday())  # 주의 시작은 월요일
        return monday + timedelta(days=_WEEK_OFFSET[week] * 7 + target)

    # 요일만 말한 경우. 오늘은 후보에서 빼고 앞이나 뒤로 가장 가까운 그 요일을 찾는다
    if direction == "past":
        return today - timedelta(days=(today.weekday() - target) % 7 or 7)

    # future/nearest 는 다음번 그 요일.
    # 요일만 말할 때는 대부분 다가올 일정을 가리키는 경우라 nearest 도 미래로 봄
    return today + timedelta(days=(target - today.weekday()) % 7 or 7)


def _try_month_day(key: str, today: date, direction: TemporalDirection) -> date | None:
    matched = _MONTH_DAY_RE.match(key)
    if matched is None:
        return None

    month, day = int(matched.group("month")), int(matched.group("day"))
    candidates = [
        candidate
        for year in (today.year - 1, today.year, today.year + 1)
        if (candidate := _try_make_date(year, month, day)) is not None
    ]
    if not candidates:
        raise DateParseError(f"존재하지 않는 날짜: {key!r}")
    if direction == "past":
        past_candidates = [candidate for candidate in candidates if candidate <= today]
        if not past_candidates:
            raise DateParseError(f"작년~올해 범위에서 과거 날짜를 찾을 수 없음: {key!r}")
        return max(past_candidates)
    if direction == "future":
        future_candidates = [candidate for candidate in candidates if candidate >= today]
        if not future_candidates:
            raise DateParseError(f"올해~내년 범위에서 미래 날짜를 찾을 수 없음: {key!r}")
        return min(future_candidates)
    return min(candidates, key=lambda candidate: abs((candidate - today).days))


def _try_year_month_day(key: str) -> date | None:
    matched = _YEAR_MONTH_DAY_RE.match(key)
    if matched is None:
        return None

    year = int(matched.group("year"))
    _check_year(year, key)
    month, day = int(matched.group("month")), int(matched.group("day"))
    candidate = _try_make_date(year, month, day)
    if candidate is None:
        raise DateParseError(f"존재하지 않는 날짜: {key!r}")
    return candidate


def _check_year(year: int, text: str) -> None:
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        raise DateParseError(f"다룰 수 없는 연도: {text!r}")


def _try_make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _to_24_hour(hour: int, meridiem: str | None) -> int:
    if meridiem in _PM_MERIDIEMS:
        return hour if hour == 12 else hour + 12  # 낮 12시 = 12:00, 저녁 8시 = 20:00
    if meridiem == "밤":
        return 0 if hour == 12 else hour + 12  # 밤 12시 = 자정
    if meridiem in _AM_MERIDIEMS:
        return 0 if hour == 12 else hour  # 오전 12시 = 자정
    return hour  # 표현이 없으면 24시간제로
