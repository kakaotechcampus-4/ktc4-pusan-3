"""Memory Agent가 추출한 날짜·시각 표현을 확정 값으로 변환하는 규칙 모듈.

LLM은 자연어의 의미 해석까지만 담당한다.
예를 들어 "모레 운동회가 있고 전날 저녁 8시에 알려줘"라는 입력에서
"모레", "저녁 8시", "행사 기준 하루 전"과 같은 표현과 관계를 추출한다.

이 모듈은 그 결과를 AgentContext의 현재 시각과 서비스 timezone을 기준으로
실제 date / time / datetime 값으로 계산한다.

역할 분리 원칙:
- LLM: 날짜·시각 표현 추출, 과거/미래 문맥 판단, 기준 일정과의 관계 해석
- 이 모듈: 날짜 덧셈·뺄셈, 요일/연도 계산, 24시간제 변환, timezone 적용, 유효성 검증
- DB/tool: 계산된 값을 실제 observation / event / reminder 필드에 저장
"""

from dataclasses import dataclass
import re
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Literal

TemporalDirection = Literal["past", "future", "nearest"]

class DateParseError(ValueError):
    """날짜·시각 표현을 해석하지 못함. tool 은 DATE_UNPARSEABLE 로 바꿔 모델에 되돌린다."""

@dataclass(frozen=True)
class DateRange:
    """조회에 사용할 [start, end) 날짜 범위."""
    start: date
    end: date
    
_DAY_OFFSETS = { # 오늘 기준 며칠 차이인지
    "그저께": -2, "그제": -2, "재작일": -2,
    "어제": -1, "작일": -1, "어저께": -1,
    "오늘": 0, "금일": 0,
    "내일": 1, "명일": 1, "낼": 1,
    "모레": 2, "내일모레": 2, "낼모레": 2,
    "글피": 3,
}
_YEAR_OFFSETS = {"올해": 0, "금년": 0, "작년": -1, "지난해": -1, "전년": -1, "재작년": -2}

# 기준 일정이 있어야 뜻이 정해지는 표현
_ANCHOR_RELATIVE = {"전날", "전일", "다음날", "익일", "당일", "하루전", "이틀전", "하루뒤"}

_WEEKDAY_INDEX = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_WEEK_OFFSET = {
    "이번주": 0, "금주": 0,
    "다음주": 1, "담주": 1, "차주": 1,
    "지난주": -1, "저번주": -1, "전주": -1,
}

_YEAR_RE = re.compile(r"^(?P<year>\d{4})년$")
_YEAR_MONTH_RE = re.compile(r"^(?P<year>\d{4})년(?P<month>\d{1,2})월$")
_YEAR_MONTH_DAY_RE = re.compile(r"^(?P<year>\d{4})년(?P<month>\d{1,2})월(?P<day>\d{1,2})일$")
_WEEKDAY_RE = re.compile(r"^(?P<week>이번주|금주|다음주|담주|차주|지난주|저번주|전주)?(?P<day>[월화수목금토일])요일$")
_MONTH_DAY_RE = re.compile(r"^(?P<month>\d{1,2})월(?P<day>\d{1,2})일$")

_NAMED_TIMES = {"정오": time(12, 0), "자정": time(0, 0), "한밤중": time(0, 0)}
_TIME_RE = re.compile(
    r"^(?P<mer>새벽|아침|오전|낮|점심|오후|저녁|밤)?"
    r"(?P<hour>\d{1,2})(?::|시)"
    r"(?:(?P<minute>\d{1,2})분?|(?P<half>반))?$"
)
_TIME_TAIL_RE = re.compile(r"(에|쯤|경|정각|께)$")

_PM_MERIDIEMS = {"오후", "저녁", "낮", "점심"}
_AM_MERIDIEMS = {"오전", "아침", "새벽"}


def today_of(now: datetime, tz: tzinfo) -> date:
    return now.astimezone(tz).date()


