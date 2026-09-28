"""날씨 · 일정 · 장소 조회 (모델 tool 3개).

지금은 구현부가 주석이라 호출시 NotImplementedError.
실패는 기본값으로 메우지 않는다 — 날씨 실패는 "맑음"이 아니다 (D8).
"""

from app.agents.activity.context import ActivityContext
from app.agents.activity.result import ToolResult
from app.agents.activity.schemas.outing import (
    LookupScheduleArgs,
    LookupWeatherArgs,
    SearchNearbyPlacesArgs,
)

# 장소 검색 반경. 모델 인자가 아니라 코드 상수다 (D9 — 5km 는 위경도 bounding box 로 끝난다)
PLACE_RADIUS_M = 5_000


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
    """근처 장소를 종류로 찾는다. 36개월 이상 · 위치 있음 · 야외 가능일 때만 열린다.

    외부 API 연결 후:
    - `context.grid` 와 args.category, PLACE_RADIUS_M 만 포트에 넘긴다.
      🚨 검색어는 닫힌 enum 하나다. 모델이 만든 문자열을 넣지 않는다.
    - 결과에는 이름 · 종류 · 거리만 싣는다. 모델은 이 이름만 place_name 에 쓸 수 있다.
    - 실패하면 UPSTREAM_ERROR. 모델은 장소가 필요 없는 활동만 낸다.
    - 장소 행은 근거(suggestion_evidence)에 넣지 않는다. 날씨처럼 필터 조건이다.
    """
    raise NotImplementedError("외부 API 연결 후 구현")
