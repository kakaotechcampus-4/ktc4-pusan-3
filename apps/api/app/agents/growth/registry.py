"""Growth task와 Gate 조건에 따라 모델에 노출할 tool과 조기 종료 여부를 결정한다.

어떤 tool을 사용할 수 있는지는 모델이 직접 판단하지 않고 이 코드에서 결정한다.
각 Gate의 기준값은 `gating.py`에 정의되어 있다.

task별로 모델에 열리는 tool:

- learning_suggestion
  education / routine / activity / affinity 검색,
  lookup_notice, propose_learning_activity

- routine_coaching
  routine / activity / affinity 검색,
  propose_routine_plan

- book_suggestion
  search_book_interests(교육 · 관심 · 놀이 · 생활 기록을 코드가 점수로 줄 세운다),
  search_books, propose_books

- growth_review
  모델을 호출하지 않는다.

교육 관련 tool인 `search_education_memory`, `lookup_notice`,
`propose_learning_activity`는 12개월 이상부터 사용할 수 있다.
그 외 tool은 0개월부터 사용할 수 있다.

`lookup_notice`는 조회할 공지가 없으면 tool 목록에서 제외하지만,
이 경우에도 추천 자체는 계속 진행한다.

다음 조건에서는 `closed_readout_key`를 반환하고 모델을 호출하지 않는다.

- growth_review + 건강정보 동의 없음
  → closed.consent
- learning_suggestion + 0~11개월
  → closed.infant_learning
- learning_suggestion + 알레르기 조회 실패
  → blocked.safety
- book_suggestion + 도서 API 장애 또는 API key 없음
  → closed.book_api

건강정보 동의 여부로 닫히는 task는 `growth_review`뿐이다.
`learning_suggestion`은 동의가 있는 경우에만 `health_safety`를 조회하고,
동의가 없으면 해당 정보를 읽지 않은 채 진행한다.
`routine_coaching`과 `book_suggestion`은 건강정보를 조회하지 않는다.

`compute_growth_delta`, `check_routine_category`, `pick_next_step`,
`search_growth_doc`, `rank_evidence` 같은 코드 내부 tool은
모델에 노출하지 않는다. 모델에 노출하면 Gate를 우회할 수 있기 때문이다.

모델용 tool 의 정의는 `schemas/tool_defs.py`, 실행은 `execute_tool` 이다. 정의가 없는 이름
(`lookup_notice`)은 `tools_for` 가 내도 스펙에 싣지 않고 실행도 하지 않는다 — 공지 내용 포트가
운영에 없다. 도서 tool 은 도서 포트가 없으면 `closed_readout_key` 가 먼저 닫는다.
"""

from collections.abc import Awaitable, Callable, Collection
from typing import Any

from pydantic import ValidationError

from app.agents.common.gate import Gate
from app.agents.common.tool_schema import ToolDefinition, build_tool_specs
from app.agents.growth.context import GrowthContext
from app.agents.growth.gating import EDUCATION_MIN_MONTH, MIN_MONTH, opens
from app.agents.growth.readouts import (
    BLOCKED_SAFETY,
    CLOSED_BOOK_API,
    CLOSED_CONSENT,
    CLOSED_INFANT_LEARNING,
)
from app.agents.growth.result import ErrorCode, ToolResult, fail
from app.agents.growth.schemas.task import GrowthTaskType
from app.agents.growth.schemas.tool_defs import TOOL_DEFINITIONS
from app.agents.growth.tools.books import search_books_tool
from app.agents.growth.tools.delta import compute_growth_delta
from app.agents.growth.tools.interests import search_book_interests
from app.agents.growth.tools.memory import (
    search_activity_memory,
    search_affinity,
    search_education_memory,
    search_routine_memory,
)
from app.agents.growth.tools.propose import (
    propose_books_tool,
    propose_learning_activity,
    propose_routine_plan,
)
from app.agents.growth.tools.routine import check_routine_category, pick_next_step

ToolHandler = Callable[[GrowthContext, Any], Awaitable[ToolResult]]

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "search_education_memory": search_education_memory,
    "search_routine_memory": search_routine_memory,
    "search_activity_memory": search_activity_memory,
    "search_affinity": search_affinity,
    "search_book_interests": search_book_interests,
    "search_books": search_books_tool,
    "propose_learning_activity": propose_learning_activity,
    "propose_routine_plan": propose_routine_plan,
    "propose_books": propose_books_tool,
}
_DEFINITIONS: dict[str, ToolDefinition] = {d.name: d for d in TOOL_DEFINITIONS}
_SPECS_BY_NAME: dict[str, dict[str, Any]] = {
    spec["function"]["name"]: spec
    for spec in build_tool_specs([d for d in TOOL_DEFINITIONS if d.name in TOOL_HANDLERS])
}

