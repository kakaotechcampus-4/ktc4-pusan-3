"""날씨 · 일정 · 장소 조회 (모델 tool 3개).

실패는 기본값으로 메우지 않는다 — 날씨 실패는 "맑음"이 아니다 (D8).
"""

import asyncio
import logging
from collections.abc import Awaitable, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any, TypeVar

from app.agents.activity.context import ActivityContext
from app.agents.activity.result import ErrorCode, ToolResult, fail, ok
from app.agents.activity.schemas.common import DayLabel, PlaceCategory
from app.agents.activity.schemas.outing import (
    LookupScheduleArgs,
    LookupWeatherArgs,
    SearchNearbyPlacesArgs,
)
from app.agents.activity.store.ports import ScheduleBlock, UpstreamUnavailable
from app.agents.activity.weather import (
    WEATHER_UNCHECKED,
    WeatherBrief,
    judge_weather,
    parse_uv,
    read_advisories,
    read_air,
    read_forecast,
)
from app.rules.holidays import holidays_known, is_public_holiday
from app.rules.sun import sun_times

logger = logging.getLogger(__name__)

# 장소 검색은 넓게 찾고 가까운 순으로 몇 곳만 준다. 둘 다 모델 인자가 아니라 코드 상수이고
# 잠정값이다 — eval 로 조정한다. 반경을 좁게 자르면 군 지역에서 0곳이 돼 외출 요청에
# 장소가 빠진다. 20km 도 위경도 bounding box + haversine 으로 끝난다 (D9)
PLACE_MAX_RADIUS_M = 20_000
PLACE_TOP_K = 5

# 바깥 활동이 어려운 날(outdoor_ok=False)에도 찾을 수 있는 종류
INDOOR_CATEGORIES = frozenset(
    {PlaceCategory.LIBRARY, PlaceCategory.INDOOR_PLAYGROUND, PlaceCategory.EXPERIENCE_CENTER}
)

# 비는 시간을 계산하는 하루의 범위. 아이가 밖에서 놀 수 있는 시간대로 둔 잠정값이다 — eval 로
# 조정한다. 오늘이면 지금 시각부터 센다
DAY_START = time(8)
DAY_END = time(20)
# 끝 시각이 없는 일정이 차지한다고 보는 길이. 비는 시간을 넉넉히 잡지 않는 쪽으로 둔다 (잠정)
OPEN_ENDED_BLOCK = timedelta(hours=1)

_PLACES = "search_nearby_places"
_WEATHER = "lookup_weather"
_SCHEDULE = "lookup_schedule"
_WEEKDAYS = "월화수목금토일"

_T = TypeVar("_T")


def day_of(label: DayLabel, today: date) -> date:
    """모델이 고른 날 라벨을 날짜로. 모델은 날짜를 계산하지 않는다.

    이번 주말은 토요일이다. 오늘이 토 · 일이면 오늘이다 — 주말 당일에 "이번 주말"은 오늘이다.
    """
    if label == DayLabel.TOMORROW:
        return today + timedelta(days=1)
    if label == DayLabel.THIS_WEEKEND:
        days_to_saturday = 5 - today.weekday()
        return today if days_to_saturday <= 0 else today + timedelta(days=days_to_saturday)
    return today


def days_of(label: DayLabel, today: date) -> tuple[date, ...]:
    """라벨이 가리키는 날들. 이번 주말은 토 · 일 이틀이다 — 주말 나들이는 둘 중 하루를 고르는
    일이라 날마다 따로 본다. 오늘이 일요일이면 오늘 하루다.
    """
    first = day_of(label, today)
    if label == DayLabel.THIS_WEEKEND and first.weekday() == 5:
        return (first, first + timedelta(days=1))
    return (first,)


