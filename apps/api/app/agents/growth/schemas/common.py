"""Growth tool 스키마가 같이 쓰는 base · enum · 근거 인자."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class ToolArgs(BaseModel):
    """모든 Growth tool argument 의 base. 정의되지 않은 필드는 받지 않는다."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class RoutineCategory(StrEnum):
    """`observation_routine.routine_category` 와 같은 값 (`doc_seed.ROUTINE_CATEGORIES`)."""

    SELF_CARE = "self_care"
    MEALTIME = "mealtime"
    HOUSEHOLD_TASK = "household_task"
    SOCIAL_MANNER = "social_manner"
    HABIT = "habit"
    TRANSITION = "transition"


class EvidencePick(ToolArgs):
    """모델이 고른 근거 하나. 모델은 `id` 를 고르고 `note` 만 쓴다.

    종류 · polarity · 날짜는 출력 tool 이 그 id 를 돌려준 조회 결과에서 채운다 —
    모델에게 맡기면 틀린 값이 들어갈 수 있다. 프롬프트 [예시]의 문서 행 id 도 받는다 —
    `growth_doc` 근거가 되고 개인화로 세지 않는다.
    """

    id: Annotated[
        str,
        Field(description="조회 tool 결과나 [예시]에 있던 id 만. 지어내지 않는다"),
    ]
    note: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "그 기록이나 예시에서 무엇을 근거로 봤는지 한 줄. 보호자 화면에 그대로 나간다"
            ),
        ),
    ]
