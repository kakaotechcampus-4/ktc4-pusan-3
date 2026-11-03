"""행사가 열리는 날 · 그날 장소 — 일정 원문(운영 시간 · 프로그램 · 개요)을 읽는다.

TourAPI 목록의 시작일 · 종료일은 전체 기간이다. "매주 토요일" · "월요일 휴무" · 날짜마다 장소가
바뀌는 행사는 기간만 보면 닫힌 날에 낸다 — 11-03 목록 104건 중 약 1/3 이 이런 일정이었고, 행사를
묻지 않은 "오늘 근처" 요청에서 낸 행사 3개가 다 이 때문에 틀렸다(토요일에만 하는 행사를 화요일에
냈다).

읽는 순서
1. 장소가 "○○ 전역" 이거나 운영 시간이 시각 없이 "상이" 뿐이면 모른다 — 어디서 · 언제 하는지
   원문으로 알 수 없다.
2. 일정 낱말(요일 · 휴무 · 날짜 · 회차 …)이 하나도 없으면 기간 안 매일이다. 모델을 부르지 않는다.
3. 있으면 모델이 원문을 구조로 옮긴다 — 옮기기만 하고 날짜 계산은 코드가 한다(루트 §3). 코드가 본다
   - 제한(요일 · 날짜 · 쉬는 요일)에는 근거 구절이 있고, 원문에 그대로 있다
   - 요일 · 몇째 주 값 · 날짜는 기간 안 · 장소는 원문에 있는 이름
   - 쉬는 요일 · 몇째 주 요일 · 그 요일만 여는 요일은 근거 구절에 요일로 적혀 있다
   - 날짜를 적었으면 요일은 버린다 · 매월 몇째 주와 같은 요일의 "매주" 는 버린다 · 쉬는 요일만
     적었으면 매일에서 그 요일을 뺀다
   - 제한인데 열리는 날이 없으면 모른다
4. 운영 시간 칸만 코드로 읽은 요일과 날마다 맞댄다. 둘 다 연다고 한 날만 연다.

🚨 모르는 날은 열리지 않는 날이다 — 그날 행사로 내지 않는다(관람 연령을 모르면 빼는 것과 같다).
   모델 호출 · 원문 조회가 실패해도 모른다. 기간으로 메우지 않는다.

캐시 — 같은 행사 · 같은 원문은 그날 한 번만 읽는다. 프로세스에 하나(`SHARED_CACHE`, `--workers 1`
전제 #134). 실패는 캐시하지 않는다 — 다음 조회에서 다시 읽는다.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.agents.activity.events import Event, every_day, in_period, schedule_days
from app.agents.activity.store.ports import EventSchedule, EventSource, UpstreamUnavailable
from app.agents.common.llm_client import LLMClient, LLMError
from app.core.event_raw import RawEventIntro

log = logging.getLogger(__name__)

# 모델 한 번을 기다리는 상한(초). 11-03 실측 p90 1.9s · 최대 3.3s(reasoning none). 넘으면 모른다 —
# 공용 LLM 상한(60s)까지 기다리면 run 시간 예산(20s)을 넘는다
READ_TIMEOUT_S = 8.0
# 장소 이름 길이 상한. 원문 문단을 장소로 옮겨 오지 않게 (잠정)
MAX_PLACE_CHARS = 40

UNKNOWN = EventSchedule(open_days=frozenset())

_WEEKDAYS = "월화수목금토일"
_W = f"[{_WEEKDAYS}]"
# 일정 낱말. 운영 시간 · 프로그램 · 개요에 하나도 없으면 기간 안 매일이다
_SCHEDULE_WORDS = re.compile(
    rf"{_W}요일|주말|평일|매주|격주|매월|매달|휴무|휴관|휴장|공연\s*없음|미운영|상이|회차|"
    rf"\(\s*{_W}\s*\)|\d{{1,2}}\s*월\s*\d{{1,2}}\s*일|"
    rf"\d{{1,2}}\s*[./]\s*\d{{1,2}}\s*\.?\s*\(\s*{_W}"
)
_CLOCK = re.compile(r"\d{1,2}:\d{2}")
# 운영 시간 칸의 요일 — 쉬는 요일 · 매주 X요일 · X요일~Y요일 · [X요일] · 첫머리 (X)
_CLOSED = re.compile(r"휴무|휴관|휴장|공연\s*없음|미운영|제외")
_EVERY = re.compile(rf"매주\s*((?:{_W}(?:요일)?\s*(?:[,·/및와과]|\s)\s*)*{_W}(?:요일)?)")
_RANGE = re.compile(rf"({_W})요일\s*(?:~|-|부터)\s*({_W})요일")
# 근거 구절이 요일을 적는 모양 — X요일 · (X) · X~Y · 금, 토, 일. "매일" 은 요일을 적은 것이 아니다
_NAMED_DAY = re.compile(rf"({_W})요일|\(\s*({_W})\s*\)")
_DAY_SPAN = re.compile(rf"({_W})(?:요일)?\s*(?:~|-|–|부터)\s*({_W})(?:요일)?")
_DAY_LIST = re.compile(rf"{_W}(?:\s*[,·/]\s*{_W})+")
_BRACKETED = re.compile(rf"\[\s*({_W})요일\s*\]")
_LEADING = re.compile(rf"\s*\(({_W})\)")
# 근거 구절 앞에 모델이 붙이는 칸 이름("운영 시간: …") — 원문에는 없다
_LABEL = re.compile(r"^\s*(?:행사 이름|행사 기간|장소|운영 시간|프로그램|개요)\s*[:：]\s*")
_NTH = frozenset({1, 2, 3, 4, 5, -1})
_ALL_DAYS = frozenset(range(7))

# 11-03 에 104건으로 잰 그대로다. 줄 끝 `\` 는 줄을 잇는다 — 글은 바뀌지 않는다
SYSTEM_PROMPT = """\
너는 행사 안내문에서 "그 행사에 가족이 찾아가면 열려 있는 날"만 뽑는다. \
날짜 계산은 하지 않는다 — 원문에 적힌 것을 구조로 옮긴다.

