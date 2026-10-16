"""Growth Agent 진입점 — 게이트 · 코드 닫힘 · tool calling 루프.

순서
  ① build_gate 로 월령 · 동의 · 안전 조회를 모은다. 닫혔으면 코드 문구로 끝 — 모델 0회
  ② 루틴 요청이 의료 처치 · 증상 목록에 걸리면 닫는다 — 모델 0회
  ③ growth_review 는 측정을 그대로 정리한 readout 하나 — 모델 0회
  ④ 0–11개월 루틴은 리듬 안내(`rhythm_info` 행, 없으면 `rhythm.no_row`) 하나 — 모델 0회
  ⑤ 교육은 코드가 문서 행 [예시]를 골라 프롬프트에 싣는다. 모델에게 문서 검색 tool 은 없다
  ⑥ 공통 `run_tool_loop` 로 대화한다. 출력 tool 이 통과하면 끝난다
  ⑦ 안전 필터 · 금지 표현으로 모자라면 같은 대화에 제외 목록 한 줄을 더해 1회 더 부른다
     (model_calls=2). 처음 통과한 것을 먼저 두고 빈자리만 채운다(`merge_drafts`)

도서(`book_suggestion`)는 포트가 있어도 아직 모델 경로가 없다 — `unsupported`. 저장은 하지 않는다 —
결과의 suggestions 를 pipeline 이 저장한다.
pipeline 에 등록돼 있다 — context 는 entrypoint 가 만든다.
"""

import logging
from collections.abc import Callable, Collection
from typing import Any

from app.agents.common.llm_client import LLMClient
from app.agents.common.readout import Readout
from app.agents.common.result import AgentStatus, DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.common.suggestion import (
    SuggestionDraft,
    check_count,
    count_notice,
    merge_drafts,
)
from app.agents.common.tool_loop import run_tool_loop
from app.agents.growth.context import GrowthContext, build_gate
from app.agents.growth.gating import routine_mode
from app.agents.growth.prompt import build_system_prompt, recall_message, request_message
from app.agents.growth.readouts import CAUTION_NON_FOOD_ALLERGY, READOUTS, RHYTHM_NO_ROW
from app.agents.growth.registry import (
    OUTPUT_TOOL,
    closed_readout_key,
    execute_tool,
    specs_for,
    tools_for,
)
from app.agents.growth.result import ToolResult
from app.agents.growth.review import step_of
from app.agents.growth.schemas.task import GrowthTaskType, task_type_of
from app.agents.growth.tools.closures import closure_key
from app.agents.growth.tools.delta import asks_judgement, compute_growth_delta
from app.agents.growth.tools.docs import DocHit, search_growth_doc
from app.rules.term_match import normalize

logger = logging.getLogger(__name__)

AGENT = "growth"
# 대화 한 번의 왕복 상한. 기억 조회 두세 번 + 제출 + 거절 뒤 재제출
MAX_STEPS = 8
MAX_COMPLETION_TOKENS = 2000


async def run(
    task: DomainTask, context: GrowthContext, *, client: LLMClient | None = None
) -> DomainAgentResult:
    """Growth 요청 하나를 처리한다. 결과는 공통 `DomainAgentResult` 다."""
    task_type = task_type_of(task)
    gate = await build_gate(context, task_type)
    context.state.gate = gate

    key = closed_readout_key(task_type, gate)
    if key is None and task_type is GrowthTaskType.ROUTINE_COACHING:
        key = closure_key(task.request_texts)
    if key is not None:
        return _result(task_type, "blocked", readouts=(READOUTS.render(key),))

    if task_type is GrowthTaskType.GROWTH_REVIEW:
        assert context.state.birth_date is not None  # build_gate 가 채운다
        readout = compute_growth_delta(
            context.state.measurements,
            birth_date=context.state.birth_date,
            asks_judgement=asks_judgement(task.request_texts),
        )
        return _result(task_type, "completed", readouts=(readout,))

    if task_type is GrowthTaskType.BOOK_SUGGESTION:
        return _result(task_type, "unsupported")

    if (
        task_type is GrowthTaskType.ROUTINE_COACHING
        and routine_mode(gate.stage.months, None) == "rhythm_info"
    ):
        return _result(task_type, "completed", readouts=(await _rhythm(context),))

    examples: tuple[DocHit, ...] = ()
    if task_type is GrowthTaskType.LEARNING_SUGGESTION:
        examples = await search_growth_doc(
            context.ports.docs,
            task_type=task_type,
            months=gate.stage.months,
            # 같은 날 같은 아이는 같은 [예시] — 재호출도 같은 프롬프트다. 날이 바뀌면 바뀐다
            rotation=f"{context.child_id}|{context.today.isoformat()}",
        )
        # 프롬프트에 실은 행만 근거로 달 수 있다
        context.state.seen_docs = {hit.row.id: hit.row for hit in examples}

    allowed = tools_for(task_type, gate)
    llm = client or LLMClient(role="growth")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(context, task_type, examples)},
        {"role": "user", "content": request_message(task.request_texts)},
    ]
    output = OUTPUT_TOOL[task_type]
    if not await _converse(llm, messages, context, allowed, output):
        return _result(task_type, "failed", model_calls=1)

    state = context.state
    if state.closing is not None or state.question is not None:
        return _ended(task_type, context)

    picked = state.suggestions
    model_calls = 1
    if state.excluded and len(picked) < state.capacity:
        # 안전 필터 · 금지 표현으로 빠졌다. 사유 없이 제외 목록만 주고 1회 더 부른다
        model_calls = 2
        state.done_steps = frozenset(step for draft in picked if (step := step_of(draft)))
        again = await _recall(llm, messages, task, context, allowed, output, picked)
        if again is None and not picked:
            return _result(task_type, "failed", model_calls=model_calls)
        picked = merge_drafts(picked, again or (), key=_same(task_type))
        check_count(picked, exhausted=True)

    notice = count_notice(len(picked))
    readouts = (
        *(_caution(text) for text in state.cautions),
        *((notice,) if notice else ()),
    )
    return _result(
        task_type, "completed", suggestions=picked, readouts=readouts, model_calls=model_calls
    )


