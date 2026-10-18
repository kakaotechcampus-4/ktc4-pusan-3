"""병원 찾기(`find_pediatric_places`)가 쥐는 포트와 값 (Health_Tool_명세 · `place_lookup`).

모델을 부르지 않는 코드 tool 이다. 실제 공공 API(국립중앙의료원 병·의원 · 응급의료기관)는
`FacilitySource` 어댑터 뒤에 둔다 — 이 모듈은 어느 기관을 어떤 순서로 보여 줄지만 정한다.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from app.core.weather_raw import WeatherGrid


class PlaceMode(StrEnum):
    """`normal` 은 보호자가 병원을 찾을 때, `emergency` 는 Supervisor 응급 안내에서 넘어왔을 때."""

    NORMAL = "normal"
    EMERGENCY = "emergency"


class FacilityTier(StrEnum):
    """보여 줄 기관 종류. 시간대가 어느 종류를 먼저 보일지 정한다 (명세 6-4)."""

    PEDIATRIC_CLINIC = "pediatric_clinic"  # 소아청소년과 의원 — 평일 주간
    MOONLIGHT = "moonlight"  # 달빛어린이병원 — 야간 · 휴일 소아 경증
    EMERGENCY_ROOM = "emergency_room"  # 응급의료기관


@dataclass(frozen=True)
class Facility:
    """어댑터가 돌려주는 기관 하나. 좌표는 **기관 주소**의 것이라 위치정보가 아니다.

    `open_now` 는 어댑터가 진료시간 · 실시간 정보로 판정한 값이다. 모르면 None.
    """

    name: str
    tier: FacilityTier
    lat: float
    lon: float
    phone: str | None = None
    open_now: bool | None = None


class FacilityLookupError(Exception):
    """공공 API 를 못 읽었다. 빈 목록과 다르다 — 화면은 "불러오지 못했어요 · 응급 시 119" 를 낸다.

    메시지에 좌표 · 지역 코드를 싣지 않는다 (예외는 로그로 흘러간다).
    """


class FacilitySource(Protocol):
    async def facilities(
        self, *, grid: WeatherGrid, tier: FacilityTier, now: datetime
    ) -> list[Facility]:
        """그 격자가 속한 시군구의 기관. 실패하면 FacilityLookupError.

        🚨 보호자 좌표를 받지 않는다 — 날씨와 같은 5km 격자만 받고, 어댑터가 격자를 시군구로
        바꿔 조회한다. 밖으로 나가는 위치 파생값은 시군구뿐이다 (Activity 와 같은 원칙,
        activity-agent-v1 D9). 거리는 호출부가 서버 안에서 계산한다. 격자에 맞는 시군구가
        없으면(바다) 빈 목록이다.
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
