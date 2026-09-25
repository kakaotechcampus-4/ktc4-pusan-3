"""모델이 부른 tool 이름을 실제 함수로 이으며, 인자를 검증하는 지점.

모델이 고칠 수 있는 실패(이름·인자·날짜·대상)는
예외로 올리지 않고, 전부 ToolResult로 바꿔 모델에게 전달.
저장소 장애처럼 모델이 고칠 수 없는 실패는 올림
"""

from collections.abc import Collection
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from app.agents.common.tool_schema import ToolDefinition, build_tool_specs
from app.agents.memory.context import AgentContext
from app.agents.memory.result import ErrorCode, ToolResult, fail
from app.agents.memory.schemas.tool_defs import TOOL_DEFINITIONS
from app.agents.memory.tools.observation import OBSERVATION_HANDLERS
from app.agents.memory.tools.schedule import SCHEDULE_HANDLERS

ToolHandler = Callable[[AgentContext, Any], Awaitable[ToolResult]]

# 이름-실제 함수 매핑 핸들러
TOOL_HANDLERS: dict[str, ToolHandler] = {
    **OBSERVATION_HANDLERS,
    **SCHEDULE_HANDLERS,
}
_DEFINITIONS: dict[str, ToolDefinition] = {
    definition.name: definition for definition in TOOL_DEFINITIONS
}

# 실행할 수 없는 tool은 모델에게 보여주지 않음
TOOL_SPECS: list[dict[str, Any]] = build_tool_specs(
    [definition for definition in TOOL_DEFINITIONS if definition.name in TOOL_HANDLERS]
)

_SPECS_BY_NAME: dict[str, dict[str, Any]] = {spec["function"]["name"]: spec for spec in TOOL_SPECS}
_SPECS_CACHE: dict[frozenset[str], list[dict[str, Any]]] = {}


def registered_names() -> list[str]:
    return [name for name in _DEFINITIONS if name in TOOL_HANDLERS]


def specs_for(names: Collection[str]) -> list[dict[str, Any]]:
    """묶음에 해당하는 스펙만 TOOL_SPECS 순서대로. 조합이 몇 개뿐이라 조합별로 한 번만 만든다.

    순서가 매번 같아야 요청 앞부분이 같아져 프롬프트 캐시가 걸린다.
    """
    key = frozenset(names)
    if key not in _SPECS_CACHE:
        _SPECS_CACHE[key] = [spec for name, spec in _SPECS_BY_NAME.items() if name in key]
    return _SPECS_CACHE[key]


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    context: AgentContext,
    *,
    allowed: Collection[str] | None = None,
) -> ToolResult:
    """tool 호출 하나를 검증하고 실행한다.

    처리 순서는 허용 목록 확인, tool 조회, 인자 검증, 실행 순서다.
    allowed: 목록에 포함된 tool만 실행 가능.
    allowed=None: 별도의 제한 없이 등록된 tool 사용.
    """

    if allowed is not None and name not in allowed:
        return fail(
            "parse",
            name,
            ErrorCode.TOOL_NOT_ALLOWED,
            f"'{name}' 은 이번 요청에서 쓸 수 없는 tool이다. 제공된 tool 중에서 고른다.",
        )

    definition = _DEFINITIONS.get(name)
    handler = TOOL_HANDLERS.get(name)
    if definition is None or handler is None:
        return fail(
            "parse",
            name,
            ErrorCode.TOOL_NOT_ALLOWED,
            f"'{name}' 은 없는 tool이다. 제공된 tool 중에서 고른다.",
        )

    try:
        args = definition.args.model_validate(arguments)
    except ValidationError as exc:
        return fail(
            "parse",
            name,
            ErrorCode.INVALID_ARGS,
            f"인자가 스키마와 맞지 않는다: {_summarize(exc)}",
        )

    return await handler(context, args)


def _summarize(exc: ValidationError, limit: int = 3) -> str:
    """모델이 고칠 수 있게 필드와 사유만 짧게 남긴다. 값 원문은 싣지 않는다."""
    parts = [
        f"{'.'.join(str(item) for item in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()[:limit]
    ]
    return " / ".join(parts)


def args_model(name: str) -> type[BaseModel] | None:
    return definition.args if (definition := _DEFINITIONS.get(name)) else None
