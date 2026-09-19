"""Food tool 실행에 필요한 요청 단위 컨텍스트.

LLM이 만들면 안 되는 값(child_id, 식이 단계)과 날짜 계산의 기준(now, timezone),
읽기 포트를 한 곳에 모은다. tool 인자에는 이 값들이 노출되지 않는다.
"""

from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from uuid import UUID

from app.agents.common.datetime_rules import today_of
from app.agents.food.schemas.common import FeedingStage
from app.agents.food.store.ports import (
    ChildProfileReader,
    DaycareMenuReader,
    FoodMemoryReader,
    NutritionSource,
    SafetyReader,
)


@dataclass(frozen=True)
class FoodContext:
    child_id: UUID
    now: datetime  # timezone이 붙은 현재 시각
    timezone: tzinfo
    # 실구현 때는 profile.birth_date 에서 코드가 계산한다
    # (영아기/유아기 경계 개월 수는 미정)
    stage: FeedingStage
    # 포트는 DB 연결 전까지 None
    memory: FoodMemoryReader | None = None
    profile: ChildProfileReader | None = None
    safety: SafetyReader | None = None
    menu: DaycareMenuReader | None = None
    nutrition: NutritionSource | None = None

    @property
    def today(self) -> date:
        return today_of(self.now, self.timezone)
