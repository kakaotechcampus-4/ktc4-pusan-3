"""`find_pediatric_places` — 시간대에 맞는 가까운 병원 다섯 곳 (Health_Tool_명세 · 명세 6-4).

모델을 부르지 않는다. 순서는 이렇다.

1. 좌표가 없으면 닫는다 — "위치를 허용하시면 가까운 병원을 찾아드려요".
2. 좌표 → 기상청 격자. 어댑터는 격자만 받아 시도 이름으로 바꿔 조회한다 — 밖으로 나가는 위치
   값은 시도 이름뿐이다.
3. 시간대가 기관 종류의 순서를 정한다 — 평일 주간은 소아청소년과, 야간 · 휴일은 지금 진료 중인
   소아청소년과(달빛어린이병원 포함) 다음 응급의료기관, 응급 안내에서 넘어오면 응급의료기관만.
4. 종류 순서대로, 같은 종류 안에서는 가까운 순으로 다섯 곳. 평가 · 추천 순위는 매기지 않는다.
"""

import logging
import math
from dataclasses import dataclass
from datetime import datetime, time

from app.agents.activity.store.ports import CoarseLocation
from app.agents.health.places.hours import open_now
from app.agents.health.places.ports import (
    FacilityLookupError,
    FacilitySource,
    FacilityTier,
    NearbyPlace,
    PlaceLookup,
    PlaceMode,
    RawFacility,
)
from app.rules.holidays import holidays_known, is_public_holiday

logger = logging.getLogger(__name__)

# 평일 주간 — 소아청소년과 의원이 문을 여는 시간으로 본다. 토요일은 오전 진료가 의원마다 달라서
# 야간 · 휴일 쪽(진료시간으로 지금 연 곳만)으로 보낸다
DAYTIME_START = time(9, 0)
DAYTIME_END = time(18, 0)

PLACE_TOP_K = 5

# 소아청소년과(낮 · 밤)는 이 거리 안만 보인다. 목록이 시도 전체라 자르지 않으면 밤에 46km 의원이
# 600m 응급실보다 먼저 다섯 칸을 채운다 (#517 리뷰). 응급실은 자르지 않는다 — 멀어도 가야 한다
CLINIC_MAX_DISTANCE_M = 10_000

_EARTH_RADIUS_M = 6_371_000


@dataclass(frozen=True)
class _Ranked:
    facility: RawFacility
    distance_m: int
    open_now: bool | None


def tiers_for(now: datetime, mode: PlaceMode) -> tuple[FacilityTier, ...]:
    """보여 줄 기관 종류와 그 순서. 코드가 정한다 — 모델에게 묻지 않는다 (루트 §3)."""
    if mode is PlaceMode.EMERGENCY:
        return (FacilityTier.EMERGENCY_ROOM,)
    if _is_weekday_daytime(now):
        return (FacilityTier.PEDIATRIC_CLINIC,)
    return (FacilityTier.NIGHT_CLINIC, FacilityTier.EMERGENCY_ROOM)


def _is_weekday_daytime(now: datetime) -> bool:
    day = now.date()
    if day.weekday() >= 5:
        return False
    if not holidays_known(day.year):
        logger.warning("공휴일 상수가 없는 해 — 주말만 본다", extra={"year": day.year})
    elif is_public_holiday(day):
        return False
    return DAYTIME_START <= now.time() < DAYTIME_END


async def find_pediatric_places(
    *,
    location: CoarseLocation | None,
    now: datetime,
    mode: PlaceMode,
    source: FacilitySource | None,
) -> PlaceLookup:
    """`now` 는 KST 벽시계 시각이다(호출부가 맞춘다). 실패는 빈 목록이 아니라 `unavailable` 이다."""
    if location is None:
        return PlaceLookup(status="no_location")
    if source is None:
        return PlaceLookup(status="unavailable")  # 어댑터가 안 붙었다 — 조회 실패와 같다
    grid = location.grid

    ranked: list[_Ranked] = []
    for tier in tiers_for(now, mode):
        try:
            found = await source.facilities(grid=grid, tier=tier)
        except FacilityLookupError:
            logger.warning("병원 정보를 읽지 못했다", extra={"tier": tier.value})
            return PlaceLookup(status="unavailable")
        candidates = [
            _Ranked(f, _distance_m(location, f), open_now(f, now)) for f in found if f.tier is tier
        ]
        ranked.extend(
            sorted(
                (r for r in candidates if _shown(tier, r.open_now, r.distance_m)),
                key=lambda r: (r.distance_m, r.facility.name),
            )
        )
        if len(ranked) >= PLACE_TOP_K:
            break

    places = tuple(
        NearbyPlace(
            name=r.facility.name,
            tier=r.facility.tier,
            distance_m=r.distance_m,
            phone=r.facility.phone,
            open_now=r.open_now,
        )
        for r in ranked[:PLACE_TOP_K]
    )
    return PlaceLookup(status="ok" if places else "empty", places=places)


def _shown(tier: FacilityTier, is_open: bool | None, distance_m: int) -> bool:
    """야간 · 휴일 소아청소년과는 지금 진료 중인 게 확실한 곳만 — 밤에 "모름" 은 헛걸음이다.
    그 밖은 문을 닫은 게 확실한 곳만 뺀다. 모르는 곳은 남긴다 — 전화로 확인할 수 있다.
    """
    if tier is not FacilityTier.EMERGENCY_ROOM and distance_m > CLINIC_MAX_DISTANCE_M:
        return False
    if tier is FacilityTier.NIGHT_CLINIC:
        return is_open is True
    return is_open is not False


def _distance_m(location: CoarseLocation, facility: RawFacility) -> int:
    """하버사인 거리(m). 보호자 좌표는 이미 약 1km 로 흐린 값이라 100m 단위로 자른다."""
    lat1, lon1 = math.radians(location.lat), math.radians(location.lon)
    lat2, lon2 = math.radians(facility.lat), math.radians(facility.lon)
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    meters = 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))
    return int(round(meters / 100) * 100)
