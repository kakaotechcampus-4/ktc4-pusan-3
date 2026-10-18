"""모델에게 보이는 Growth tool 8개의 이름 · 호출 조건 · argument 모델.

description 에는 '무엇을 하는가'보다 '언제 부르는가'를 적는다. 문서 행 조회 · 단계 고르기 ·
안전 필터는 코드 tool 이라 여기 없다 (`gating.CODE_TOOL_NAMES`).
`lookup_notice` 는 아직 정의하지 않는다 — 공지 내용 포트가 운영에 없다. `tools_for` 가 이름을
내도 `specs_for` 가 정의된 것만 싣는다.
"""

from app.agents.common.tool_schema import ToolDefinition
from app.agents.growth.schemas.books import ProposeBooksArgs, SearchBooksArgs
from app.agents.growth.schemas.learning import ProposeLearningActivityArgs
from app.agents.growth.schemas.memory import (
    SearchActivityMemoryArgs,
    SearchAffinityArgs,
    SearchEducationMemoryArgs,
    SearchRoutineMemoryArgs,
)
from app.agents.growth.schemas.routine import ProposeRoutinePlanArgs

TOOL_DEFINITIONS: list[ToolDefinition] = [
    ToolDefinition(
        name="search_education_memory",
        description=(
            "아이가 최근 배운 것 · 관심 있게 본 것과 배움 관심을 근거 순서대로 찾는다. "
            "교육 활동을 고르기 전에 부른다. 결과의 id 만 evidence 로 쓸 수 있다."
        ),
        args=SearchEducationMemoryArgs,
    ),
    ToolDefinition(
        name="search_routine_memory",
        description=(
            "생활 루틴 기록(도움 수준 · 상황)과, 코드가 고른 다음 단계(next_steps)를 가져온다. "
            "생활 루틴을 제안하기 전에 먼저 부른다. step_id 는 이 결과에 있는 것만 쓴다."
        ),
        args=SearchRoutineMemoryArgs,
    ),
    ToolDefinition(
        name="search_activity_memory",
        description=(
            "최근 놀이 기록과 놀이 관심을 찾는다. 관심사를 활동이나 루틴에 이을 때 참고로 부른다."
        ),
        args=SearchActivityMemoryArgs,
    ),
    ToolDefinition(
        name="search_affinity",
        description="확정되거나 쌓이는 중인 관심(배움 · 놀이)만 찾는다. 관심사를 이을 때 부른다.",
        args=SearchAffinityArgs,
    ),
    ToolDefinition(
        name="search_books",
        description=(
            "관심 주제 낱말로 책을 찾는다. 이 나이에 맞는 형태의 책만 돌려준다. "
            "책을 내기 전에 부른다. 결과의 isbn 만 propose_books 에 쓸 수 있다."
        ),
        args=SearchBooksArgs,
    ),
    ToolDefinition(
        name="propose_learning_activity",
        description=(
            "교육 활동 후보 3개를 제출한다. 마지막에 한 번 부른다. "
            "쓰는 물건은 materials 에 빠짐없이 적는다 — 안전 검사는 코드가 한다."
        ),
        args=ProposeLearningActivityArgs,
    ),
    ToolDefinition(
        name="propose_routine_plan",
        description=(
            "생활 루틴 제안을 제출한다. 마지막에 한 번 부른다. next_steps 의 단계마다 하나씩, "
            "next_steps 가 없으면 items 를 비우고 category · subject 만 낸다."
        ),
        args=ProposeRoutinePlanArgs,
    ),
    ToolDefinition(
        name="propose_books",
        description=(
            "책 3권을 제출한다. 마지막에 한 번 부른다. search_books 결과의 isbn 만 쓴다. "
            "낼 책이 모자라면 있는 만큼, 하나도 없으면 items 를 비운다."
        ),
        args=ProposeBooksArgs,
    ),
]
