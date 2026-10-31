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


class DateNeedsAsking(DateParseError):
    """표현은 알아들었지만 하루로 정해지지 않는다("지난주", 기준 일정을 못 찾은 "소풍 이틀 전").
    모델이 다른 표현으로 고쳐 부르면 날짜를 지어내게 되니, 메시지대로 보호자에게 묻는다."""


class DateNeedsAnchor(DateNeedsAsking):
    """무엇에서 센 날인지가 있어야 풀린다("전날", 관찰의 "3일 뒤").
    기준을 어디에 넣는지는 tool 마다 달라서, 다시 부르는 안내는 tool 쪽이 붙인다."""


class MissingStartTime(DateParseError):
    """일정의 시작 시각을 모르면 자정으로 때우지 않고 몇 시인지 되묻게 한다."""


class MissingEndTime(DateParseError):
    """끝나는 날짜만 알고 끝나는 시각을 모르는 경우. 시작 시각으로 채우지 않고 되묻게 한다."""


@dataclass(frozen=True)
class DateRange:
    """조회에 사용할 [start, end) 날짜 범위."""

    start: date
    end: date


@dataclass(frozen=True)
class EventWhen:
    """일정이 언제인가를 의미하는 클래스.
    시작+종료+종일 여부는 따로 움직이지 않는 시간 표현으로 간주한다."""

    starts_at: datetime
    ends_at: datetime | None
    all_day: bool


@dataclass(frozen=True)
class WhenPatch:
    """발화에서 뽑은 "언제"에 해당하는 표현. 말하지 않은 자리는 None으로 두기."""

    starts_on: str | None = None  # 시작 "날짜" (모레, 금요일, 2026-09-17)
    starts_time: str | None = None  # 시작 "시각" (오전 10시, 하루 종일)
    ends_on: str | None = None
    ends_time: str | None = None
    direction: TemporalDirection = "nearest"
    drop_end: bool = False  # 종료를 지움

    def is_empty(self) -> bool:
        """언제인지에 대해 아무 말도 하지 않았다면 시간 구간을 건드리지 않는다."""
        return (
            self.starts_on is None
            and self.starts_time is None
            and self.ends_on is None
            and self.ends_time is None
            and not self.drop_end
        )


_DAY_OFFSETS = {  # 오늘 기준 며칠 차이인지
    "그끄저께": -3,
    "그끄제": -3,
    "그저께": -2,
    "그제": -2,
    "재작일": -2,
    "어제": -1,
    "작일": -1,
    "어저께": -1,
    "어젯밤": -1,
    "간밤": -1,
    "오늘": 0,
    "금일": 0,
    "내일": 1,
    "명일": 1,
    "낼": 1,
    "모레": 2,
    "내일모레": 2,
    "낼모레": 2,
    "글피": 3,
    "그글피": 4,
}
_YEAR_OFFSETS = {"올해": 0, "금년": 0, "작년": -1, "지난해": -1, "전년": -1, "재작년": -2}

# 기준 일정에서 며칠 떨어졌는지만 말하는 표현. 기준 일정 없이 오면 되묻는다
_ANCHOR_WORDS = {
    "전날": -1,
    "전일": -1,
    "그전날": -1,
    "당일": 0,
    "그날": 0,
    "다음날": 1,
    "그다음날": 1,
    "익일": 1,
    "이튿날": 1,
}

# "이틀 전" · "사흘 뒤" · "일주일 전" · "3일 후" · "2주 전" 처럼 날수를 센 표현.
# 기준은 따로 정한다 — 기준 일정이 있으면 그 날, 없으면 오늘 (resolve_date 의 anchor)
_SPAN_RE = re.compile(r"^(?P<amount>.+?)(?P<side>전|뒤|후)$")
_NATIVE_DAYS = {
    "하루": 1,
    "이틀": 2,
    "사흘": 3,
    "나흘": 4,
    "닷새": 5,
    "엿새": 6,
    "이레": 7,
    "여드레": 8,
    "아흐레": 9,
    "열흘": 10,
    "보름": 15,
}
_DIGIT_DAYS_RE = re.compile(r"^(?P<days>\d{1,4})일$")
# "일주일" · "한 주" · "2주" · "두 주일". "이주" 는 "이번 주" 줄임과 헷갈려 받지 않는다
_WEEKS_RE = re.compile(r"^(?P<weeks>\d{1,3}|일|한|두|세|네)주(?:일)?$")
_WEEK_COUNTS = {"일": 1, "한": 1, "두": 2, "세": 3, "네": 4}

