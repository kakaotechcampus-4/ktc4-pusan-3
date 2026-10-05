"""날씨 · 일정 · 장소 조회 (모델 tool 3개).

날씨 · 일정은 아직 구현부가 주석이라 호출시 NotImplementedError. 장소 조회는 포트까지 연결돼 있다.
실패는 기본값으로 메우지 않는다 — 날씨 실패는 "맑음"이 아니다 (D8).
"""

from app.agents.activity.context import ActivityContext
from app.agents.activity.result import ErrorCode, ToolResult, fail, ok
from app.agents.activity.schemas.common import PlaceCategory
from app.agents.activity.schemas.outing import (
    LookupScheduleArgs,
    LookupWeatherArgs,
    SearchNearbyPlacesArgs,
)
from app.agents.activity.store.ports import UpstreamUnavailable

# 장소 검색은 넓게 찾고 가까운 순으로 몇 곳만 준다. 둘 다 모델 인자가 아니라 코드 상수이고
# 잠정값이다 — eval 로 조정한다. 반경을 좁게 자르면 군 지역에서 0곳이 돼 외출 요청에
# 장소가 빠진다. 20km 도 위경도 bounding box + haversine 으로 끝난다 (D9)
PLACE_MAX_RADIUS_M = 20_000
PLACE_TOP_K = 5

# 바깥 활동이 어려운 날(outdoor_ok=False)에도 찾을 수 있는 종류
INDOOR_CATEGORIES = frozenset(
    {PlaceCategory.LIBRARY, PlaceCategory.INDOOR_PLAYGROUND, PlaceCategory.EXPERIENCE_CENTER}
)

_PLACES = "search_nearby_places"


async def lookup_weather(context: ActivityContext, args: LookupWeatherArgs) -> ToolResult:
    """오늘(또는 라벨의 날) 야외 판정과 등급 라벨을 돌려준다.

    DB 연결 후:
    - args.day 를 날짜로 바꾼다. 모델은 날짜를 계산하지 않는다.
    - 예보 · 대기질 · 자외선 · 특보를 병렬로 부르고 4-2 표로 판정한다.
    - 모델에게는 판정(`outdoor_ok`)과 등급 라벨만 준다. raw 수치는 주지 않는다 —
      모델이 자기 기준으로 재해석해 "나쁨이지만 잠깐이면 괜찮아요"를 쓴다.
    - 미세먼지만 실패하면 야외는 허용하되 "미세먼지는 확인하지 못했어요" 를 싣는다.
    - 날씨가 실패하면 UPSTREAM_ERROR. "맑음"으로 가정하지 않는다.
    """
    raise NotImplementedError("외부 API 연결 후 구현")


async def lookup_schedule(context: ActivityContext, args: LookupScheduleArgs) -> ToolResult:
    """아이 일정과 비는 시간을 돌려준다. 읽기 전용이다.

    DB 연결 후:
    - args.day 를 날짜 범위로 바꾼다.
    - 일정 칸을 합쳐 비는 시간을 계산한다. 공휴일은 app/rules/ 상수로 본다 —
      아이 일정에는 어린이집 휴원일이 없다.
    - 일정 제목은 싣지 않는다. 비는 시간과 겹침만 준다.
    """
    raise NotImplementedError("DB 연결 후 구현")


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
