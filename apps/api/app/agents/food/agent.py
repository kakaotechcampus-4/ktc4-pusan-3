"""Food Agent 진입점.

`build_gate` 로 이번 run 의 Gate 를 계산하고, 닫힌 조합이면 코드 문구로 모델 0회로 끝낸다.
열려 있으면 `run_tool_loop`(공통 tool 루프) 위에서 출력 tool 이 통과할 때까지 돈다.
"""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Literal

from app.agents.common.llm_client import LLMClient
from app.agents.common.readout import Readout
from app.agents.common.schemas.task import DomainTask
from app.agents.common.suggestion import SuggestionDraft
from app.agents.common.tool_loop import ToolCall, ToolLoopResult, run_tool_loop
from app.agents.food.context import FoodContext, build_gate
from app.agents.food.prompt import build_system_prompt
from app.agents.food.readouts import READOUTS
from app.agents.food.registry import (
    closed_readout_key,
    execute_tool,
    output_tools_for,
    requires_safety_check,
    specs_for,
    tools_for,
)
from app.agents.food.result import ToolResult
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.schemas.task import task_type_of
from app.rules.age import Stage

# ok      출력 tool 이 검증을 통과했다
# failed  모델이 말로 끝냈거나(no_tool_call) 상한까지 출력 tool 이 통과하지 못했다(max_steps).
#         추천 0개로 내면 모델 실패가 "말할 게 없다" 로 보인다(#320 PM 리뷰) — 그래서 failed 로 낸다
# closed  게이트 · 라벨 규칙이 닫았다. 코드 문구만 나간다(모델 0회)
FoodStatus = Literal["ok", "failed", "closed"]


@dataclass(frozen=True)
class FoodAgentResult:
    task_type: FoodTaskType
    stage: Stage
    status: FoodStatus
    request_texts: tuple[str, ...]
    tools: tuple[str, ...]  # 이번 task에 모델에게 열리는 tool
    requires_safety_check: bool
    model_calls: int = 0  # Agent 진입 수 — 0(게이트 닫힘) · 1 · 2(안전 필터 후 재호출)
    agent: Literal["food"] = "food"  # pipeline 이 DomainOutcome 으로 읽는다
    readouts: tuple[Readout, ...] = ()
    needs_observation: tuple[str, ...] = ()
    suggestions: tuple[SuggestionDraft, ...] = ()


async def run(
    task: DomainTask, context: FoodContext, *, client: LLMClient | None = None
) -> FoodAgentResult:
    """Food Agent 진입점."""
    task_type = task_type_of(task)
    gate = await build_gate(context)
    context.state.gate = gate
    context.state.utterance = "\n".join(task.request_texts)
    stage = gate.stage.stage

    key = closed_readout_key(task_type, gate)
    if key is not None:
        return FoodAgentResult(
            task_type=task_type,
            stage=stage,
            status="closed",
            request_texts=task.request_texts,
            tools=(),
            requires_safety_check=requires_safety_check(task_type),
            model_calls=0,
            readouts=(READOUTS.render(key),),
        )

    allowed = tools_for(task_type, gate)
    output_tools = output_tools_for(task_type)
    llm = client or LLMClient(role="food")
    messages = [
        {"role": "system", "content": build_system_prompt(context, task_type)},
        {"role": "user", "content": context.state.utterance},
    ]

    async def execute(name: str, arguments: dict[str, Any]) -> ToolResult:
        return await execute_tool(name, arguments, context, allowed=allowed)

    loop_result = await run_tool_loop(
        llm,
        messages,
        tools=specs_for(allowed),
        execute=execute,
        output_tools=output_tools,
    )

    readouts = _readouts_from(task_type, loop_result, output_tools) if loop_result.completed else ()
    return FoodAgentResult(
        task_type=task_type,
        stage=stage,
        status="ok" if loop_result.completed else "failed",
        request_texts=task.request_texts,
        tools=allowed,
        requires_safety_check=requires_safety_check(task_type),
        model_calls=1,
        readouts=readouts,
    )


def _readouts_from(
    task_type: FoodTaskType, loop_result: ToolLoopResult, output_tools: Collection[str]
) -> tuple[Readout, ...]:
    """통과한 출력 tool 의 결과를 화면에 낼 Readout 으로. 식단 추천 · 급식 갱신의 결과 변환은
    각자의 구현 이슈(issue3 등)에서 마저 다룬다 — 지금 실구현된 출력은 영양소 분석뿐이다.
    """
    if task_type != FoodTaskType.NUTRIENT_ANALYSIS:
        return ()
    accepted = _accepted_call(loop_result, output_tools)
    findings = accepted.result.data.get("findings") or []
    if not findings:
        return ()
    return (Readout(kind="nutrient_analysis", body="\n".join(findings), authored_by="model"),)


def _accepted_call(loop_result: ToolLoopResult, output_tools: Collection[str]) -> ToolCall:
    for call in reversed(loop_result.calls):
        if call.result.success and call.name in output_tools:
            return call
    raise AssertionError("output_accepted 인데 성공한 출력 tool 호출이 없다")  # pragma: no cover
