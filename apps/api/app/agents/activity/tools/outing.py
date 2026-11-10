"""날씨 · 일정 · 장소 · 행사 조회 (모델 tool 4개).

실패는 기본값으로 메우지 않는다 — 날씨 실패는 "맑음"이 아니다 (D8).
"""

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from datetime import date, datetime, time, timedelta
from time import perf_counter
from typing import Any, TypeVar

from app.agents.activity.areas import (
    find_area,
    find_province,
    leads_with_area,
    mentions_area,
    named_area,
    said,
)
from app.agents.activity.context import ActivityContext
from app.agents.activity.events import (
    Event,
    age_allows,
    distance_m,
    ended_today,
    in_period,
    in_province,
    open_on,
    period_tier,
    places_on,
    read_event,
)
from app.agents.activity.result import ErrorCode, ToolResult, fail, ok
from app.agents.activity.rules import asks_outing, shown_again
from app.agents.activity.schemas.common import PlaceCategory
from app.agents.activity.schemas.outing import (
    LookupScheduleArgs,
    LookupWeatherArgs,
    SearchEventsArgs,
    SearchNearbyPlacesArgs,
)
from app.agents.activity.store.ports import (
    EVENT_INTRO_LIMIT,
    EVENT_MAX_RADIUS_M,
    EVENT_TOP_K,
    PLACE_MAX_RADIUS_M,
    PLACE_TOP_K,
    CoarseLocation,
    EventRow,
    EventSchedule,
    ScheduleBlock,
    UpstreamUnavailable,
)
from app.agents.activity.weather import (
    WEATHER_NOT_ASKED,
    WEATHER_UNCHECKED,
    WeatherBrief,
    judge_weather,
    not_asked,
    parse_uv,
    read_advisories,
    read_air,
    read_forecast,
)
from app.agents.activity.when import PastDay
from app.agents.activity.when import days_of as _when_days
from app.agents.common.datetime_rules import DateParseError
from app.rules.holidays import holidays_known, is_public_holiday
from app.rules.sun import sun_times

logger = logging.getLogger(__name__)

# 위치가 없을 때 일몰을 계산하는 고정 기준점(대전 부근, 국토 가운데). 보호자 위치가 아니다
SUNSET_REFERENCE = (36.35, 127.38)

# 바깥 활동이 어려운 날(outdoor_ok=False)에도 찾을 수 있는 종류. 체험관(experience_center)은
# 박물관 · 과학관 · 체험관이다 — 적재하지 않고 TourAPI 시 · 도 목록에서 찾는다
# (`activity/culture.py`)
INDOOR_CATEGORIES = frozenset(
    {PlaceCategory.LIBRARY, PlaceCategory.INDOOR_PLAYGROUND, PlaceCategory.EXPERIENCE_CENTER}
)

# 비는 시간을 계산하는 하루의 범위. 아이가 밖에서 놀 수 있는 시간대로 둔 잠정값이다 — eval 로
# 조정한다. 오늘이면 지금 시각부터 센다
DAY_START = time(8)
DAY_END = time(20)
# 끝 시각이 없는 일정이 차지한다고 보는 길이. 비는 시간을 넉넉히 잡지 않는 쪽으로 둔다 (잠정)
OPEN_ENDED_BLOCK = timedelta(hours=1)

# 일정 읽기(행사마다 모델 호출)를 기다리는 상한 · run 마감 전에 마지막 후보를 낼 몫 (잠정 — eval).
# 11-10 실측: 캐시가 빈 첫 run 의 행사 조회 4 ~ 7초, 가장 느린 한 건이 시간을 정한다
EVENT_READ_BUDGET_S = 4.0
FINAL_TURN_RESERVE_S = 6.0
# 미리 읽는 행사 — 오늘부터 이 날 수 안에 시작하는 것만 (주말 · 다음 주까지)
PREFETCH_DAYS = 14
# 상한을 넘겨 뒤에서 이어 가는 일정 읽기 — 끝날 때까지 붙든다(가비지로 사라지지 않게)
_BACKGROUND: set["asyncio.Task[EventSchedule]"] = set()

_PLACES = "search_nearby_places"
_EVENTS = "search_events"
_WEATHER = "lookup_weather"
_SCHEDULE = "lookup_schedule"
_WEEKDAYS = "월화수목금토일"

_T = TypeVar("_T")


def day_of(when: str, today: date) -> date:
    """표현이 가리키는 첫날. 날짜는 코드가 푼다(`when.days_of`, #800)."""
    return days_of(when, today)[0]


async def outdoor_on(context: ActivityContext, label: str) -> bool:
    """그 라벨의 날에 바깥 활동을 할 수 있는가. 출력 검증과 장소 조회가 같이 쓴다 (#377).

    - 이미 판정한 날(사전 조회 · `lookup_weather`)은 `state.weather` 를 다시 쓰고, 없으면 여기서
      판정해 넣는다 — 모델이 `lookup_weather` 를 안 불렀어도 그날 판정으로 거른다.
    - 이번 주말은 토 · 일 중 **하루라도** 되면 연다. 보호자가 둘 중 하루를 고르는 일이라, 둘 다
      맑아야 열면 주말 나들이가 너무 자주 막힌다. 되는 날은 모델이 reason 에 쓴다.
    - 날씨를 못 읽은 날도 바깥을 막지 않는다 — 판정은 `check_weather` 가 한다.
    """
    days = days_of(label, context.today)
    missing = [day for day in days if day not in context.state.weather]
    if missing:
        briefs = await asyncio.gather(*(check_weather(context, day) for day in missing))
        context.state.weather.update(zip(missing, briefs, strict=True))
    return any(context.state.weather[day].outdoor_ok for day in days)


