"""날씨 · 일정 · 장소 조회 인자.

날짜는 DayLabel 라벨로만 받는다. 모델이 날짜를 계산하지 않는다.
위치는 인자에 없다 — 좌표는 요청 바디로만 받아 격자로 뭉갠 뒤 context 가 들고 있다 (4-3).
반경도 인자에 없다 — 코드 상수다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.activity.schemas.common import DayLabel, PlaceCategory, ToolArgs

Day = Annotated[
    DayLabel,
    Field(default=DayLabel.TODAY, description="today / tomorrow / this_weekend 중 하나"),
]


class LookupWeatherArgs(ToolArgs):
    """판정과 등급 라벨만 돌려준다. raw 수치는 모델에게 주지 않는다 (4-2)."""

    day: Day


class LookupScheduleArgs(ToolArgs):
    """아이 일정과 비는 시간. 읽기 전용."""

    day: Day


class SearchNearbyPlacesArgs(ToolArgs):
    category: Annotated[
        PlaceCategory,
        Field(
            description=(
                "park / playground / library / indoor_playground / experience_center 중 하나"
            ),
        ),
    ]
