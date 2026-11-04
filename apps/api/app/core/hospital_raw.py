"""병원 원문 계약. 병원 어댑터(`app/integrations/hospital/`)가 싣고 Health 병원 찾기가 읽는 모양.

`app/integrations/` 는 core 만 import 할 수 있어서 어댑터와 Agent 가 함께 보는 모양은 여기 둔다
(`core/weather_raw.py` 와 같은 자리). 진료시간은 API 원문("0900" · "1830")이고, 지금 진료 중인지는
Health 쪽이 읽는다. 조회 실패는 `core.weather_raw.UpstreamUnavailable` 을 같이 쓴다.

🚨 보호자 좌표는 여기 없다. 좌표는 **기관 주소**의 것이다.
"""

from dataclasses import dataclass, field
from enum import StrEnum


class FacilityTier(StrEnum):
    """보여 줄 기관 종류. 시간대가 어느 종류를 먼저 보일지 정한다 (Health 명세 6-4)."""

    PEDIATRIC_CLINIC = "pediatric_clinic"  # 소아청소년과 진료 기관 — 평일 주간
    # 야간 · 휴일에 지금 진료 중인 소아청소년과. 달빛어린이병원은 API 에 표시 칸이 없어(자유 글에만
    # 있다) 진료시간으로 고른다 — 달빛어린이병원도 여기에 잡힌다
    NIGHT_CLINIC = "night_clinic"
    EMERGENCY_ROOM = "emergency_room"  # 응급의료기관


# 요일 칸 순서 — 국립중앙의료원 dutyTime1~8 (월 … 일, 공휴일)
HOLIDAY_SLOT = 7


@dataclass(frozen=True)
class RawFacility:
    """기관 하나의 원문. `hours[i]` 는 dutyTime{i+1} 의 (시작, 끝) — 없는 칸은 None.

    응급의료기관처럼 진료시간이 없는 종류는 `hours` 가 비어 있다.
    """

    name: str
    tier: FacilityTier
    lat: float
    lon: float
    phone: str | None = None
    hours: tuple[tuple[str | None, str | None], ...] = field(default=())
