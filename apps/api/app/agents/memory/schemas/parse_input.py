"""복합 발화를 의미 단위로 쪼개는 parse_input 의 argument 스키마.
저장·조회를 하지 않고, 이 입력에 어떤 작업이 들어 있는지 먼저 나눈다.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import Field

from app.agents.memory.schemas.common import ToolArgs


class SegmentIntent(StrEnum):
    """분리된 한 조각이 어떤 작업인지."""

    OBSERVATION_FOOD = "observation_food"
    OBSERVATION_HEALTH = "observation_health"
    OBSERVATION_EDUCATION = "observation_education"
    OBSERVATION_ACTIVITY = "observation_activity"
    OBSERVATION_QUERY = "observation_query"
    OBSERVATION_UPDATE = "observation_update"
    OBSERVATION_DELETE = "observation_delete"
    EVENT_CREATE = "event_create"
    EVENT_QUERY = "event_query"
    EVENT_UPDATE = "event_update"
    EVENT_DELETE = "event_delete"
    EVENT_ITEM_CREATE = "event_item_create"
    REMINDER_CREATE = "reminder_create"
    OUT_OF_SCOPE = "out_of_scope"  # 추천 요청 등 Memory Agent 범위 밖
    UNCLEAR = "unclear"  # 정보가 부족해 되물어야 하는 조각


class InputSegment(ToolArgs):
    text: Annotated[str, Field(description="원문에서 잘라낸 구간. 표현을 바꾸지 않는다")]
    intent: Annotated[SegmentIntent, Field(description="이 구간이 요구하는 작업")]


class ParseInputArgs(ToolArgs):
    segments: Annotated[
        list[InputSegment],
        Field(min_length=1, description="입력에 담긴 작업 목록. 하나뿐이어도 배열로 준다"),
    ]