def days_of(when: str, today: date) -> tuple[date, ...]:
    """표현이 가리키는 날들 — 보호자가 쓴 표현 그대로 받아 코드가 푼다(`when.days_of`, #800).

    이번 주말은 토 · 일 이틀이다 — 주말 나들이는 둘 중 하루를 고르는 일이라 날마다 따로 본다.
    오늘이 일요일이면 오늘 하루다. 못 읽거나 지난 날이면 `DateParseError` 다 — tool 은
    `resolve_days` 로 걸러 모델에게 다시 쓰게 한다.
    """
    return _when_days(when, today)


def resolve_days(context: ActivityContext, when: str, tool: str) -> tuple[date, ...] | ToolResult:
    """tool 인자의 날 표현을 날짜로. 못 읽으면 모델이 고쳐 다시 부르게 INVALID_ARGS 로 돌려준다.

    보호자에게 되묻지 않는다 — 표현을 고쳐 쓰는 일은 모델이 한다.
    """
    try:
        return days_of(when, context.today)
    except PastDay:
        return fail(
            "query",
            tool,
            ErrorCode.INVALID_ARGS,
            f"'{when}' 은 이미 지난 날이다. 오늘 이후의 날을 쓴다. "
            "보호자가 날을 말하지 않았으면 오늘이다.",
        )
    except DateParseError:
        return fail("query", tool, ErrorCode.INVALID_ARGS, when_remedy(when))


def when_remedy(when: str) -> str:
    """못 읽은 날 표현을 고쳐 쓰는 법. 출력 tool 도 같은 문장을 쓴다."""
    return (
        f"'{when}' 을 날짜로 읽지 못했다. 보호자가 쓴 표현을 오늘 · 내일 · 이번 주말 · "
        "다음 주 토요일 · 11월 20일 같은 꼴로 바꿔 다시 쓴다. 날짜를 계산해 적지 않는다."
    )


def dark_now(context: ActivityContext) -> bool:
    """지금 해가 졌는가. 놀이터만 오늘 후보에서 뺀다 — 어두우면 다칠 수 있다 (#800).

    공원 · 실내 장소 · 행사는 막지 않는다. 밤에 하는 축제도 있고 공원은 보호자와 같이 걷는다.
    """
    return _after_sunset(context, context.today)


async def check_weather(context: ActivityContext, day: date) -> WeatherBrief:
    """그날의 야외 판정. run() 의 사전 조회와 `lookup_weather` 가 같이 쓴다.

    - 예보 · 대기질 · 자외선 · 특보를 병렬로 부른다. 하나가 실패해도 나머지는 쓴다 — 실패한
      조회만 None 으로 `judge_weather` 에 넘기고, 판정 규칙이 실내만 · 고지를 정한다 (D8).
    - 위치나 날씨 포트가 없으면 조회하지 않는다. 실패가 아니라 "묻지 않은 날"이라 바깥 놀이를
      막지 않고 확인을 보호자에게 맡긴다(`not_asked`). 조회했는데 실패한 날도 바깥은 막지 않고
      "확인하지 못했어요" 를 붙인다(`judge_weather`).
    - 위치는 외출 기준점(`context.outing_location`)이다 — 보호자가 지역을 말했으면 그 지역 날씨다.
    - 🚨 대기질은 오늘만 본다. 에어코리아는 지금 측정값만 주고 예보가 아니다 — 내일 추천에 오늘
      미세먼지를 쓰지 않고 "확인 못 함"으로 둔다. 특보는 지금 발효 중인 것을 그날에도 본다
      (더 막는 쪽).
    - 해가 졌는지는 날씨 판정이 아니다 — 바깥 활동을 막지 않고 오늘 놀이터만 뺀다(`dark_now`, #800).
    """
    weather = context.ports.weather
    grid = context.grid
    if weather is None or grid is None:
        return not_asked()

    today = day == context.today
    forecast, air, uv, advisories = await asyncio.gather(
        _or_none(weather.forecast(grid=grid, day=day)),
        _or_none(weather.air_quality(grid=grid)) if today else _none(),
        _or_none(weather.uv(grid=grid, day=day)),
        _or_none(weather.advisories(grid=grid)),
    )
    return judge_weather(
        forecast=read_forecast(forecast) if forecast is not None else None,
        air=read_air(air) if air is not None else None,
        uv_index=parse_uv(uv),
        advisories=read_advisories(advisories) if advisories is not None else None,
    )