def _result(
    task_type: GrowthTaskType,
    status: AgentStatus,
    *,
    suggestions: tuple[SuggestionDraft, ...] = (),
    readouts: tuple[Readout, ...] = (),
    question: str | None = None,
    model_calls: int = 0,
) -> DomainAgentResult:
    return DomainAgentResult(
        agent=AGENT,
        task_type=task_type.value,
        status=status,
        suggestions=suggestions,
        readouts=readouts,
        needs_observation=(question,) if question else (),
        model_calls=model_calls,
    )


def _ended(task_type: GrowthTaskType, context: GrowthContext) -> DomainAgentResult:
    """출력 tool 이 추천 없이 끝냈다 — 코드 문구(닫힘 · 단계 없음)나 되묻기 한 줄. 모델 1회."""
    state = context.state
    if state.question is not None:
        return _result(task_type, "completed", question=state.question, model_calls=1)
    assert state.closing is not None
    return _result(task_type, "blocked", readouts=(READOUTS.render(state.closing),), model_calls=1)


async def _rhythm(context: GrowthContext) -> Readout:
    """0–11개월 루틴 — `rhythm_info` 행 본문 하나. 없으면 `rhythm.no_row`."""
    gate = context.state.gate
    assert gate is not None
    hits = await search_growth_doc(
        context.ports.docs, task_type=GrowthTaskType.ROUTINE_COACHING, months=gate.stage.months
    )
    if not hits:
        return READOUTS.render(RHYTHM_NO_ROW)
    row = hits[0].row
    return Readout(kind="rhythm_info", title=row.title, body=row.body, authored_by="code")


def _caution(text: str) -> Readout:
    return Readout(kind=READOUTS.get(CAUTION_NON_FOOD_ALLERGY).kind, body=text, authored_by="code")


def _same(task_type: GrowthTaskType) -> Callable[[SuggestionDraft], object]:
    """재호출 결과에서 "같은 추천" — 루틴은 옮긴 단계 행, 교육은 정규화한 제목 줄."""
    if task_type is GrowthTaskType.ROUTINE_COACHING:
        return lambda draft: step_of(draft) or normalize(draft.content)
    return lambda draft: normalize(draft.content.split("\n", 1)[0])


async def _recall(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    task: DomainTask,
    context: GrowthContext,
    allowed: Collection[str],
    output: str,
    first: tuple[SuggestionDraft, ...],
) -> tuple[SuggestionDraft, ...] | None:
    """재호출 1회 — 같은 대화에 제외 목록 한 줄을 더한다. 후보를 못 내면 None.

    새 대화로 시작하면 모델이 기억 조회를 처음부터 다시 해 왕복이 늘고, pipeline 의 20초에 걸리면
    처음 통과한 것까지 잃는다. 이어 쓰면 앞의 조회 결과를 그대로 본다.
    """
    state = context.state
    messages.append(
        {
            "role": "user",
            "content": recall_message(
                task.request_texts,
                chosen=[draft.content.split("\n", 1)[0] for draft in first],
                excluded=state.excluded,
            ),
        }
    )
    state.suggestions, state.excluded = (), ()
    if not await _converse(llm, messages, context, allowed, output):
        logger.warning("Growth 재호출이 후보를 내지 못했다 run_id=%s", context.run_id)
        return None
    if state.closing is not None or state.question is not None:
        return None
    return state.suggestions


async def _converse(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    context: GrowthContext,
    allowed: Collection[str],
    output: str,
) -> bool:
    """대화 한 번. 출력 tool 이 통과하면 True — 결과는 run state 에 있다.

    `tool_choice="required"` 라 말로 끝나지 않는다 — Activity 라이브에서 auto 는 tool 을 거의
    안 불렀다(#320). 그래도 말로 끝나거나 왕복 상한을 넘기면 False 다.
    """

    async def execute(name: str, arguments: dict[str, Any]) -> ToolResult:
        return await execute_tool(name, arguments, context, allowed=allowed)

    loop = await run_tool_loop(
        llm,
        messages,
        tools=specs_for(allowed),
        execute=execute,
        output_tools=(output,),
        max_steps=MAX_STEPS,
        tool_choice="required",
        max_completion_tokens=MAX_COMPLETION_TOKENS,
    )
    if not loop.completed:
        logger.warning(
            "Growth 가 결과를 내지 못했다 run_id=%s ended_by=%s", context.run_id, loop.ended_by
        )
    return loop.completed
