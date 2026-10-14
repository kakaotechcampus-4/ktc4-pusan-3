"""Activity Agent 진입점 — 사전 조회 + tool calling 루프 (설계 3-2).

순서
  ① 오늘 날씨를 먼저 판정해 `outdoor_ok` 를 정한다 — 장소 조회를 실내 종류로 좁힐지가 걸려 있다
  ② build_gate 로 월령 · 동의 · 안전 조회 · 위치를 모은다. 안전 조회가 실패했으면 모델 0회로 닫는다
  ③ 문서 행(예시)을 코드가 조회해 id 와 함께 프롬프트에 넣는다. 일반 추천은 이 id 를 근거로 단다
  ④ 모델이 조회 tool 을 부르고 마지막에 propose_activity_candidates 로 후보 3개를 낸다.
     거절되면 사유를 보고 고쳐 다시 낸다 — 같은 진입 안의 루프라 model_calls 가 늘지 않는다
  ⑤ 안전 필터로 빠져 3개가 안 되면 같은 대화에 이어 재호출 1회(model_calls=2). 처음 통과한 것을
     먼저 두고 빈자리만 채운다(`merge_drafts`). 그 뒤 check_count(exhausted=True) → count_notice.
     전부 빠졌는데 재호출도 후보를 못 내면 `failed` 다

저장은 하지 않는다. 결과의 suggestions 를 pipeline 이 저장한다 (공통규약 §3).
pipeline 에는 아직 연결하지 않았다 — Supervisor 라벨 · 추천 저장 콜백이 정해지면 붙인다.
"""

import logging
from collections.abc import Collection
from typing import Any

from app.agents.activity.context import ActivityContext, build_gate
from app.agents.activity.gating import checks_weather
from app.agents.activity.prompt import build_system_prompt, recall_message, request_message
from app.agents.activity.readouts import READOUTS
from app.agents.activity.registry import closed_readout_key, execute_tool, specs_for, tools_for
from app.agents.activity.result import ToolResult
from app.agents.activity.rules import normalize_activity
from app.agents.activity.schemas.task import task_type_of
from app.agents.activity.tools.docs import search_activity_doc
from app.agents.activity.tools.outing import check_weather
from app.agents.activity.weather import WEATHER_UNCHECKED
from app.agents.common.llm_client import LLMClient
from app.agents.common.result import DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.common.suggestion import (
    MAX_SUGGESTIONS,
    SuggestionDraft,
    check_count,
    count_notice,
    merge_drafts,
)
from app.agents.common.tool_loop import run_tool_loop

logger = logging.getLogger(__name__)

AGENT = "activity"
OUTPUT_TOOL = "propose_activity_candidates"
# 대화 한 번의 왕복 상한. 기억 · 날씨 · 일정 · 장소 조회에 제출과 거절 뒤 재제출까지 들어가는 수
MAX_STEPS = 8
# 후보 3개에 칸이 많아 Memory 보다 넉넉히 둔다
MAX_COMPLETION_TOKENS = 2000


async def run(
    task: DomainTask, context: ActivityContext, *, client: LLMClient | None = None
) -> DomainAgentResult:
    """놀이 추천 하나를 처리한다. 결과는 공통 `DomainAgentResult` 다."""
    task_type = task_type_of(task)
    weather = await check_weather(context, context.today)
    gate = await build_gate(context, outdoor_ok=weather.outdoor_ok)
    context.state.gate = gate
    context.state.weather[context.today] = weather

    closed = closed_readout_key(task_type, gate)
    if closed is not None:
        return DomainAgentResult(
            agent=AGENT,
            task_type=task_type.value,
            status="blocked",
            readouts=(READOUTS.render(closed),),
        )

    allowed = tools_for(task_type, gate)
    examples = await search_activity_doc(
        context.ports.docs, months=gate.stage.months, query=" ".join(task.request_texts)
    )
    # 프롬프트에 실은 행만 근거로 달 수 있다. 재호출도 같은 system 프롬프트라 그대로 쓴다
    context.state.seen_docs = {row.id: row for row in examples}
    system = build_system_prompt(context, examples, weather=weather)
    llm = client or LLMClient(role="activity")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": request_message(task.request_texts)},
    ]

    if not await _converse(llm, messages, context, allowed):
        return _failed(task_type.value, model_calls=1)

    picked = _picked(context)
    model_calls = 1
    if context.state.excluded and len(picked) < MAX_SUGGESTIONS:
        # 안전 필터로 빠졌다. 사유 없이 제외 목록만 주고 1회 더 부른다 (Tool_공통.md §5-2)
        model_calls = 2
        again = await _recall(llm, messages, task, context, allowed, picked)
        if again is None and not picked:
            # 처음 것이 전부 빠졌고 재호출도 후보를 못 냈다. "조건에 맞는 추천이 없어요" 가 아니라
            # 모델이 실패한 것이다 — 1~2개가 남았으면 그것을 낸다 (#359 리뷰)
            return _failed(task_type.value, model_calls=model_calls)
        picked = merge_drafts(picked, again or (), key=_same_activity)
        check_count(tuple(draft for draft, _ in picked), exhausted=True)

    drafts = tuple(draft for draft, _ in picked)
    # 🚨 확인 문구(state.cautions)와 위험 경고(state.warnings)는 아직 결과에 싣지 않는다 — 화면
    # 자리가 정해지지 않았다. pipeline 은 for_task() 사본으로 Agent 를 돌리고 결과만 받으므로,
    # 연결할 때 결과로 옮기지 않으면 표시 없이 빠진다 (#359 리뷰)
    context.state.suggestions = drafts
    context.state.warnings = tuple(warning for _, warning in picked)
    notice = count_notice(len(drafts))
    attempted = checks_weather(gate)
    return DomainAgentResult(
        agent=AGENT,
        task_type=task_type.value,
        status="degraded" if attempted and WEATHER_UNCHECKED in weather.notices else "completed",
        suggestions=drafts,
        readouts=(
            *(READOUTS.render(key) for key in weather.notices if attempted),
            *((notice,) if notice else ()),
        ),
        model_calls=model_calls,
    )