async def lookup_weather(context: ActivityContext, args: LookupWeatherArgs) -> ToolResult:
    """라벨의 날마다 야외 판정과 등급 라벨을 돌려준다.

    - args.day 를 날짜로 바꾼다(`days_of`). 모델은 날짜를 계산하지 않는다. 이번 주말은 토 · 일을
      날마다 따로 판정한다 — 토요일 비가 일요일 야외를 막거나, 토요일 맑음이 일요일 비를 가리지
      않게.
    - 판정은 `check_weather` 가 4-2 표로 한다.
    - 모델에게는 판정(`outdoor_ok`)과 등급 라벨만 준다. raw 수치는 주지 않는다 —
      모델이 자기 기준으로 재해석해 "나쁨이지만 잠깐이면 괜찮아요"를 쓴다.
    - 화면에 붙는 안내(문구 키)는 모델에게 주지 않고 `context.state.weather` 에 날짜별로 둔다.
    - 미세먼지만 실패하면 야외는 허용하되 "미세먼지는 확인하지 못했어요" 를 싣는다.
    - 날씨를 못 읽은 날은 `checked=False` 다. 모든 날을 못 읽으면 UPSTREAM_ERROR. "맑음"으로
      가정하지 않지만 바깥을 막지도 않는다 — 모델은 날씨 이야기만 쓰지 않는다.
    """
    days = resolve_days(context, args.day, _WEATHER)
    if isinstance(days, ToolResult):
        return days
    briefs = await asyncio.gather(*(check_weather(context, day) for day in days))
    context.state.weather.update(zip(days, briefs, strict=True))
    # 묻지 않은 날(위치 · 포트 없음)도 확인한 날이 아니다 — 이 tool 은 열리지 않았어야 한다.
    # 못 읽은 것이 있어도 막힌 날은 확인한 날로 준다 — "바깥 어려움" 은 읽은 신호로 난 판정이다
    checked = [not {WEATHER_UNCHECKED, WEATHER_NOT_ASKED} & set(brief.notices) for brief in briefs]
    if not any(checked):
        return fail(
            "query",
            _WEATHER,
            ErrorCode.UPSTREAM_ERROR,
            "날씨를 확인하지 못했다. 맑다고 가정하지 않고 날씨 이야기를 쓰지 않는다. "
            "바깥 활동도 낼 수 있다.",
        )
    return ok(
        "query",
        _WEATHER,
        days=[
            {
                "date": day.isoformat(),
                "weekday": _WEEKDAYS[day.weekday()],
                "checked": ok_day,
                **brief.to_model_payload(),
            }
            for day, brief, ok_day in zip(days, briefs, checked, strict=True)
        ],
    )


async def lookup_schedule(context: ActivityContext, args: LookupScheduleArgs) -> ToolResult:
    """아이 일정과 비는 시간을 돌려준다. 읽기 전용이다.

    - args.day 를 날짜로 바꾼다(`days_of`). 모델은 날짜를 계산하지 않는다.
    - 날마다 평일 · 주말 · 공휴일을 가른다. 공휴일은 `app/rules/holidays.py` 상수로 본다 —
      아이 일정에는 어린이집 휴원일이 없어서, 이걸 모르면 공휴일 오전을 "어린이집 가 있는 시간"으로
      읽는다. 상수를 넣어 두지 않은 해는 주말만 보고 로그에 남긴다.
    - 비는 시간은 코드가 계산한다(`DAY_START` ~ `DAY_END`, 오늘이면 지금부터). 일정 충돌은
      모델에게 맡기지 않는다 (루트 CLAUDE.md §2).
    - 일정 제목은 싣지 않는다. 시각만 준다 — 포트도 제목을 주지 않는다.
    """
    days = resolve_days(context, args.day, _SCHEDULE)
    if isinstance(days, ToolResult):
        return days
    blocks = await context.ports.schedule.blocks(
        child_id=context.child_id, date_from=days[0], date_to=days[-1]
    )
    local_now = context.now.astimezone(context.timezone)
    return ok(
        "query",
        _SCHEDULE,
        days=[_day_schedule(day, blocks, context, local_now) for day in days],
    )