- 행사 전체(주 프로그램)의 일정만 본다. \
부속 프로그램(체험 · 투어 · 도슨트 · 부대 공연 · 아카데미 · 강연),
  개막식 · 폐막식, 예매 · 신청 · 접수 기간, \
요일별 운영 "시간"이 다른 것은 열리는 날을 제한하지 않는다.
- schedule
  - every_day: 행사 기간 안 매일 열린다. 쉬는 요일이 있으면 closed_weekdays 에 적는다.
  - restricted: 정해진 날에만 열린다. open_weekdays(그 요일만) · \
monthly(매월 n째 주 요일, 마지막 주는 -1) ·
    dates(그 날짜만) · venue_dates(장소마다 날짜가 다를 때) 중 맞는 것을 채운다. 여럿이면 합친다.
    closed_weekdays 는 그 위에서 뺀다.
  - unknown: 공연 · 회차마다 다르다고만 하고 날짜가 없거나, 일정은 홈페이지를 보라고만 할 때.
- 공휴일 · 명절 · 기상에 따른 휴무나 변경은 적지 않는다.
- 요일 숫자: 월0 화1 수2 목3 금4 토5 일6.
- 날짜는 YYYY-MM-DD. 연도는 행사 기간에서 정한다. 요일이 같이 적혀 있으면 그 요일에 맞는 날짜다
  (예: "10월 23일(금), 24일(토), 6일(금)" 의 6일은 11월 6일).