Picked = tuple[tuple[SuggestionDraft, tuple[str, ...]], ...]


def _picked(context: ActivityContext) -> Picked:
    """출력 tool 이 통과시킨 후보와 그 경고 문구를 짝으로. 합칠 때 순서가 같이 간다."""
    return tuple(zip(context.state.suggestions, context.state.warnings, strict=True))


def _same_activity(item: tuple[SuggestionDraft, tuple[str, ...]]) -> str:
    """재호출 결과에서 "같은 후보" — 최근 중복 검사와 같은 정규화다 (Tool_공통.md §5-2)."""
    return normalize_activity(item[0].content)


def _failed(task_type: str, *, model_calls: int) -> DomainAgentResult:
    return DomainAgentResult(
        agent=AGENT, task_type=task_type, status="failed", model_calls=model_calls
    )


async def _recall(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    task: DomainTask,
    context: ActivityContext,
    allowed: Collection[str],
    first: Picked,
) -> Picked | None:
    """재호출 1회. 같은 대화에 제외 목록 한 줄을 더해 이어 부른다 (Tool_공통.md §5-2).

    새 대화로 시작하면 모델이 기억 · 날씨 조회를 처음부터 다시 해 왕복이 늘고, pipeline 의 20초에
    걸리면 처음 통과한 것까지 잃는다. 이어 쓰면 앞의 조회 결과를 그대로 본다.
    후보를 못 내면 None 이다 — 0개 통과(전부 안전 필터로 빠짐)와 구별한다.
    """
    messages.append(
        {
            "role": "user",
            "content": recall_message(
                task.request_texts,
                chosen=[draft.content for draft, _ in first],
                excluded=context.state.excluded,
            ),
        }
    )
    context.state.suggestions, context.state.warnings, context.state.excluded = (), (), ()
    if not await _converse(llm, messages, context, allowed):
        logger.warning("Activity 재호출이 후보를 내지 못했다 run_id=%s", context.run_id)
        return None
    return _picked(context)


async def _converse(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    context: ActivityContext,
    allowed: Collection[str],
) -> bool:
    """대화 한 번. 출력 tool 이 통과하면 True — 결과는 run state 에 있다.

    루프는 공통 `run_tool_loop` 다. `messages` 에 응답과 tool 결과를 이어 붙인다 — 재호출이 같은
    목록에 이어 쓴다. 한 응답에 tool 이 여러 개 오면 온 순서대로 다 실행하고, 깨진 인자는 실행하지
    않고 돌려준다. `tool_choice="required"` 라 말로 끝나지 않는다 — 라이브에서 auto 는 tool 을
    0/3 부르고 required 는 3/3 불렀다 (#320 리뷰). 그래도 말로 끝나거나 왕복 상한을 넘기면 False 다.
    """

    async def execute(name: str, arguments: dict[str, Any]) -> ToolResult:
        return await execute_tool(name, arguments, context, allowed=allowed)

    loop = await run_tool_loop(
        llm,
        messages,
        tools=specs_for(allowed),
        execute=execute,
        output_tools=(OUTPUT_TOOL,),
        max_steps=MAX_STEPS,
        tool_choice="required",
        max_completion_tokens=MAX_COMPLETION_TOKENS,
    )
    if not loop.completed:
        logger.warning(
            "Activity 가 후보를 내지 못했다 run_id=%s ended_by=%s", context.run_id, loop.ended_by
        )
    return loop.completed