async def search_nearby_places(
    context: ActivityContext, args: SearchNearbyPlacesArgs
) -> ToolResult:
    """근처 장소를 종류로 찾는다. 장소 어댑터가 있으면 열린다 — 월령으로는 닫지 않는다.

    - 기준점은 보호자 위치다. 보호자가 지역을 말했으면(args.area) 그 지역 대표 지점이고, 한 번
      찾으면 이 run 내내 그 지점이다(`_origin`). 둘 다 없으면 INVALID_ARGS — 기본 좌표로
      대신하지 않는다 (4-3).
    - 기준점 · args.category · 반경만 포트에 넘긴다. 포트는 적재한 place 테이블에서 거리를
      계산한다 — 외부 API 를 부르지 않는다 (D9).
      🚨 검색 조건은 닫힌 enum 하나와 기준점이다. 모델이 만든 문자열로 찾지 않는다 — 지역 이름은
      코드가 대표 지점으로 바꾸고, 보호자가 쓴 글자 안에 없으면 받지 않는다.
    - 바깥 활동이 어려운 날은 실내 종류만 찾는다. 바깥 종류를 고르면 INVALID_ARGS 이고
      모델이 실내 종류로 고쳐 다시 부른다. 그날은 args.day 다 — 오늘 비가 와도 주말 공원은
      찾을 수 있다 (#377). 지역을 찾았으면 그 지역 날씨로 본다.
    - 가까운 순 `PLACE_TOP_K` 곳의 이름 · 종류만 싣는다. 🚨 거리는 모델에 주지 않는다 —
      이름 여러 곳과 거리를 같이 주면 위치를 거꾸로 짐작할 수 있고, 약관(draft-1 제8조 ③)이
      인공지능에 전달된다고 적은 것은 장소 이름뿐이다. 정렬은 여기서 끝낸다.
      돌려준 장소는 `context.state.seen_places` 에 적는다 — 출력 검증이 place_name 을 이 표와
      대조한다.
    - 실패하면 UPSTREAM_ERROR. 모델은 장소가 필요 없는 활동만 낸다.
    - 장소 행은 근거(suggestion_evidence)에 넣지 않는다. 날씨처럼 필터 조건이다.
    """
    if context.state.gate is None:
        raise RuntimeError("Gate 없이 장소 조회가 불렸다 — run() 이 먼저 만든다")

    origin = _origin(context, args.area)
    if isinstance(origin, ToolResult):
        return origin

    # ToolArgs 는 use_enum_values 라 args.category 가 글자("park")로 온다. 포트에는 enum 으로 넘긴다
    category = PlaceCategory(args.category)
    days = resolve_days(context, args.day, _PLACES)
    if isinstance(days, ToolResult):
        return days
    if category not in INDOOR_CATEGORIES and not await outdoor_on(context, args.day):
        indoor = " · ".join(sorted(INDOOR_CATEGORIES))
        return fail(
            "query",
            _PLACES,
            ErrorCode.INVALID_ARGS,
            f"그날은 바깥 활동이 어려운 날이라 실내 장소만 찾을 수 있다. {indoor} 중에서 고른다.",
        )
    if category == PlaceCategory.PLAYGROUND and days == (context.today,) and dark_now(context):
        # 오늘 하루를 물었고 해가 졌다 — 놀이터만 뺀다. 공원 · 실내는 그대로 (#800)
        context.state.dark_skipped = True
        return fail(
            "query",
            _PLACES,
            ErrorCode.INVALID_ARGS,
            "지금은 해가 져서 오늘 놀이터는 찾지 않는다. 공원이나 실내 장소를 고른다.",
        )

    try:
        rows = await context.ports.places.nearby(
            location=origin, category=category, radius_m=PLACE_MAX_RADIUS_M
        )
    except UpstreamUnavailable:
        return fail(
            "query",
            _PLACES,
            ErrorCode.UPSTREAM_ERROR,
            "장소를 찾지 못했다. 장소가 필요 없는 놀이만 낸다.",
        )

    nearest = sorted(rows, key=lambda row: row.distance_m)[:PLACE_TOP_K]
    for row in nearest:
        # 이름이 같은 곳(체인 키즈카페 등)은 먼저 본 가까운 곳을 남긴다
        context.state.seen_places.setdefault(row.name, row)
        if context.state.area is not None:
            context.state.area_places.add(row.name)
    return ok(
        "query",
        _PLACES,
        places=[{"name": row.name, "category": row.category.value} for row in nearest],
    )


