"""Activity tool 이름 -> 함수 매핑, 인자 검증, task × Gate로 여닫는 tool 묶음.

어떤 tool을 열지는 모델이 아니라 여기 코드가 정한다. 값은 gating.py 에 있다.

  라벨: activity_recommendation (하나뿐)
    0–35개월   memory · weather · schedule · propose
    36개월+    memory · weather · schedule · (places) · propose

- `(places)` 는 위치가 있고(`has_location`) 야외가 가능할 때(`outdoor_ok`)만 더해진다.
  비가 오면 통째로 닫는다 — 실내 장소 소스가 Kakao 키워드뿐이라서다 (3-2).
- **닫힌 조합이 없다.** 어느 월령에서도 출력 tool 이 열린다. 빈 튜플이 나오면 버그다.
- `safety_ok` · `consent_child_health` 는 어느 tool 도 닫지 않는다. 조회 실패면 재료를 쓰는
  후보만 빼고 고지한다 (D7) — 그건 출력 검증의 일이다.
- 안전 필터 · 중복 제거 · 문서 행 조회는 `CODE_TOOLS` 라 모델이 부를 수 없다.
"""

from collections.abc import Awaitable, Callable, Collection
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agents.activity.context import ActivityContext
from app.agents.activity.gating import opens
from app.agents.activity.result import ErrorCode, ToolResult, fail
from app.agents.activity.schemas.task import ActivityTaskType
from app.agents.activity.schemas.tool_defs import TOOL_DEFINITIONS
from app.agents.activity.tools.docs import search_activity_doc
from app.agents.activity.tools.filters import filter_activity_safety, filter_recent_duplicates
from app.agents.activity.tools.memory import search_activity_memory
from app.agents.activity.tools.outing import (
    lookup_schedule,
    lookup_weather,
    search_nearby_places,
)
from app.agents.activity.tools.recommend import propose_activity_candidates
from app.agents.common.gate import Gate
from app.agents.common.tool_schema import ToolDefinition, build_tool_specs

ToolHandler = Callable[[ActivityContext, Any], Awaitable[ToolResult]]

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "search_activity_memory": search_activity_memory,
    "lookup_weather": lookup_weather,
    "lookup_schedule": lookup_schedule,
    "search_nearby_places": search_nearby_places,
    "propose_activity_candidates": propose_activity_candidates,
}

# 모델에게 보이지 않는 코드 tool. 코드가 정해진 지점에서 직접 부른다.
# rank_evidence(공통)는 search_activity_memory 안에서 부른다
CODE_TOOLS: dict[str, Callable[..., Any]] = {
    "search_activity_doc": search_activity_doc,
    "filter_activity_safety": filter_activity_safety,
    "filter_recent_duplicates": filter_recent_duplicates,
}

_DEFINITIONS: dict[str, ToolDefinition] = {d.name: d for d in TOOL_DEFINITIONS}
TOOL_SPECS: list[dict[str, Any]] = build_tool_specs(
    [d for d in TOOL_DEFINITIONS if d.name in TOOL_HANDLERS]
)
_SPECS_BY_NAME: dict[str, dict[str, Any]] = {spec["function"]["name"]: spec for spec in TOOL_SPECS}

TASK_TOOLS: dict[ActivityTaskType, frozenset[str]] = {
    ActivityTaskType.ACTIVITY_RECOMMENDATION: frozenset(TOOL_HANDLERS),
}
# task 마다 마지막에 한 번 부르는 출력 tool
OUTPUT_TOOL: dict[ActivityTaskType, str] = {
    ActivityTaskType.ACTIVITY_RECOMMENDATION: "propose_activity_candidates",
}


def tools_for(task: ActivityTaskType, gate: Gate) -> tuple[str, ...]:
    """이번 task에 모델에게 열 tool. 순서는 `TOOL_DEFINITIONS` 순서를 따른다."""
    names = TASK_TOOLS[task]
    return tuple(d.name for d in TOOL_DEFINITIONS if d.name in names and opens(d.name, gate))


def specs_for(names: Collection[str]) -> list[dict[str, Any]]:
    return [_SPECS_BY_NAME[name] for name in _SPECS_BY_NAME if name in names]


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    context: ActivityContext,
    *,
    allowed: Collection[str],
) -> ToolResult:
    """tool 을 한 번 실행한다. 허용 목록 → 이름 조회 → 인자 검증 → 실행 순서.

    모델에게 안 보여준 tool 을 이름으로 불러도 실행하지 않는다. allowed 는 tools_for() 결과다.
    코드 tool 은 TOOL_HANDLERS 에 없어 allowed 에 들어 있어도 실행되지 않는다.
    지금 핸들러는 NotImplementedError 를 낸다 — 삼키지 않고 그대로 올린다.
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
            None,
            name,
            ErrorCode.INVALID_ARGS,
            f"인자가 스키마와 맞지 않는다: {_summarize(exc)}",
        )

    return await handler(context, args)


def _summarize(exc: ValidationError, limit: int = 3) -> str:
    """필드와 사유만 짧게. 값 원문(활동명·장소명·재료)은 싣지 않는다."""
    parts = [
        f"{'.'.join(str(item) for item in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()[:limit]
    ]
    return " / ".join(parts)


def args_model(name: str) -> type[BaseModel] | None:
    return definition.args if (definition := _DEFINITIONS.get(name)) else None