# 하루로 정해지지 않는 표현. 고르지 않고 되묻는다 — "지난주에 카레 먹었어" 를 지난주 월요일로
# 두면 지어낸 날짜다. 되물을 때 어느 기간인지 보여 주려고 기간은 계산해 둔다(_try_period)
_VAGUE_DAYS = {"며칠전", "엊그제", "엊그저께", "얼마전"}
_MONTH_OFFSETS = {"이번달": 0, "이달": 0, "지난달": -1, "저번달": -1, "다음달": 1, "담달": 1}
# "주말" · "지난 주말" · "지난주 주말"
_WEEKEND_RE = re.compile(r"^(?:(?P<week>이번|금|다음|담|차|지지난|지난|저저번|저번|전)주?)?주말$")

_WEEKDAY_INDEX = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_WEEK_OFFSET = {
    "지지난주": -2,
    "저저번주": -2,
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
    r"^(?P<week>이번주|금주|다음주|담주|차주|지지난주|지난주|저저번주|저번주|전주)?(?P<day>[월화수목금토일])요일$"
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
# 구간을 여닫는 조사와 때를 가리키는 "에". "이틀 전에" · "어제부터"
_RANGE_TAIL_RE = re.compile(r"(?:부터|까지|에)$")

_PM_MERIDIEMS = {"오후", "저녁", "낮", "점심"}
_AM_MERIDIEMS = {"오전", "아침", "새벽"}


def today_of(now: datetime, tz: tzinfo) -> date:
    return now.astimezone(tz).date()


def resolve_date(
    value: str,
    *,
    today: date,
    direction: TemporalDirection = "nearest",
    anchor: DateRange | None = None,
) -> date:
    """하루를 가리키는 날짜 표현 또는 ISO 문자열을 확정 날짜로 바꾼다.
    명시적 연도는 그대로 사용하고, 연도 생략 월일·요일만 direction을 적용한다.

    `anchor` 는 기준의 기간이다("소풍 이틀 전에 열났어" 의 소풍, 여러 날이면 [첫날, 끝 다음 날)).
    있으면 표현을 거기서 센 거리로만 읽는다.
    없으면 기준은 말한 날(오늘)이다 — "이틀 전에 밥 먹었어"."""
    if not isinstance(value, str) or not value.strip():
        raise DateParseError("날짜 표현이 비어 있다.")

    # "어제부터 계속 기침" 의 "어제부터" 처럼 구간을 여는 표현은 그 날짜 하나로 본다.
    # (관찰은 하루 단위 — build_observed_range)
    text = _RANGE_TAIL_RE.sub("", _normalize(value)) or _normalize(value)
    key = text.replace(" ", "")

    if anchor is not None:
        return _from_anchor(text, anchor)

    iso = _try_iso_date(text)
    if iso is not None:
        _check_year(iso.year, text)
        return iso

    if key in _ANCHOR_WORDS:
        raise DateNeedsAnchor(f"'{text}' 는 무엇의 {text}인지 있어야 풀리는 날짜다.")
    if key in _DAY_OFFSETS:
        return today + timedelta(days=_DAY_OFFSETS[key])

    span = _span_offset(key)
    if span is not None:
        # 기준이 없으면 어디서든 오늘에서 센다. "전" · "뒤" 가 방향을 말하므로 direction 은
        # 보지 않는다. 지난 일의 "3일 뒤"("걸리고 3일 뒤에 나았어")는 미래 날짜가 되고,
        # 그건 계산이 아니라 관찰 저장의 검사가 막는다(tools/dates.py check_observed)
        return today + timedelta(days=span)

    resolved = _try_year_month_day(key)
    if resolved is not None:
        return resolved
    resolved = _try_weekday(key, today, direction)
    if resolved is not None:
        return resolved
    resolved = _try_month_day(key, today, direction)
    if resolved is not None:
        return resolved

    vague = _try_period(key, today, direction)
    if vague is not None:
        last = vague.end - timedelta(days=1)
        raise DateNeedsAsking(
            f"'{text}' 는 {vague.start.isoformat()}~{last.isoformat()} 중 "
            "어느 날인지 정해지지 않는다. "
            "날짜를 골라 넣지 말고 무슨 요일·며칠이었는지 보호자에게 묻는다."
        )
    if key in _VAGUE_DAYS:
        raise DateNeedsAsking(
            f"'{text}' 는 어느 날인지 정해지지 않는다. "
            "날짜를 골라 넣지 말고 언제였는지 보호자에게 묻는다."
        )

    raise DateParseError(f"해석할 수 없는 날짜 표현: {text!r}")


def _from_anchor(text: str, anchor: DateRange) -> date:
    """기준에서 센 날. 앞쪽("전날" · "이틀 전")은 첫날에서, 뒤쪽("다음날" · "사흘 뒤")은 마지막
    날에서 센다 — 10/1~10/3 수련회의 "다음날" 은 10/2 가 아니라 10/4 다."""
    offset = _relative_offset(text)
    if offset is None:
        raise DateParseError(
            f"기준이 있으면 날짜에는 기준에서 며칠 떨어졌는지만 넣는다. {text!r} 대신 "
            "'전날'·'이틀 전'·'3일 뒤' 처럼 넣는다. 기준이 아니면 기준을 빼고 다시 부른다."
        )
    last = anchor.end - timedelta(days=1)
    if offset < 0:
        return anchor.start + timedelta(days=offset)
    if offset > 0:
        return last + timedelta(days=offset)
    if anchor.start != last:
        raise DateNeedsAsking(
            f"기준이 {anchor.start.isoformat()}~{last.isoformat()} 여러 날이라 '{text}' 가 "
            "어느 날인지 정해지지 않는다. 며칠이었는지 보호자에게 묻는다."
        )
    return anchor.start


def _relative_offset(value: str) -> int | None:
    """기준일에서 며칠 떨어졌는지. "전날" -1, "당일" 0, "이틀 전" -2, "3일 뒤" +3.
    거리를 말하는 표현이 아니면 None — 기준일은 호출하는 쪽이 정한다."""
    key = _RANGE_TAIL_RE.sub("", _normalize(value)).replace(" ", "")
    if key in _ANCHOR_WORDS:
        return _ANCHOR_WORDS[key]
    return _span_offset(key)


def resolve_date_range(
    value: str,
    *,
    today: date,
    direction: TemporalDirection = "nearest",
    anchor: DateRange | None = None,
) -> DateRange:
    """조회용 날짜 표현을 [start, end) 범위로 바꾼다.
    연도·연월·상대연도·주·주말·달은 기간으로, 하루 표현은 1일 범위로 만든다.
    `anchor` 는 resolve_date 와 같다 — 있으면 하루 표현만 받는다."""
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

    if anchor is None:
        # 조회는 기간이면 된다. "지난주에 뭐 먹었지?" 를 무슨 요일이었는지 되묻지 않는다
        period = _try_period(_RANGE_TAIL_RE.sub("", key) or key, today, direction)
        if period is not None:
            return period

    day = resolve_date(text, today=today, direction=direction, anchor=anchor)
    return DateRange(start=day, end=day + timedelta(days=1))


def resolve_query_bound(
    value: str | None,
    *,
    today: date,
    direction: TemporalDirection,
    is_end: bool,
    anchor: DateRange | None = None,
) -> date | None:
    """조회 경계를 확정한다. 표현이 없으면 경계도 없다(None).

    resolve_date_range 는 [start, end) 열린 끝을 주는데 store 는 양쪽 닫힌 구간을 본다.
    그래서 끝 경계는 하루 당긴다. 덕분에 "2026년 9월" 하나로 월 전체를 가리킬 수 있다.

    direction 은 호출하는 쪽이 정한다 — 관찰 조회는 과거, 일정 조회는 미래가 기본이다.
    "금요일" 같은 표현이 지난 금요일인지 다가올 금요일인지는 계산으로 못 정한다.
    """
    if value is None:
        return None
    span = resolve_date_range(value, today=today, direction=direction, anchor=anchor)
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


def resolve_when(
    patch: WhenPatch, *, current: EventWhen | None, today: date, tz: tzinfo
) -> EventWhen:
    """발화에서 추출한 시각 표현을 기존 일정 구간에 반영한다.
    시작,종료,all_day를 함께 계산해 시간 구간의 일관성을 유지한다.

    원칙:
    - 날짜만 변경하면 기존 구간의 길이를 유지한 채 전체 구간을 이동한다.
      단 여러 날 일정의 시작 "시각"만 바꾼 경우에는 종료를 그대로 둔다.
    - all_day가 켜지면 00:00~23:59로 다시 계산한다.
    - all_day가 꺼지면 종일 일정에서 사용하던 종료 시각은 유지하지 않는다.
    - 종일 일정에 종료 시각이 오면 쓸 수 없으므로 check_when이 거부한다.
    - drop_end면 시작은 그대로 두고 종료만 없앤다. 종일 일정은 check_when이 거부한다.

    새 일정인 경우 current는 None
    """

    start_day = _start_day(patch, current, today=today, tz=tz)

    if _stays_all_day(patch, current):
        end_day = _all_day_end_day(patch, current, start_day, today=today, tz=tz)
        return EventWhen(
            starts_at=combine(start_day, ALL_DAY_START, tz),
            ends_at=combine(end_day, ALL_DAY_END, tz),
            all_day=True,
        )

    starts_at = combine(start_day, _start_time(patch, current, tz), tz)
    return EventWhen(
        starts_at=starts_at,
        ends_at=_end_at(patch, current, starts_at, tz=tz),
        all_day=False,
    )


def check_when(when: EventWhen, patch: WhenPatch) -> str | None:
    """구간이 성립하면 None, 아니면 모델에게 설명문을 돌려준다."""
    if patch.drop_end and (patch.ends_on is not None or patch.ends_time is not None):
        return (
            "끝을 지우라는 요청과 끝을 새로 정하는 값이 함께 왔다. 끝을 없앨 거면 ends_on·"
            "ends_time을 빼고, 새 끝을 정할 거면 지우기를 뺀다."
        )
    if when.all_day and patch.drop_end:
        return (
            "하루 종일 일정은 끝을 따로 지울 수 없다. 시각이 있는 일정으로 바꾸려면 "
            "starts_time에 시작 시각을 넣는다. 그러면 끝은 비워진다."
        )
    if when.all_day and patch.ends_time is not None:
        return (
            "하루 종일 일정은 시각을 갖지 않아 끝나는 시각을 쓸 수 없다. 시각이 있는 "
            "일정으로 바꾸려면 starts_time에 시작 시각도 함께 넣는다."
        )
    if when.ends_at is not None and when.ends_at <= when.starts_at:
        return (
            "끝나는 시각이 시작보다 앞서거나 같다. 자정을 넘기거나 여러 날 가는 일정이면 "
            "ends_on에 끝나는 날짜를 넣는다. 아니면 ends_time을 다시 확인한다."
        )
    return None


def _start_day(patch: WhenPatch, current: EventWhen | None, *, today: date, tz: tzinfo) -> date:
    if patch.starts_on is not None:
        return resolve_date(patch.starts_on, today=today, direction=patch.direction)
    if current is not None:
        return current.starts_at.astimezone(tz).date()
    raise DateParseError("일정의 시작 날짜가 없다.")


def _stays_all_day(patch: WhenPatch, current: EventWhen | None) -> bool:
    if is_all_day(patch.starts_time):
        return True
    if patch.starts_time is not None:
        return False  # 시각을 새로 말했으면 더 이상 하루 종일로 두지 않음
    return current.all_day if current is not None else False


def _all_day_end_day(
    patch: WhenPatch, current: EventWhen | None, start_day: date, *, today: date, tz: tzinfo
) -> date:
    """며칠짜리인지는 유지한 채 마지막 날만 옮긴다."""
    if patch.ends_on is not None:
        return resolve_date(patch.ends_on, today=today, direction=patch.direction)
    if current is None or current.ends_at is None:
        return start_day
    span = current.ends_at.astimezone(tz).date() - current.starts_at.astimezone(tz).date()
    return start_day + span


def _start_time(patch: WhenPatch, current: EventWhen | None, tz: tzinfo) -> time:
    """시작 시각. 모르면 자정으로 때우지 않고 MissingStartTime으로 되묻게 한다."""
    if patch.starts_time is None:
        # 시각을 말하지 않았으면 원래 시각 그대로
        if current is not None and not current.all_day:
            return current.starts_at.astimezone(tz).time()
        raise MissingStartTime("일정의 시작 시각이 없다.")

    try:
        moment = resolve_time(patch.starts_time)
    except DateParseError as exc:
        raise MissingStartTime(str(exc)) from exc  # "아침"/"낮" 은 시각이 아님
    if moment is None:
        raise MissingStartTime("일정의 시작 시각이 없다.")
    return moment


def _end_at(
    patch: WhenPatch,
    current: EventWhen | None,
    starts_at: datetime,
    *,
    tz: tzinfo,
) -> datetime | None:
    """ends_on(날짜)과 ends_time(시각)을 합쳐 종료 시각 하나로 만든다.
    빠진 쪽은 지어내지 않고 기존 값에서 가져온다.
    날짜는 기존 종료일(없으면 시작일), 시각은 기존 종료 시각.
    """
    if patch.drop_end:
        return None
    previous = _previous_end(current)
    if patch.ends_on is None and patch.ends_time is None:
        if previous is None or current is None:
            return None
        if patch.starts_on is None and _spans_days(current, tz):
            # 여러 날 일정의 마지막 날 종료는 첫날 시작 시각과 따로 보아야 한다
            return previous
        return starts_at + (previous - current.starts_at)  # 길이를 유지한 채 함께 이동

    if patch.ends_on is not None:
        # 끝나는 날짜는 오늘이 아니라 시작일 기준
        start_day = starts_at.astimezone(tz).date()
        day = resolve_date(patch.ends_on, today=start_day, direction="future")
    else:
        day = (previous or starts_at).astimezone(tz).date()

    if patch.ends_time is not None:
        moment = resolve_time(patch.ends_time)
    elif previous is not None:
        moment = previous.astimezone(tz).time()  # 날짜만 바꿨다고 시각을 자정으로 두지 않음
    else:
        moment = None
    if moment is None:
        raise MissingEndTime("일정이 몇 시에 끝나는지 없다.")

    end = combine(day, moment, tz)
    if patch.ends_on is None and moment == ALL_DAY_START and end <= starts_at:
        # 종료 자리의 자정은 그날이 아니라 다음 날 00:00
        end += timedelta(days=1)
    return end


def _spans_days(when: EventWhen, tz: tzinfo) -> bool:
    if when.ends_at is None:
        return False
    return when.ends_at.astimezone(tz).date() != when.starts_at.astimezone(tz).date()


def _previous_end(current: EventWhen | None) -> datetime | None:
    """종일 일정의 23:59 는 그 성격에서 나온 값이라, 시각 있는 일정이 되면 버린다."""
    if current is None or current.all_day:
        return None
    return current.ends_at


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


def _span_offset(key: str) -> int | None:
    """ "이틀전" -2, "3일뒤" +3, "일주일전" -7. 날수를 센 표현이 아니면 None."""
    matched = _SPAN_RE.match(key)
    if matched is None:
        return None
    days = _count_days(matched.group("amount"))
    if days is None:
        return None
    return -days if matched.group("side") == "전" else days


def _count_days(amount: str) -> int | None:
    if amount in _NATIVE_DAYS:
        return _NATIVE_DAYS[amount]
    matched = _DIGIT_DAYS_RE.match(amount)
    if matched is not None:
        return int(matched.group("days"))
    matched = _WEEKS_RE.match(amount)
    if matched is not None:
        weeks = matched.group("weeks")
        return (_WEEK_COUNTS[weeks] if weeks in _WEEK_COUNTS else int(weeks)) * 7
    return None


def _try_period(key: str, today: date, direction: TemporalDirection) -> DateRange | None:
    """하루보다 넓은 기간 — 주 · 주말 · 달. 주는 월요일에 시작한다."""
    monday = today - timedelta(days=today.weekday())
    if key in _WEEK_OFFSET:
        start = monday + timedelta(days=_WEEK_OFFSET[key] * 7)
        return DateRange(start=start, end=start + timedelta(days=7))
    matched = _WEEKEND_RE.match(key)
    if matched is not None:
        week = matched.group("week")
        if week:
            weeks = _WEEK_OFFSET[f"{week}주"]
        elif direction == "past" and today.weekday() < 5:
            # 화요일에 "주말에 키즈카페 갔어" 는 아직 안 온 이번 주말이 아니라 지난 주말이다
            weeks = -1
        else:
            # 앞으로의 일이거나 주말 당일이면 이번 주말 — 일요일에 말했으면 오늘이 든 그 주말
            weeks = 0
        saturday = monday + timedelta(days=weeks * 7 + 5)
        return DateRange(start=saturday, end=saturday + timedelta(days=2))
    if key in _MONTH_OFFSETS:
        index = today.year * 12 + today.month - 1 + _MONTH_OFFSETS[key]
        start = date(index // 12, index % 12 + 1, 1)
        after = index + 1
        return DateRange(start=start, end=date(after // 12, after % 12 + 1, 1))
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