async def search_events(context: ActivityContext, args: SearchEventsArgs) -> ToolResult:
    """그날 열리는 축제 · 공연 · 행사를 가까운 순으로 찾는다. 행사 어댑터가 있으면 열린다.

    - 기준점은 장소 조회와 같다(`_origin`) — 보호자가 말한 지역, 이 run 의 지역, 보호자 위치 순.
      한 run 에 한 지역이라 장소와 행사의 기준점이 갈리지 않는다.
    - 🚨 어댑터에는 위치를 주지 않는다. 오늘 이후에 끝나는 전국 목록을 받아(어댑터가 하루 캐시)
      거리는 여기서 계산한다 — 보호자 위치가 외부 API 로 나가지 않는다 (4-3).
    - 행사는 바깥 나들이로 본다. 그날(args.day) 바깥 활동이 어려우면 찾지 않는다 — 실내 공연도
      오가는 길과 줄이 바깥이다. 이번 주말은 바깥이 되는 날에 열리는 행사만 찾는다.
    - 못 읽는 행 · 온라인 · 술 행사는 빼고(`events.read_event`), 기간으로 먼저 거른 뒤 반경 안
      가까운 순으로 소개정보를 `EVENT_INTRO_LIMIT` 건까지 물어 관람 연령으로 거른다
      (`events.age_allows`). 소개정보를 못 읽은 행사는 뺀다 — 연령을 모르는 채 아이에게 내지 않는다.
    - 🚨 남은 행사의 일정을 읽어(`event_schedules`) 그날 열리는 행사만 남긴다. 기간은 전체 기간이라
      "매주 토요일" 을 모른다. 일정을 모르는 날은 열리지 않는 날이다. 일정 읽기는 모델 호출이라
      연령으로 빠진 행사에는 부르지 않는다.
    - 가까운 순 `EVENT_TOP_K` 건의 이름 · 종류 · 기간 · 그날 장소 · 요청한 날 중 열리는 날만 싣는다.
      장소가 날마다 다른 행사는 그날 장소다 — 다른 날 장소를 주면 모델이 그 장소로 쓴다.
      🚨 거리 · 요금 · 연락처 · 링크는 모델에 주지 않는다. 돌려준 행사는 `context.state.seen_events`
      에 적는다 — 출력 검증이 place_name 을 이 표와 대조하고, 후보의 날에 열리는지 다시 본다.
      지역에서 찾았으면 "근처" 금지 목록(`area_places`)에도 넣는다.
    - 실패하면 UPSTREAM_ERROR. 모델은 행사 없이 낸다.
    - 행사 행은 근거(suggestion_evidence)에 넣지 않는다. 장소처럼 필터 조건이다.
    """
    gate = context.state.gate
    events = context.ports.events
    schedules = context.ports.event_schedules
    if gate is None or events is None or schedules is None:
        raise RuntimeError(
            "Gate · 행사 어댑터 · 일정 포트 없이 행사 조회가 불렸다 — registry 가 열지 않는다"
        )

    origin = _origin(context, args.area, tool=_EVENTS, provinces=True)
    if isinstance(origin, ToolResult):
        return origin

    asked = resolve_days(context, args.day, _EVENTS)
    if isinstance(asked, ToolResult):
        return asked
    await outdoor_on(context, args.day)
    weather = context.state.weather
    days = [day for day in asked if weather[day].outdoor_ok]
    if not days:
        return fail(
            "query",
            _EVENTS,
            ErrorCode.INVALID_ARGS,
            "그날은 바깥 활동이 어려운 날이라 행사를 찾지 않는다. 다시 찾지 않고 행사 없이 낸다.",
        )

    try:
        raws = await events.events(ending_on_or_after=context.today)
    except UpstreamUnavailable:
        return fail(
            "query",
            _EVENTS,
            ErrorCode.UPSTREAM_ERROR,
            "행사를 찾지 못했다. 행사 없이 낸다.",
        )

    # 시 · 도만 말했으면 반경이 아니라 그 시 · 도의 행사다. 정렬은 대표 지점에서 가까운 순이다
    area = context.state.area
    province = area.province if area is not None and area.whole_province else None
    found: list[tuple[int, Event]] = []
    for raw in raws:
        event = read_event(raw)
        if event is None or not in_period(event, days):
            continue
        distance = distance_m(event, origin)
        if in_province(event, province) if province else distance <= EVENT_MAX_RADIUS_M:
            found.append((distance, event))
    # 앞 턴에서 보여 준 행사는 빼고 그다음 행사를 준다 — "다른 행사" 를 여러 번 물어도 (#852)
    fresh = [
        pair for pair in found if not shown_again("", pair[1].name, context.state.shown_before)
    ]
    if found and not fresh:
        return _no_new_events(context)
    nearest = sorted(fresh, key=lambda pair: _event_order(context, *pair))[:EVENT_INTRO_LIMIT]
    intros = await asyncio.gather(
        *(_or_none(events.intro(content_id=event.content_id)) for _, event in nearest)
    )
    allowed = [
        (distance, event, intro)
        for (distance, event), intro in zip(nearest, intros, strict=True)
        if intro is not None and age_allows(intro.age_limit, gate.stage.months)
    ]
    # 일정 읽기(모델 호출)는 상한 안에 끝난 행사만 이번에 쓴다 — 첫 run 이 20초를 넘지 않게.
    # 못 끝낸 읽기는 멈추지 않고 이어서 캐시를 채운다(다음 run 부터 나온다)
    found_schedules = await _read_schedules(
        context, [event for _, event, _ in allowed], budget=_read_budget(context)
    )
    # 상한에 걸려 이번에 못 쓴 행사 수 — 결과가 비었을 때 "더 없음" 과 "확인 중" 을 가른다
    pending = found_schedules.count(None)
    allowed = [found for found, schedule in zip(allowed, found_schedules, strict=True) if schedule]
    found_schedules = [schedule for schedule in found_schedules if schedule]
    # 오늘 밤에 물었으면 운영 시간 원문에서 끝나는 시각을 읽어, 이미 끝난 행사는 오늘에서 뺀다.
    # 시각을 모르면 남긴다 — 화면에 "가기 전에 확인" 문구가 붙는다 (#800)
    local_now = context.now.astimezone(context.timezone).time()
    for _, event, intro in allowed:
        if ended_today(intro.playtime, local_now):
            context.state.ended_today.add(event.name)
    opened = (
        EventRow(
            name=event.name,
            kind=event.kind,
            starts_on=event.starts_on,
            ends_on=event.ends_on,
            place=intro.place.strip(),
            distance_m=distance,
            schedule=schedule,
        )
        for (distance, event, intro), schedule in zip(allowed, found_schedules, strict=True)
    )
    rows = [row for row in opened if open_on(row, _open_days(context, row.name, days))][
        :EVENT_TOP_K
    ]
    if not rows and pending:
        # 일정을 아직 읽는 중이라 빠졌다 — 행사가 없는 게 아니다 (#906 리뷰)
        return _events_pending(context)
    if not rows and len(fresh) < len(found):
        # 보여 준 행사를 빼고 남은 것도 연령 · 일정으로 빠졌다 — 새 행사가 없는 것과 같다
        return _no_new_events(context)
    for row in rows:
        context.state.seen_events.setdefault(row.name, row)
        if context.state.area is not None:
            context.state.area_places.add(row.name)
    return ok(
        "query",
        _EVENTS,
        events=[_event_result(row, _open_days(context, row.name, days)) for row in rows],
    )


# 보호자 위치에서 찾을 때 "가까운 묶음" 의 경계. 이 안의 행사가 밖의 행사보다 먼저다 (잠정값)
NEAR_EVENT_M = 10_000


def _event_order(context: ActivityContext, distance: int, event: Event) -> tuple[int, ...]:
    """행사를 소개정보 · 일정까지 읽을 순서.

    - 보호자 위치에서 찾으면 거리가 뜻이 있다 — 가까운 묶음(`NEAR_EVENT_M`) 안이 먼저고, 묶음
      안에서는 기간이 짧은 행사 → 가까운 순이다. 멀리 있는 짧은 행사가 1km 앞 행사를 앞지르지
      않고(#856 리뷰), 1년 내내 하는 가까운 곳이 위를 다 차지하지도 않는다(#855).
    - 보호자가 지역을 말했으면 거리는 그 지역 대표 지점에서 잰 값이라 보호자에게 뜻이 없다 —
      기간이 짧은 행사 → 가까운 순이다.
    """
    tier = period_tier(event)
    if context.state.area is not None:
        return (tier, distance)
    return (0 if distance <= NEAR_EVENT_M else 1, tier, distance)


