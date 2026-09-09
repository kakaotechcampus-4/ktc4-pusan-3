"""모델이 부른 tool 이름을 실제 함수로 이으며, 인자를 검증하는 지점.

루프가 죽으면 모델이 고칠 기회를 잃기 때문에 어떤 실패도 예외로 올리지 않는다.
전부 ToolResult로 바꿔 모델에게 전달한다.
"""

from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError

from app.agents.memory.context import AgentContext
from app.agents.memory.result import ErrorCode, ToolResult, fail
from app.agents.memory.schemas.tool_defs import TOOL_DEFINITIONS
from app.agents.memory.schemas.tool_schema import ToolDefinition, build_tool_specs
from app.agents.memory.tools.observation import OBSERVATION_HANDLERS
from app.agents.memory.tools.parse_input import parse_input
from app.agents.memory.tools.schedule import SCHEDULE_HANDLERS

ToolHandler = Callable[[AgentContext, Any], Awaitable[ToolResult]]

# 이름-실제 함수 매핑 핸들러
TOOL_HANDLERS: dict[str, ToolHandler] = {
    "parse_input": parse_input,
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


def registered_names() -> list[str]:
    return [name for name in _DEFINITIONS if name in TOOL_HANDLERS]


async def execute_tool(name: str, arguments: dict[str, Any], context: AgentContext) -> ToolResult:
    """tool을 한 번 실행. 이름 조회 -> 인자 검증 -> 실행 순서."""
    definition = _DEFINITIONS.get(name)
    handler = TOOL_HANDLERS.get(name)
    if definition is None or handler is None:
        return fail(
            "parse",
            name,
            ErrorCode.UNKNOWN_TOOL,
            f"'{name}' 은 없는 tool이다. 제공된 tool 중에서 고른다.",
        )

    try:
        args = definition.args.model_validate(arguments)
    except ValidationError as exc:
        return fail(
            "parse",
            name,
            ErrorCode.VALIDATION_ERROR,
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
