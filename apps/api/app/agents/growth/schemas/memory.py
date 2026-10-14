"""기억 조회 tool 인자. 기간은 받지 않는다 — 관찰은 최근 14일, affinity 는 무기한이다."""

from typing import Annotated, Literal

from pydantic import Field

from app.agents.growth.schemas.common import RoutineCategory, ToolArgs

_KEYWORDS = Field(
    default_factory=list,
    max_length=5,
    description="찾을 주제 이름. 예: 숫자, 공룡. 비우면 최근 기록과 관심 전체",
)


class SearchEducationMemoryArgs(ToolArgs):
    keywords: Annotated[list[str], _KEYWORDS]


class SearchActivityMemoryArgs(ToolArgs):
    keywords: Annotated[list[str], _KEYWORDS]


class SearchRoutineMemoryArgs(ToolArgs):
    category: Annotated[
        RoutineCategory | None,
        Field(default=None, description="좁혀 볼 생활 루틴 카테고리. 비우면 전부"),
    ]


class SearchAffinityArgs(ToolArgs):
    # routine 은 affinity 가 없다 — 루틴 관찰은 search_routine_memory 로만 본다
    domains: Annotated[
        list[Literal["education", "activity"]],
        Field(min_length=1, max_length=2, description="education · activity 중 볼 것"),
    ]