def start_event_prefetch(context: ActivityContext, today_weather: Awaitable[WeatherBrief]) -> None:
    """나들이를 물은 run 이면 날씨를 보는 동안 보호자 위치 근처 행사의 소개정보 · 일정 읽기를 뒤에서
    시작한다 — 모델이 행사를 찾을 때(날씨 조회 · 모델 1회차 뒤, 약 5초)는 캐시에 있다.

    날(주말)은 아직 모르므로 위치 근처에서 `PREFETCH_DAYS` 안에 열리는 행사를 행사 조회와 같은
    순서(`_event_order`)로 `EVENT_TOP_K` 건만 읽는다. 관람 연령은 거르지 않는다 — 읽기만 하고
    거르기는 행사 조회가 한다. 실패는 조용히 버린다 — 행사 조회가 다시 읽는다.

    하지 않는 run (#906 리뷰)
    - 이번 말에 나들이 낱말이 없다(`rules.asks_outing`) — 앞 턴은 보지 않는다
    - 이번 말에 지역 이름이 있다 — 그 run 은 보호자 위치가 아니라 그 지역에서 찾는다
    - 위치 · 행사 어댑터가 없다
    일정 읽기(모델 호출)는 오늘 날씨를 받은 뒤에 시작하고, 오늘 바깥이 어려우면 하지 않는다.
    """
    events = context.ports.events
    schedules = context.ports.event_schedules
    origin = context.location
    if events is None or schedules is None or origin is None:
        return
    said_now = context.state.original_texts or context.state.request_texts
    if not asks_outing(said_now) or mentions_area(said_now):
        return
    task = asyncio.ensure_future(_prefetch(context, origin, today_weather))
    _BACKGROUND.add(task)
    task.add_done_callback(_settle)


async def _prefetch(
    context: ActivityContext, origin: CoarseLocation, today_weather: Awaitable[WeatherBrief]
) -> None:
    events = context.ports.events
    schedules = context.ports.event_schedules
    if events is None or schedules is None:
        return
    raws = await _or_none(events.events(ending_on_or_after=context.today))
    if not raws:
        return
    horizon = context.today + timedelta(days=PREFETCH_DAYS)
    near = [
        (distance, event)
        for event in (read_event(raw) for raw in raws)
        if event is not None
        and event.starts_on <= horizon
        and (distance := distance_m(event, origin)) <= EVENT_MAX_RADIUS_M
    ]
    picked = [
        event
        for _, event in sorted(near, key=lambda pair: _event_order(context, *pair))[:EVENT_TOP_K]
    ]
    # 소개정보는 TourAPI 조회라 날씨와 같이 읽는다. 일정 읽기는 모델 호출이라 오늘 바깥이 될 때만
    await asyncio.gather(*(_or_none(events.intro(content_id=event.content_id)) for event in picked))
    try:
        weather = await asyncio.shield(today_weather)
    except Exception:  # noqa: BLE001 — 날씨를 모르면 미리 읽지 않는다. 행사 조회가 그때 읽는다
        return
    if not weather.outdoor_ok:
        return
    await asyncio.gather(
        *(schedules.schedule_of(event, today=context.today) for event in picked),
        return_exceptions=True,
    )


def _read_budget(context: ActivityContext) -> float:
    """일정 읽기를 기다리는 시간. 기본 `EVENT_READ_BUDGET_S` 이고, run 마감이 가까우면 마지막 후보를
    낼 몫(`FINAL_TURN_RESERVE_S`)을 남기고 그만큼만 기다린다. 마감을 모르면 기본값이다."""
    deadline = context.state.deadline
    if deadline is None:
        return EVENT_READ_BUDGET_S
    left = deadline - perf_counter() - FINAL_TURN_RESERVE_S
    return max(0.0, min(EVENT_READ_BUDGET_S, left))


async def _read_schedules(
    context: ActivityContext, events: Sequence[Event], *, budget: float
) -> list[EventSchedule | None]:
    """행사마다 일정 — 상한 안에 못 읽은 행사는 None. 못 끝낸 읽기는 취소하지 않고 뒤에서 이어
    간다(`_BACKGROUND` 가 붙든다) — 일정 캐시가 채워져 다음 run 은 기다리지 않는다."""
    schedules = context.ports.event_schedules
    if schedules is None or not events:
        return []
    tasks = [
        asyncio.ensure_future(schedules.schedule_of(event, today=context.today)) for event in events
    ]
    if budget > 0:
        await asyncio.wait(tasks, timeout=budget)
    found: list[EventSchedule | None] = []
    for task in tasks:
        if task.done() and not task.cancelled() and task.exception() is None:
            found.append(task.result())
            continue
        if not task.done():
            _BACKGROUND.add(task)
            task.add_done_callback(_settle)
        found.append(None)
    if any(schedule is None for schedule in found):
        logger.info("행사 일정 읽기 상한 budget=%.1fs 못_읽음=%d", budget, found.count(None))
    return found


def _settle(task: "asyncio.Task[EventSchedule]") -> None:
    _BACKGROUND.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.warning("행사 일정 뒤 읽기 실패 error=%s", type(task.exception()).__name__)


