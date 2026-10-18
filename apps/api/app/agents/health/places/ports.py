"""병원 찾기(`find_pediatric_places`)가 쥐는 포트와 값 (Health_Tool_명세 · `place_lookup`).

모델을 부르지 않는 코드 tool 이다. 실제 공공 API(국립중앙의료원 병·의원 · 응급의료기관)는
`FacilitySource` 어댑터 뒤에 둔다 — 이 모듈은 어느 기관을 어떤 순서로 보여 줄지만 정한다.
어댑터가 싣는 원문(`RawFacility`)과 종류(`FacilityTier`)는 `core/hospital_raw.py` 에 있다 —
`app/integrations/` 는 agents 를 import 할 수 없어서다.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from app.core.hospital_raw import FacilityTier, RawFacility
from app.core.weather_raw import UpstreamUnavailable, WeatherGrid

# 조회 실패. 빈 목록과 다르다 — 화면은 "불러오지 못했어요 · 응급 시 119" 를 낸다
FacilityLookupError = UpstreamUnavailable

__all__ = [
    "FacilityLookupError",
    "FacilitySource",
    "FacilityTier",
    "NearbyPlace",
    "PlaceLookup",
    "PlaceMode",
    "RawFacility",
]


class PlaceMode(StrEnum):
    """`normal` 은 보호자가 병원을 찾을 때, `emergency` 는 Supervisor 응급 안내에서 넘어왔을 때."""

    NORMAL = "normal"
    EMERGENCY = "emergency"


class FacilitySource(Protocol):
    async def facilities(self, *, grid: WeatherGrid, tier: FacilityTier) -> list[RawFacility]:
        """그 격자가 속한 지역의 기관. 실패하면 FacilityLookupError.

        🚨 보호자 좌표를 받지 않는다 — 날씨와 같은 5km 격자만 받고, 어댑터가 격자를 지역 코드로
        바꿔 조회한다 (Activity 와 같은 원칙, activity-agent-v1 D9). 거리와 "지금 진료 중" 은
        호출부가 서버 안에서 계산한다. 격자에 맞는 지역이 없으면(바다) 빈 목록이다.
        """
        ...


@dataclass(frozen=True)
class NearbyPlace:
    """화면에 나가는 기관 한 줄. 평가 · 순위 문구는 없다 — 거리 · 운영 여부만 (명세 6-4)."""

    name: str
    tier: FacilityTier
    distance_m: int
    phone: str | None
    open_now: bool | None


@dataclass(frozen=True)
class PlaceLookup:
    """`find_pediatric_places` 의 결과. 목록이 비어도 readout 은 늘 하나 있다."""

    status: str  # ok · empty · no_location · unavailable
    places: tuple[NearbyPlace, ...] = field(default=())