async def check_weather(context: ActivityContext, day: date) -> WeatherBrief:
    """그날의 야외 판정. run() 의 사전 조회와 `lookup_weather` 가 같이 쓴다.

    - 예보 · 대기질 · 자외선 · 특보를 병렬로 부른다. 하나가 실패해도 나머지는 쓴다 — 실패한
      조회만 None 으로 `judge_weather` 에 넘기고, 판정 규칙이 실내만 · 고지를 정한다 (D8).
    - 위치나 날씨 포트가 없으면 조회하지 않고 실패와 같게 본다 — 실내만.
    - 🚨 대기질은 오늘만 본다. 에어코리아는 지금 측정값만 주고 예보가 아니다 — 내일 추천에 오늘
      미세먼지를 쓰지 않고 "확인 못 함"으로 둔다. 특보는 지금 발효 중인 것을 그날에도 본다
      (더 막는 쪽).
    - 해가 졌는지는 오늘만 본다. 일몰은 위치와 날짜로 계산한다(`app/rules/sun.py`).
    """
    weather = context.ports.weather
    grid = context.grid
    location = context.location
    if weather is None or grid is None or location is None:
        return judge_weather(
            forecast=None, air=None, uv_index=None, advisories=None, after_sunset=False
        )

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
        after_sunset=today and _after_sunset(context, day),
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
    - 날씨를 못 읽은 날은 `checked=False` · 실내만이다. 모든 날을 못 읽으면 UPSTREAM_ERROR.
      "맑음"으로 가정하지 않는다.
    """
    days = days_of(args.day, context.today)
    briefs = await asyncio.gather(*(check_weather(context, day) for day in days))
    context.state.weather.update(zip(days, briefs, strict=True))
    checked = [WEATHER_UNCHECKED not in brief.notices for brief in briefs]
    if not any(checked):
        return fail(
            "query",
            _WEATHER,
            ErrorCode.UPSTREAM_ERROR,
            "날씨를 확인하지 못했다. 맑다고 가정하지 않는다. 실내 활동만 낸다.",
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
    today = context.today
    days = days_of(args.day, today)
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
    """근처 장소를 종류로 찾는다. 위치가 있을 때만 열린다 — 월령으로는 닫지 않는다.

    - `context.location` · args.category · 반경만 포트에 넘긴다. 포트는 적재한 place
      테이블에서 거리를 계산한다 — 외부 API 를 부르지 않는다 (D9).
      🚨 검색 조건은 닫힌 enum 하나다. 모델이 만든 문자열로 찾지 않는다.
    - 바깥 활동이 어려운 날은 실내 종류만 찾는다. 바깥 종류를 고르면 INVALID_ARGS 이고
      모델이 실내 종류로 고쳐 다시 부른다.
    - 가까운 순 `PLACE_TOP_K` 곳의 이름 · 종류만 싣는다. 🚨 거리는 모델에 주지 않는다 —
      이름 여러 곳과 거리를 같이 주면 위치를 거꾸로 짐작할 수 있고, 약관(draft-1 제8조 ③)이
      인공지능에 전달된다고 적은 것은 장소 이름뿐이다. 정렬은 여기서 끝낸다.
      돌려준 장소는 `context.state.seen_places` 에 적는다 — 출력 검증이 place_name 을 이 표와
      대조한다.
    - 실패하면 UPSTREAM_ERROR. 모델은 장소가 필요 없는 활동만 낸다.
    - 장소 행은 근거(suggestion_evidence)에 넣지 않는다. 날씨처럼 필터 조건이다.
    """
    gate = context.state.gate
    location = context.location
    if gate is None or location is None:
        raise RuntimeError("Gate · 위치 없이 장소 조회가 불렸다 — registry 가 열지 않았어야 한다")

    if not gate.outdoor_ok and args.category not in INDOOR_CATEGORIES:
        indoor = " · ".join(sorted(INDOOR_CATEGORIES))
        return fail(
            "query",
            _PLACES,
            ErrorCode.INVALID_ARGS,
            f"오늘은 바깥 활동이 어려운 날이라 실내 장소만 찾을 수 있다. {indoor} 중에서 고른다.",
        )

    try:
        rows = await context.ports.places.nearby(
            location=location, category=args.category, radius_m=PLACE_MAX_RADIUS_M
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
        context.state.seen_places[row.name] = row
    return ok(
        "query",
        _PLACES,
        places=[{"name": row.name, "category": row.category.value} for row in nearest],
    )


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
    """지금이 그날 일몰 뒤인가. 위치 · 시각대는 context 에서 읽는다."""
    location = context.location
    local = context.now.astimezone(context.timezone)
    offset = local.utcoffset()
    if location is None or offset is None:
        return False
    hours = offset.total_seconds() / 3600
    times = sun_times(day, location.lat, location.lon, tz_offset_hours=hours)
    if times is None:
        return False
    _, sunset = times
    return local.time() >= sunset