def resolve_date(value: str, *, today: date, direction: TemporalDirection = "nearest") -> date:
    """하루를 가리키는 날짜 표현 또는 ISO 문자열을 확정 날짜로 바꾼다.
    명시적 연도는 그대로 사용하고, 연도 생략 월일·요일만 direction을 적용한다."""
    if not isinstance(value, str) or not value.strip():
        raise DateParseError("날짜 표현이 비어 있다.")

    text = _normalize(value)
    iso = _try_iso_date(text)
    if iso is not None:
        return iso

    key = text.replace(" ", "")
    if key in _ANCHOR_RELATIVE:
        raise DateParseError(
            f"'{text}' 는 기준 일정이 있어야 해석된다. offset_days_from_event 를 쓴다."
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


def resolve_date_range(value: str, *, today: date, direction: TemporalDirection = "nearest") -> DateRange:
    """조회용 날짜 표현을 [start, end) 범위로 바꾼다. 연도·연월·상대연도는 기간으로, 하루 표현은 1일 범위로 만든다."""
    if not isinstance(value, str) or not value.strip():
        raise DateParseError("조회 날짜 표현이 비어 있다.")
    text = _normalize(value)
    key = text.replace(" ", "")

    if key in _YEAR_OFFSETS:
        year = today.year + _YEAR_OFFSETS[key]
        return DateRange(start=date(year, 1, 1), end=date(year+1, 1, 1))
    matched = _YEAR_RE.match(key)
    if matched is not None:
        year = int(matched.group("year"))
        return DateRange(start=date(year, 1, 1), end=date(year+1, 1, 1))
    matched = _YEAR_MONTH_RE.match(key)
    if matched is not None:
        year, month = int(matched.group("year")), int(matched.group("month"))
        if not 1 <= month <= 12:
            raise DateParseError(f"존재하지 않는 월: {text!r}")
        start = date(year, month, 1)
        end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        return DateRange(start=start, end=end)
    
    day = resolve_date(text, today=today, direction=direction)
    return DateRange(start=day, end=day + timedelta(days=1))


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


def shift_days(day: date, days: int) -> date:
    """기준 날짜에서 days만큼 이동. reminder의 offset_days_from_event가 쓴다."""
    return day + timedelta(days=days)


def build_observed_range(day: date) -> str:
    """observation의 daterange 리터럴. 관찰 1건은 하루로 보고 항상 [d, d+1) 반닫힘으로 만든다."""
    return f"[{day.isoformat()},{(day + timedelta(days=1)).isoformat()})"


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _try_iso_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).date()   # LLM 이 ISO datetime 을 줄 때도 있음
    except ValueError:
        return None


def _try_weekday(key: str, today: date, direction: TemporalDirection) -> date | None:
    matched = _WEEKDAY_RE.match(key)
    if matched is None:
        return None

    target = _WEEKDAY_INDEX[matched.group("day")]
    week = matched.group("week")
    if week is not None:                               # "다음 주 목요일" 처럼 주가 명시된 경우
        monday = today - timedelta(days=today.weekday())   # 주의 시작은 월요일
        return monday + timedelta(days=_WEEK_OFFSET[week] * 7 + target)

    # 요일만 말한 경우. 오늘은 후보에서 빼고 앞이나 뒤로 가장 가까운 그 요일을 찾는다
    if direction == "past":
        return today - timedelta(days=(today.weekday() - target) % 7 or 7)

    # future/nearest 는 다음번 그 요일.
    # 요일만 말할 때는 다가올 일정을 가리키는 경우가 대부분이라 nearest 도 미래로 봄
    return today + timedelta(days=(target - today.weekday()) % 7 or 7)


def _try_month_day(key: str, today: date, direction: TemporalDirection) -> date | None:
    matched = _MONTH_DAY_RE.match(key)
    if matched is None:
        return None

    month, day = int(matched.group("month")), int(matched.group("day"))
    candidates = [
        candidate for year in (today.year - 1, today.year, today.year + 1)
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
    
    year, month, day = int(matched.group("year")), int(matched.group("month")), int(matched.group("day"))
    candidate = _try_make_date(year, month, day)
    if candidate is None:
        raise DateParseError(f"존재하지 않는 날짜: {key!r}")
    return candidate

def _try_make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _to_24_hour(hour: int, meridiem: str | None) -> int:
    if meridiem in _PM_MERIDIEMS:
        return hour if hour == 12 else hour + 12      # 낮 12시 = 12:00, 저녁 8시 = 20:00
    if meridiem == "밤":
        return 0 if hour == 12 else hour + 12         # 밤 12시 = 자정
    if meridiem in _AM_MERIDIEMS:
        return 0 if hour == 12 else hour              # 오전 12시 = 자정
    return hour                                       # 표현이 없으면 24시간제로