TASK_TOOLS: dict[GrowthTaskType, frozenset[str]] = {
    GrowthTaskType.LEARNING_SUGGESTION: frozenset(
        {
            "search_education_memory",
            "search_routine_memory",
            "search_activity_memory",
            "search_affinity",
            "lookup_notice",
            "propose_learning_activity",
        }
    ),
    GrowthTaskType.ROUTINE_COACHING: frozenset(
        {
            "search_routine_memory",
            "search_activity_memory",
            "search_affinity",
            "propose_routine_plan",
        }
    ),
    GrowthTaskType.BOOK_SUGGESTION: frozenset(
        {"search_book_interests", "search_books", "propose_books"}
    ),
    GrowthTaskType.GROWTH_REVIEW: frozenset(),
}

# task 마다 마지막에 한 번 부르는 출력 tool. growth_review 는 추천을 만들지 않아서 없다
OUTPUT_TOOL: dict[GrowthTaskType, str] = {
    GrowthTaskType.LEARNING_SUGGESTION: "propose_learning_activity",
    GrowthTaskType.ROUTINE_COACHING: "propose_routine_plan",
    GrowthTaskType.BOOK_SUGGESTION: "propose_books",
}

# 모델에게 보이지 않는 코드 tool 중 이 모듈 단계에서 구현된 것. 코드가 정해진 지점에서 직접 부른다.
# `search_growth_doc` · `rank_evidence`(공통, 조회 tool 안에서 부른다)는 아직 연결 전이다
CODE_TOOLS: dict[str, Callable[..., Any]] = {
    "compute_growth_delta": compute_growth_delta,
    "check_routine_category": check_routine_category,
    "pick_next_step": pick_next_step,
}


def closed_readout_key(task: GrowthTaskType, gate: Gate) -> str | None:
    """이 (task, gate) 가 닫혀 있으면 코드 문구 키를, 열려 있으면 None 을 돌려준다.

    닫힌 경로는 모델을 0회 부른다 — `tools_for` 도 이 값이 있으면 빈 튜플을 낸다.
    교육은 월령이 먼저다 — 0–11개월은 안전 정보를 읽지 않아서 `safety_ok` 가 의미 없다.
    """
    if task is GrowthTaskType.GROWTH_REVIEW:
        return None if gate.consent_child_health else CLOSED_CONSENT
    if task is GrowthTaskType.LEARNING_SUGGESTION:
        if gate.stage.months < EDUCATION_MIN_MONTH:
            return CLOSED_INFANT_LEARNING
        if not gate.safety_ok:
            return BLOCKED_SAFETY
    if task is GrowthTaskType.BOOK_SUGGESTION and not gate.data.book_api:
        return CLOSED_BOOK_API
    return None


def tools_for(task: GrowthTaskType, gate: Gate) -> tuple[str, ...]:
    """이번 task에 모델에게 열 tool. 닫혀 있으면 빈 튜플 — 모델을 부르지 않는다.

    순서는 `MIN_MONTH` 의 선언 순서를 따른다.
    """
    if closed_readout_key(task, gate) is not None:
        return ()
    names = TASK_TOOLS[task]
    return tuple(name for name in MIN_MONTH if name in names and opens(name, gate))


def specs_for(names: Collection[str]) -> list[dict[str, Any]]:
    """`tools_for` 결과 중 정의 · 핸들러가 있는 것만, `TOOL_DEFINITIONS` 순서로."""
    return [_SPECS_BY_NAME[name] for name in _SPECS_BY_NAME if name in names]


async def execute_tool(
    name: str, arguments: dict[str, Any], context: GrowthContext, *, allowed: Collection[str]
) -> ToolResult:
    """tool 을 한 번 실행한다. 허용 목록 → 이름 조회 → 인자 검증 → 실행 순서.

    모델에게 안 보여준 tool 을 이름으로 불러도 실행하지 않는다. 핸들러가 낸 예외는 삼키지 않는다.
    """
    definition = _DEFINITIONS.get(name)
    handler = TOOL_HANDLERS.get(name)
    if name not in allowed or definition is None or handler is None:
        return fail(
            None,
            name,
            ErrorCode.TOOL_NOT_ALLOWED,
            f"'{name}' 은 이번 요청에서 쓸 수 없는 tool 이다. 제공된 tool 중에서 고른다.",
        )
    try:
        args = definition.args.model_validate(arguments)
    except ValidationError as exc:
        return fail(
            None, name, ErrorCode.INVALID_ARGS, f"인자가 스키마와 맞지 않는다: {_summarize(exc)}"
        )
    return await handler(context, args)


def _summarize(exc: ValidationError, limit: int = 3) -> str:
    """필드와 사유만 짧게. 값 원문은 싣지 않는다."""
    return " / ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()[:limit]
    )