- evidence: 판단의 근거가 된 원문 구절을 고치지 말고 그대로(10~40자) 옮긴다. \
every_day 이고 제한 문구가 없으면 빈 목록.
- 원문끼리 어긋나면(목록의 기간과 개요의 날짜가 다르면) unknown."""

_FIELDS = (
    "schedule",
    "open_weekdays",
    "closed_weekdays",
    "monthly",
    "dates",
    "venue_dates",
    "evidence",
)
_INTS = {"type": "array", "items": {"type": "integer"}}
_STRINGS = {"type": "array", "items": {"type": "string"}}
RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "event_schedule",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": list(_FIELDS),
            "properties": {
                "schedule": {"type": "string", "enum": ["every_day", "restricted", "unknown"]},
                "open_weekdays": _INTS,
                "closed_weekdays": _INTS,
                "monthly": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["nth", "weekday"],
                        "properties": {"nth": {"type": "integer"}, "weekday": {"type": "integer"}},
                    },
                },
                "dates": _STRINGS,
                "venue_dates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["venue", "dates"],
                        "properties": {"venue": {"type": "string"}, "dates": _STRINGS},
                    },
                },
                "evidence": _STRINGS,
            },
        },
    },
}


class ScheduleCache:
    """하루 캐시 — 날이 바뀌면 비운다. 키는 행사 id 와 원문이라 원문이 바뀌면 다시 읽는다."""

    def __init__(self) -> None:
        self._day: date | None = None
        self._rows: dict[tuple[str, str], EventSchedule] = {}

    def get(self, day: date, key: tuple[str, str]) -> EventSchedule | None:
        if self._day != day:
            self._rows.clear()
            self._day = day
        return self._rows.get(key)

    def put(self, day: date, key: tuple[str, str], schedule: EventSchedule) -> None:
        if self._day == day:
            self._rows[key] = schedule


SHARED_CACHE = ScheduleCache()


class ReadEventSchedules:
    """`EventScheduleSource` 구현 — 행사 어댑터의 일정 원문을 run 안에서 읽는다.

    모델은 Activity 와 같은 역할 설정(`role="activity"`)을 쓴다. 넘기지 않으면 처음 부를 때 만든다 —
    일정 낱말이 없는 행사만 있으면 만들지 않는다.
    """

    def __init__(
        self,
        source: EventSource,
        *,
        client: LLMClient | None = None,
        cache: ScheduleCache | None = None,
    ) -> None:
        self._source = source
        self._client = client
        self._cache = cache or ScheduleCache()

    async def schedule_of(self, event: Event, *, today: date) -> EventSchedule:
        try:
            intro, overview = await asyncio.gather(
                self._source.intro(content_id=event.content_id),
                self._source.overview(content_id=event.content_id),
            )
        except UpstreamUnavailable:
            log.warning("행사 일정 원문을 못 읽었다 content_id=%s", event.content_id)
            return UNKNOWN
        text = _message(event, intro, overview)
        key = (event.content_id, text)
        if (cached := self._cache.get(today, key)) is not None:
            return cached
        if _vague(intro):
            schedule = UNKNOWN
        elif not _SCHEDULE_WORDS.search(" ".join((intro.playtime, intro.program, overview))):
            schedule = every_day(event, today)
        else:
            reply = await self._ask(text, content_id=event.content_id)
            if reply is None:
                return UNKNOWN
            source = (event.name, intro.place, intro.playtime, intro.program, overview)
            rules = _rules(reply, event, _norm(" ".join(source)))
            schedule = _resolve(rules, _playtime_rules(intro.playtime), event, today)
        self._cache.put(today, key, schedule)
        return schedule

    async def _ask(self, text: str, *, content_id: str) -> Any | None:
        """모델 답(JSON). 호출이 실패하거나 늦거나 JSON 이 아니면 None."""
        try:
            self._client = self._client or LLMClient(role="activity")
            response = await asyncio.wait_for(
                self._client.chat(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": text},
                    ],
                    response_format=RESPONSE_FORMAT,
                ),
                READ_TIMEOUT_S,
            )
            return json.loads(response.message.content or "")
        except (LLMError, TimeoutError, ValueError) as exc:
            log.warning(
                "행사 일정을 모델로 못 읽었다 content_id=%s error=%s",
                content_id,
                type(exc).__name__,
            )
            return None


@dataclass(frozen=True)
class _Rules:
    """원문에서 옮긴 일정. 그날 여는지는 `opens` 가 계산한다."""

    every_day: bool
    open_weekdays: frozenset[int] = frozenset()
    closed_weekdays: frozenset[int] = frozenset()
    monthly: tuple[tuple[int, int], ...] = ()  # (몇째 주, 요일). 마지막 주는 -1
    dates: frozenset[date] = frozenset()
    places: tuple[tuple[date, str], ...] = ()  # 장소마다 다른 날

    def opens(self, day: date) -> bool:
        if day.weekday() in self.closed_weekdays:
            return False
        if self.every_day:
            return True
        return (
            day.weekday() in self.open_weekdays
            or day in self.dates
            or any(when == day for when, _ in self.places)
            or any(weekday == day.weekday() and _nth(day, n) for n, weekday in self.monthly)
        )


def _message(event: Event, intro: RawEventIntro, overview: str) -> str:
    """모델에 주는 원문. 행사 공개 정보뿐이다 — 아이 · 보호자 정보는 이 호출에 없다."""
    return (
        f"행사 이름: {event.name}\n"
        f"행사 기간: {event.starts_on.isoformat()} ~ {event.ends_on.isoformat()}\n"
        f"장소: {intro.place}\n"
        f"운영 시간: {intro.playtime}\n"
        f"프로그램: {intro.program}\n"
        f"개요: {overview}"
    )


def _vague(intro: RawEventIntro) -> bool:
    """어디서 · 언제 하는지 원문으로 알 수 없다 — "○○ 전역" · 시각 없이 "회차별 상이"."""
    return "전역" in intro.place or ("상이" in intro.playtime and not _CLOCK.search(intro.playtime))


def _rules(reply: Any, event: Event, source: str) -> _Rules | None:
    """모델 답을 검증해 규칙으로. 못 넘으면 None — 모른다. `source` 는 정규화한 원문이다."""
    if not isinstance(reply, dict) or not set(_FIELDS) <= reply.keys():
        return None
    kind = reply["schedule"]
    if kind not in ("every_day", "restricted"):
        return None
    try:
        opened = _weekdays(reply["open_weekdays"])
        closed = _weekdays(reply["closed_weekdays"])
        monthly = tuple((_int(item["nth"]), _weekday(item["weekday"])) for item in reply["monthly"])
        listed = [_text(day) for day in reply["dates"]]
        venues = [
            (_text(item["venue"]).strip(), [_text(day) for day in item["dates"]])
            for item in reply["venue_dates"]
        ]
        quotes = [_text(quote) for quote in reply["evidence"]]
    except (KeyError, TypeError, ValueError):
        return None
    if any(n not in _NTH for n, _ in monthly):
        return None
    if (kind == "restricted" or closed) and not _quoted(quotes, source):
        return None
    if any(
        not name or len(name) > MAX_PLACE_CHARS or _norm(name) not in source for name, _ in venues
    ):
        return None
    dates = frozenset(day for day in map(_day, listed) if day and in_period(event, (day,)))
    places = sorted(
        (
            (day, name)
            for name, days in venues
            for day in map(_day, days)
            if day and in_period(event, (day,))
        ),
        key=lambda pair: pair[0],
    )
    # 날짜를 적었으면 요일보다 좁다 — 날짜 목록에 "토 · 일" 을 같이 적으면 날짜 밖 주말도 연다
    # (11-03 실측). 매월 몇째 주 X요일을 "매주 X요일" 로도 적는다 — 몇째 주가 더 좁다
    if dates or places:
        opened.clear()
    opened -= {weekday for _, weekday in monthly}
    # 요일 숫자를 하나 밀려 적는다("월요일은 쉰다" 를 1 로, 11-03 실측). 이름 붙인 요일은 근거
    # 구절에 요일로 적혀 있거나, 근거가 적은 요일의 나머지여야 한다("화요일부터 일요일까지" 의
    # 월요일). 쉬는 요일을 적었으면 여는 요일은 그 나머지라 보지 않는다
    quoted = _quoted_weekdays(quotes)
    groups = [closed, {weekday for _, weekday in monthly}]
    if kind == "restricted" and not closed:
        groups.append(opened)
    if not all(days <= quoted or days == _ALL_DAYS - quoted for days in groups):
        return None
    if kind == "restricted" and not (opened or monthly or dates or places):
        if not closed:
            return None
        kind = "every_day"  # 쉬는 요일만 적었다 — 매일에서 그 요일을 뺀다
    return _Rules(
        every_day=kind == "every_day",
        open_weekdays=frozenset(opened),
        closed_weekdays=frozenset(closed),
        monthly=monthly,
        dates=dates,
        places=tuple(places),
    )


def _playtime_rules(playtime: str) -> _Rules | None:
    """운영 시간 칸만 코드로 읽은 요일. 요일이 없으면 None — 맞댈 것이 없다."""
    text = " ".join(playtime.split())
    closed: set[int] = set()
    for found in _CLOSED.finditer(text):
        near = text[max(0, found.start() - 25) : found.end() + 15]
        closed |= {_WEEKDAYS.index(day) for day in re.findall(rf"({_W})요일", near)}
    opened: set[int] = set()
    for found in _EVERY.finditer(text):
        if not _CLOSED.search(text[found.end() : found.end() + 12]):
            opened |= {_WEEKDAYS.index(day) for day in re.findall(_W, found[1])}
    for found in _RANGE.finditer(text):
        opened |= _span(found[1], found[2])
    opened |= {_WEEKDAYS.index(day) for day in _BRACKETED.findall(text)}
    if leading := _LEADING.match(text):
        opened.add(_WEEKDAYS.index(leading[1]))
    # "평일 · 주말" 시간표처럼 일곱 요일이 다 나오면 매일이다
    restricted = bool(opened) and opened != set(range(7))
    if not (closed or restricted):
        return None
    return _Rules(
        every_day=not restricted,
        open_weekdays=frozenset(opened) if restricted else frozenset(),
        closed_weekdays=frozenset(closed),
    )


def _resolve(
    rules: _Rules | None, playtime: _Rules | None, event: Event, today: date
) -> EventSchedule:
    """오늘부터 창 안에서 열리는 날. 운영 시간 칸을 읽었으면 둘 다 연다고 한 날만이다."""
    if rules is None:
        return UNKNOWN
    open_days = frozenset(
        day
        for day in schedule_days(today)
        if in_period(event, (day,))
        and rules.opens(day)
        and (playtime is None or playtime.opens(day))
    )
    return EventSchedule(open_days=open_days, day_places=rules.places)


def _quoted(quotes: list[str], source: str) -> bool:
    """근거 구절이 하나 이상 있고 모두 원문에 그대로 있다(띄어쓰기 · 문장부호는 보지 않는다)."""
    keys = [_norm(_LABEL.sub("", quote)) for quote in quotes]
    return bool(keys) and all(key and key in source for key in keys)


def _quoted_weekdays(quotes: list[str]) -> set[int]:
    """근거 구절이 요일로 적은 날 — X요일 · (X) · X~Y · 금, 토, 일 · 매주 X · 평일 · 주말."""
    text = " ".join(quotes)
    days = {_WEEKDAYS.index(a or b) for a, b in _NAMED_DAY.findall(text)}
    for first, last in _DAY_SPAN.findall(text):
        days |= _span(first, last)
    # 요일 이름 없이 적은 목록("금, 토, 일" · "매주 토")은 "요일" 을 떼고 본다 — "토요일" 의 "일" 은
    # 일요일이 아니다
    bare = text.replace("요일", "")
    for found in (*_DAY_LIST.finditer(bare), *_EVERY.finditer(bare)):
        days |= {_WEEKDAYS.index(day) for day in re.findall(_W, found[0])}
    if "평일" in text:
        days |= {0, 1, 2, 3, 4}
    if "주말" in text:
        days |= {5, 6}
    return days


def _span(first: str, last: str) -> set[int]:
    """X요일부터 Y요일까지의 요일. 주를 넘어가도 된다(토~월)."""
    start, end = _WEEKDAYS.index(first), _WEEKDAYS.index(last)
    return {(start + offset) % 7 for offset in range((end - start) % 7 + 1)}


def _nth(day: date, n: int) -> bool:
    """그날이 그 달의 n째 주 같은 요일인가. -1 은 마지막 주다."""
    if n == -1:
        return (day + timedelta(days=7)).month != day.month
    return (day.day - 1) // 7 + 1 == n


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text).casefold()


def _day(text: str) -> date | None:
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def _weekdays(values: Any) -> set[int]:
    return {_weekday(value) for value in values}


def _weekday(value: Any) -> int:
    weekday = _int(value)
    if not 0 <= weekday <= 6:
        raise ValueError("요일 값이 아니다")
    return weekday


def _int(value: Any) -> int:
    if type(value) is not int:
        raise TypeError("정수가 아니다")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise TypeError("글자가 아니다")
    return value
