"""생활 루틴 제출 인자.

단계는 모델이 고르지 않는다 — `search_routine_memory` 가 돌려준 `next_steps` 의 `step_id` 만 쓴다.
`subject` 는 되묻기 문장에 들어가서 짧은 행동 이름만 받는다.
"""

from typing import Annotated

from pydantic import Field

from app.agents.common.suggestion import MAX_SUGGESTIONS
from app.agents.growth.schemas.common import EvidencePick, RoutineCategory, ToolArgs


class RoutineItem(ToolArgs):
    step_id: Annotated[
        str, Field(description="search_routine_memory 의 next_steps 에 있던 step_id 만")
    ]
    next_step: Annotated[str, Field(min_length=1, description="그 단계를 집 상황에 맞춘 한 문장")]
    how: Annotated[str, Field(min_length=1, description="해 보는 방법 한두 문장")]
    evidence: Annotated[
        list[EvidencePick],
        Field(default_factory=list, description="근거로 쓴 생활 루틴 기록"),
    ]
    reason: Annotated[str, Field(min_length=1, description="이 아이에게 지금 이 단계인 이유")]


class ProposeRoutinePlanArgs(ToolArgs):
    category: Annotated[RoutineCategory, Field(description="이 요청의 생활 루틴 카테고리")]
    subject: Annotated[
        str, Field(min_length=1, max_length=20, description="행동 이름. 예: 양치, 손 씻기")
    ]
    items: Annotated[
        list[RoutineItem],
        Field(
            default_factory=list,
            max_length=MAX_SUGGESTIONS,
            description="next_steps 의 단계마다 하나씩. next_steps 가 없으면 비운다",
        ),
    ]