def _events_pending(context: ActivityContext) -> ToolResult:
    """일정 읽기가 상한 안에 끝나지 않아 이번에 낼 행사가 없다 — "더 없음" 이 아니라 "확인 중" 이다.
    읽기는 뒤에서 이어 가서 다음에 물으면 나온다."""
    context.state.events_pending = True
    return ok(
        "query",
        _EVENTS,
        events=[],
        message="행사 일정을 아직 확인하는 중이다. 다시 찾지 말고 이번에는 행사 없이 낸다.",
    )


def _no_new_events(context: ActivityContext) -> ToolResult:
    """앞 턴에서 보여 준 행사 말고는 새 행사가 없다 — 같은 행사를 다시 내지 않고 안내한다 (#852)."""
    context.state.no_new_events = True
    return ok(
        "query",
        _EVENTS,
        events=[],
        message="앞에서 보여 준 행사뿐이다. 행사를 다시 내지 말고 행사 없이 낸다.",
    )


def _open_days(context: ActivityContext, name: str, days: Sequence[date]) -> list[date]:
    """그 행사에 물어볼 날 — 오늘 운영 시간이 이미 끝난 행사는 오늘을 뺀다."""
    if name not in context.state.ended_today:
        return list(days)
    return [day for day in days if day != context.today]


def _event_result(row: EventRow, days: Sequence[date]) -> dict[str, Any]:
    """모델에 주는 행사 한 건. 열리는 날은 요청한 날 중에서만, 장소는 그날 장소다."""
    open_days = [day for day in days if open_on(row, (day,))]
    moving = bool(row.schedule.day_places)
    return {
        "name": row.name,
        "kind": row.kind.value,
        "period": f"{row.starts_on.isoformat()} ~ {row.ends_on.isoformat()}",
        "place": " · ".join(places_on(row, open_days)),
        "open_days": [
            {
                "date": day.isoformat(),
                "weekday": _WEEKDAYS[day.weekday()],
                **({"place": " · ".join(places_on(row, (day,)))} if moving else {}),
            }
            for day in open_days
        ],
    }


def _origin(
    context: ActivityContext,
    area_text: str | None,
    *,
    tool: str = _PLACES,
    provinces: bool = False,
) -> CoarseLocation | ToolResult:
    """장소 · 행사를 찾을 기준점. 실패면 모델에게 돌려줄 결과다(`tool` 은 그 tool 이름).

    - 지역을 말하지 않았으면 이 run 의 기준점이다 — 앞에서 지역을 찾았으면 그 지역, 아니면 보호자
      위치다(`outing_location`). 지역을 찾은 뒤 보호자 위치로 돌아가면 장소와 날씨의 기준점이
      갈린다 (#657 리뷰). 둘 다 없으면 INVALID_ARGS.
    - 🚨 지역 이름은 보호자가 쓴 글자 안에 있어야 한다(`said`). 모델이 위치를 짐작해 지어낸
      지역으로 찾지 않는다.
    - 못 찾으면(모르는 이름 · 여러 곳인데 위치가 없음 · 시도만) 장소 없이 간다. 결과에 한 줄
      안내가 붙는다(`state.area_missed`). 비슷한 곳으로 짐작하지 않는다.
    - `provinces` 면(행사 조회) 시 · 도만 말해도 그 시 · 도 전체가 지역이다(`find_province` 를 먼저
      본다 — 제주는 제주시가 아니라 제주도 전체다). 다른 시도의 시와 겹치는 이름(광주시)은 못 찾은
      지역이다. 장소
      조회는 시 · 도 전체에서 찾지 않는다 — 행사로 시 · 도를 정한 뒤에도 안내를 남기고 장소 없이
      간다.
    - 찾으면 run 의 지역이 된다 — 날씨도 그 지역으로 다시 보고(`state.weather` 를 비운다), 보호자
      위치에서 찾은 장소 · 행사도 버린다(`state.seen_places` · `seen_events`). 남기면 그 지역
      날씨로 보호자 동네 장소가 붙는다 (#657 리뷰). 한 run 에 한 지역이다. 날씨 판정이 지역마다
      갈리면 어느 추천에 어느 날씨를 댈지 정할 수 없어서다.
    """
    state = context.state
    if not provinces and state.area is not None and state.area.whole_province:
        state.area_missed = True
        return fail(
            "query",
            tool,
            ErrorCode.NOT_FOUND,
            "시 · 도 전체는 넓어서 장소를 찾지 않는다. 장소가 필요 없는 놀이만 낸다.",
        )
    if not area_text or not area_text.strip():
        if context.outing_location is None:
            context.state.location_missing = True
            return fail(
                "query",
                tool,
                ErrorCode.INVALID_ARGS,
                "위치를 모른다. 보호자가 지역을 말했을 때만 area 에 그 이름을 넣어 찾는다. "
                "말하지 않았으면 장소가 필요 없는 놀이만 낸다.",
            )
        return context.outing_location
    name = named_area(area_text, state.said())
    if name is None and not said(area_text, state.said()):
        return fail(
            "query",
            tool,
            ErrorCode.INVALID_ARGS,
            "area 에는 보호자가 쓴 지역 이름을 글자 그대로 넣는다. 지역을 말하지 않았으면 비운다.",
        )
    # 명소 이름을 통째로 넣었다. 앞의 지역 이름을 떼어 쓰지 않는다 — 그 지역에 있는지는 모델이 안다
    if name is None and leads_with_area(area_text):
        return fail(
            "query",
            tool,
            ErrorCode.INVALID_ARGS,
            "area 에는 지역 이름만 넣는다(해수욕장 · 역 · 읍 같은 뒷말을 뺀다). 명소 이름이 지역 "
            "이름으로 시작해도 그 명소가 그 지역에 있지 않으면 그 지역 이름을 쓰지 않고 비운다.",
        )
    # 보호자가 말했지만 사전에 없는 이름(서면)은 못 찾은 지역이다 — 안내가 붙는다
    area = find_province(name) if provinces and name is not None else None
    if area is None and name is not None:
        area = find_area(name, near=context.location)
    if area is None:
        state.area_missed = state.area is None
        return fail(
            "query",
            tool,
            ErrorCode.NOT_FOUND,
            "그 지역을 찾지 못했다. 장소가 필요 없는 놀이만 낸다. 다른 이름으로 다시 찾지 않는다.",
        )
    if state.area is not None and state.area != area:
        return fail(
            "query",
            tool,
            ErrorCode.INVALID_ARGS,
            "한 번에 한 지역만 찾는다. 처음 찾은 지역 이름으로 찾는다.",
        )
    if state.area is None:
        state.area = area
        state.area_missed = False
        # 보호자 위치의 판정 · 장소다. 그 지역으로 다시 본다
        state.weather.clear()
        state.seen_places.clear()
        state.seen_events.clear()
    return area.location


