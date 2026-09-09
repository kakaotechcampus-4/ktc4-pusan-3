"""Memory Agent 진입점. 이 디렉토리에서 밖으로 열려 있는 유일한 함수.

raw_text 하나를 받아 tool calling을 끝까지 돌리고 결과를 돌려준다.
app/api와 이후의 Supervisor는 내부(registry·prompt·client)를 모른 채 run()만 부른다.

루프가 지는 책임은 세 가지다.
  - 모델이 낸 arguments가 깨져도 루프를 죽이지 않는다
  - 같은 인자로 성공한 쓰기 작업을 두 번 실행하지 않는다
  - 끝나지 않는 대화를 끊고, 끝났는지 여부를 호출자에게 알린다
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.agents.common.llm_client import LLMClient
from app.agents.memory.context import AgentContext
from app.agents.memory.prompt import build_system_prompt
from app.agents.memory.registry import TOOL_SPECS, execute_tool
from app.agents.memory.result import ErrorCode, fail

logger = logging.getLogger(__name__)

MAX_STEPS = 7                # 복합 발화 대비
MAX_COMPLETION_TOKENS = 1400

# 되돌리기 어려운 tool: 같은 인자로 두 번 성공하면 기록이 두 번 남음
_MUTATING_PREFIXES = ("create_", "update_", "delete_")

_BAD_JSON = "arguments가 올바른 JSON이 아니다. 스키마에 맞는 JSON으로 다시 만든다."
_ALREADY_DONE = "같은 인자로 이미 성공한 작업이다. 다시 부르지 않는다."


@dataclass(frozen=True)
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    result: dict[str, Any]      # ToolResult.to_payload()

    @property
    def success(self) -> bool:
        return bool(self.result.get("success"))


@dataclass
class MemoryAgentResult:
    final_message: str | None           # 끝내지 못했으면 None
    completed: bool                     # MAX_STEPS 안에 마쳤는지 여부
    steps: int
    calls: list[ToolCallRecord] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def tool_names(self) -> list[str]:
        return [call.name for call in self.calls]


async def run(
    raw_text: str,
    context: AgentContext,
    *,
    client: LLMClient | None = None,
    directive: str | None = None,
    max_steps: int = MAX_STEPS,
) -> MemoryAgentResult:
    """발화 한 건을 처리한다. directive는 이후 Supervisor가 넘길 상위 지시다."""
    llm = client or LLMClient()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(context, directive)},
        {"role": "user", "content": raw_text},
    ]
    calls: list[ToolCallRecord] = []
    usage: dict[str, int] = {}
    succeeded: set[tuple[str, str]] = set()

    for step in range(max_steps):
        response = await llm.chat(
            messages=messages, tools=TOOL_SPECS, max_completion_tokens=MAX_COMPLETION_TOKENS
        )
        _accumulate(usage, response.usage)

        tool_calls = getattr(response.message, "tool_calls", None) or []
        if not tool_calls:
            return MemoryAgentResult(
                final_message=response.message.content,
                completed=True,
                steps=step + 1,
                calls=calls,
                usage=usage,
            )

        messages.append(_assistant_message(response.message))
        for call in tool_calls:   # 한 응답에 여러 개가 와도 전부, 온 순서대로 실행한다
            record = await _execute(call, context, succeeded)
            calls.append(record)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(record.result, ensure_ascii=False),
                }
            )

    # 여기까지 왔으면 모델이 마무리 응답을 내지 않아 미완료된 것으로 간주
    logger.warning("memory agent 미완료 max_steps=%d tools=%s", max_steps, [c.name for c in calls])
    return MemoryAgentResult(
        final_message=None, completed=False, steps=max_steps, calls=calls, usage=usage
    )


async def _execute(
    call: Any, context: AgentContext, succeeded: set[tuple[str, str]]
) -> ToolCallRecord:
    name = call.function.name
    arguments = _parse_arguments(call.function.arguments)
    if arguments is None:
        # registry는 dict를 전제
        return _failed(name, {}, ErrorCode.VALIDATION_ERROR, _BAD_JSON)

    key = _dedup_key(name, arguments)
    if key is not None and key in succeeded:
        return _failed(name, arguments, ErrorCode.VALIDATION_ERROR, _ALREADY_DONE)

    result = await execute_tool(name, arguments, context)
    if key is not None and result.success:
        succeeded.add(key)      # 실패한 호출은 고쳐서 다시 부를 수 있어야 한다
    return ToolCallRecord(name=name, arguments=arguments, result=result.to_payload())


def _parse_arguments(raw: str | None) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _dedup_key(name: str, arguments: dict[str, Any]) -> tuple[str, str] | None:
    if not name.startswith(_MUTATING_PREFIXES):
        return None             # 분리·조회는 반복 호출해도 상태 안 바뀜
    return name, json.dumps(arguments, sort_keys=True, ensure_ascii=False)


def _failed(name: str, arguments: dict[str, Any], code: str, message: str) -> ToolCallRecord:
    result = fail("parse", name, code, message)
    return ToolCallRecord(name=name, arguments=arguments, result=result.to_payload())


def _assistant_message(message: Any) -> dict[str, Any]:
    """모델 응답을 다음 요청에 그대로 돌려보낼 형태로 만든다. SDK 객체를 그대로 덤프하지 않는다."""
    payload: dict[str, Any] = {"role": "assistant", "content": message.content}
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in tool_calls
        ]
    return payload


def _accumulate(total: dict[str, int], usage: dict[str, int]) -> None:
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value
