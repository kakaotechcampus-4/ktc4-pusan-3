"""영아기 이유식 단계 보조 인자.
개월 수 → 이유식 단계는 코드가 계산하고, 단계별 기준은 참고 자료에서 온다.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from app.agents.food.schemas.common import ToolArgs


class WeaningTopic(StrEnum):
    NEW_INGREDIENTS = "new_ingredients"  # 새로 도입해 볼 식재료
    TEXTURE = "texture"  # 입자 크기·질감
    GENERAL = "general"  # 단계 전반


class GuideWeaningStageArgs(ToolArgs):
    topic: Annotated[
        WeaningTopic,
        Field(
            default=WeaningTopic.GENERAL,
            description="new_ingredients(새로 먹여 볼 재료) / texture(질감) / general 중 하나",
        ),
    ]