async def _or_none(call: Awaitable[_T]) -> _T | None:
    """조회 하나의 실패를 None 으로. 다른 예외는 삼키지 않는다."""
    try:
        return await call
    except UpstreamUnavailable:
        return None


async def _none() -> None:
    return None


def _day_schedule(
    day: date, blocks: Sequence[ScheduleBlock], context: ActivityContext, now: datetime
) -> dict[str, Any]:
    """하루 몫. 바쁜 칸과 비는 칸은 `DAY_START` ~ `DAY_END` 안으로 자른다."""
    tz = context.timezone
    start = datetime.combine(day, DAY_START, tzinfo=tz)
    end = datetime.combine(day, DAY_END, tzinfo=tz)
    if day == now.date():
        start = max(start, now)

    busy: list[tuple[datetime, datetime]] = []
    all_day = False
    for block in blocks:
        if block.all_day:
            all_day = all_day or block.starts_at.astimezone(tz).date() == day
            continue
        begins = block.starts_at.astimezone(tz)
        ends = block.ends_at.astimezone(tz) if block.ends_at else begins + OPEN_ENDED_BLOCK
        clipped = (max(begins, datetime.combine(day, DAY_START, tzinfo=tz)), min(ends, end))
        if clipped[0] < clipped[1]:
            busy.append(clipped)

    return {
        "date": day.isoformat(),
        "weekday": _WEEKDAYS[day.weekday()],
        "day_type": _day_type(day),
        "all_day_event": all_day,
        "busy": [_span(a, b) for a, b in _merged(busy)],
        "free": [] if all_day else [_span(a, b) for a, b in _gaps(_merged(busy), start, end)],
    }


def _day_type(day: date) -> str:
    """평일 · 주말 · 공휴일. 공휴일을 넣어 두지 않은 해는 주말만 보고 로그에 남긴다."""
    if not holidays_known(day.year):
        logger.warning("공휴일 상수가 없는 해 — 주말만 본다", extra={"year": day.year})
    elif is_public_holiday(day):
        return "holiday"
    return "weekend" if day.weekday() >= 5 else "weekday"


def _merged(spans: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for begins, ends in sorted(spans):
        if merged and begins <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], ends))
        else:
            merged.append((begins, ends))
    return merged


def _gaps(
    busy: list[tuple[datetime, datetime]], start: datetime, end: datetime
) -> list[tuple[datetime, datetime]]:
    """start ~ end 에서 busy 를 뺀 구간. busy 는 합쳐 정렬된 상태로 받는다."""
    gaps: list[tuple[datetime, datetime]] = []
    cursor = start
    for begins, ends in busy:
        if begins > cursor:
            gaps.append((cursor, min(begins, end)))
        cursor = max(cursor, ends)
    if cursor < end:
        gaps.append((cursor, end))
    return [(a, b) for a, b in gaps if a < b]


def _span(begins: datetime, ends: datetime) -> dict[str, str]:
    return {"start": begins.strftime("%H:%M"), "end": ends.strftime("%H:%M")}


def _after_sunset(context: ActivityContext, day: date) -> bool:
    """지금이 그날 일몰 뒤인가. 위치(`outing_location`) · 시각대는 context 에서 읽는다.

    위치가 없으면 국토 가운데 기준점으로 계산한다 — 보호자 위치가 아니라 고정값이고, 국내 일몰은
    동서 끝이 30분 남짓 차이라 "해가 졌는데 공원" 을 막기에는 충분하다.
    """
    location = context.outing_location
    local = context.now.astimezone(context.timezone)
    offset = local.utcoffset()
    if offset is None:
        return False
    lat, lon = (location.lat, location.lon) if location is not None else SUNSET_REFERENCE
    hours = offset.total_seconds() / 3600
    times = sun_times(day, lat, lon, tz_offset_hours=hours)
    if times is None:
        return False
    _, sunset = times
    return local.time() >= sunset
