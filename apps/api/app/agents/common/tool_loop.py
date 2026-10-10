"""도메인 Agent의 tool calling 루프. Food · Activity · Growth · Health 가 같이 쓴다.

Memory 루프(`memory/agent.py`)와 다른 점은 끝나는 곳이다. 도메인 Agent는 말로 끝내지 않고
출력 tool로 결과를 낸다. 출력 tool 이 검증을 통과하면 그 스텝에서 끝나고 모델을 다시 부르지 않는다.
통과하지 못하면 사유를 돌려받은 모델이 고쳐서 다시 낸다. 같은 진입 안의 루프라 `model_calls` 가
늘지 않고, 왕복 수는 `steps`로 센다 (Agent_공통규약.md §7).

루프가 지는 책임
  - 모델이 낸 arguments가 JSON 객체가 아니면 실행하지 않고 `INVALID_ARGS`로 돌려준다
  - 한 응답에 tool이 여러 개 와도 전부, 온 순서대로 실행한다 — 같은 응답의 조회가 먼저 끝나야
    출력 tool이 그 결과로 근거를 대조한다
  - 끝나지 않는 대화를 `max_steps`에서 끊고, 어떻게 끝났는지(`ended_by`)를 알린다

루프 밖에 두는 것
  - 허용 목록 · 핸들러 표 · 인자 검증 — 각 Agent `registry.execute_tool` 이 한다. 루프는 받은
    `execute` 를 부르기만 한다 (공통_구현_계획.md §1)
  - 안전 필터 뒤 재호출 — 재호출 조건이 Agent 마다 다르다. Agent 가 결과를 보고 같은 `messages` 에
    제외 목록을 더해 한 번 더 부른다 (Tool_공통.md §5-2)
  - 저장 완료 판정 — tool 이 성공했다는 사실을 "저장됐다" 의 근거로 내보내지 않는다.
    도메인 쓰기가 확정됐는지는 쓰기 포트가 commit 하고 돌아온 것만 세는 `run_writes` 가 본다

pipeline은 도메인 task를 `asyncio.wait_for` 로 20초에 끊는데, 안에서 취소를 삼키고 값을 돌려주면
wait_for 가 그 값을 그대로 돌려줘서 끊긴 Agent 의 늦은 결과가
성공으로 들어간다 (Agent_공통규약.md §7). tool이 낸 예외도 잡지 않고 그대로 올린다.
"""

import json
import logging
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from typing import Any, Literal

from app.agents.common.llm_client import LLMClient
from app.agents.common.tool_runtime import ErrorCode, ToolResult, fail

logger = logging.getLogger(__name__)

# LLM 왕복 수 상한. tool 건수가 아니다 — 한 왕복에 tool 이 여러 개 와도 1만 오른다.
# Memory(MAX_STEPS = 7)와 같은 값이다. 도메인 실측이 쌓이면 Agent 가 max_steps 로 좁힌다
MAX_STEPS = 7

# Agent 가 `registry.execute_tool` 에 context · allowed 를 묶어 넘긴 것
ExecuteTool = Callable[[str, dict[str, Any]], Awaitable[ToolResult]]

# 루프가 끝난 이유
# output_accepted  출력 tool이 검증을 통과했다. 결과물은 출력 tool이 run state 에 담았다
# no_tool_call     모델이 tool 없이 말로 끝냈다. 도메인 Agent 의 출력 채널이 아니라 결과가 없다
#                  (Memory 루프의 "model" 은 정상 종료라 이름을 따로 뒀다)
# max_steps        상한까지 돌았는데 출력 tool이 통과하지 못했다
EndedBy = Literal["output_accepted", "no_tool_call", "max_steps"]

_BAD_JSON = "arguments가 올바른 JSON 객체가 아니다. 스키마에 맞는 JSON으로 다시 만든다."


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]  # 깨져서 실행하지 않았으면 빈 dict
    result: ToolResult


@dataclass(frozen=True)
class ToolLoopResult:
    """루프가 어떻게 끝났는가. 결과물(추천 · readout)은 출력 tool 이 run state 에 담았다.

    tool 성공을 "저장됐다" 로 읽는 값은 두지 않는다 (#196 멘토).
    """

    ended_by: EndedBy
    steps: int  # LLM 왕복 수. model_calls 가 아니다
    calls: tuple[ToolCall, ...] = ()
    usage: dict[str, int] = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        """출력 tool 이 검증을 통과해 끝났는가. 아니면 Agent 는 결과가 없는 것으로 본다."""
        return self.ended_by == "output_accepted"


async def run_tool_loop(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]],
    execute: ExecuteTool,
    output_tools: Collection[str],
    max_steps: int = MAX_STEPS,
    tool_choice: str | dict[str, Any] = "auto",
    max_completion_tokens: int | None = None,
) -> ToolLoopResult:
    """출력 tool 이 통과할 때까지 tool calling 을 돈다.

    `messages` 에 모델 응답과 tool 결과를 이어 붙인다 — 재호출은 같은 목록에 제외 목록 한 줄을
    더해 다시 부르면 된다. `tools` 는 Agent 의 `specs_for(tools_for(...))` 결과이고,
    `output_tools` 는 그중 결과를 내는 tool 이름(`propose_*` 등)이다.
    """
    calls: list[ToolCall] = []
    usage: dict[str, int] = {}
    for step in range(max_steps):
        response = await llm.chat(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            max_completion_tokens=max_completion_tokens,
        )
        _accumulate(usage, response.usage)

        tool_calls = getattr(response.message, "tool_calls", None) or []
        if not tool_calls:
            # 모델이 쓴 말은 화면에 내지 않는다 — 출력 채널이 아니다
            return ToolLoopResult(
                ended_by="no_tool_call", steps=step + 1, calls=tuple(calls), usage=usage
            )

        messages.append(_assistant_message(response.message))
        done = False
        for tool_call in tool_calls:  # 한 응답에 여러 개가 와도 전부, 온 순서대로 실행한다
            record = await _execute(tool_call, execute)
            calls.append(record)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(record.result.to_payload(), ensure_ascii=False),
                }
            )
            done = done or (record.name in output_tools and record.result.success)
        if done:
            return ToolLoopResult(
                ended_by="output_accepted", steps=step + 1, calls=tuple(calls), usage=usage
            )

    # tool 이름만 남긴다. 인자에는 후보 문장 · 근거 메모가 들어 있다
    logger.warning(
        "도메인 tool 루프 미완료 max_steps=%d tools=%s", max_steps, [c.name for c in calls]
    )
    return ToolLoopResult(ended_by="max_steps", steps=max_steps, calls=tuple(calls), usage=usage)


async def _execute(tool_call: Any, execute: ExecuteTool) -> ToolCall:
    name = tool_call.function.name
    arguments = _parse_arguments(tool_call.function.arguments)
    if arguments is None:
        # registry 는 dict 를 전제한다. 깨진 인자로는 실행하지 않는다 —
        # operation=None 은 실행 전에 거절했다는 뜻이다
        return ToolCall(name, {}, fail(None, name, ErrorCode.INVALID_ARGS, _BAD_JSON))
    return ToolCall(name, arguments, await execute(name, arguments))


def _parse_arguments(raw: str | None) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _assistant_message(message: Any) -> dict[str, Any]:
    """모델 응답을 다음 요청에 그대로 돌려보낼 형태로. SDK 객체를 그대로 덤프하지 않는다.

    tool_call_id 가 짝을 잃으면 provider 가 다음 요청을 거절한다.
    """
    return {
        "role": "assistant",
        "content": message.content,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments},
            }
            for call in message.tool_calls
        ],
    }


def _accumulate(total: dict[str, int], usage: dict[str, int]) -> None:
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value
