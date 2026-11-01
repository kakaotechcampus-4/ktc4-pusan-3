"""Growth Agent 진입점 — 게이트 · 코드 닫힘 · tool calling 루프.

순서
  ① build_gate 로 월령 · 동의 · 안전 조회를 모은다. 닫혔으면 코드 문구로 끝 — 모델 0회
  ② 루틴 요청 · 같은 입력의 기록 조각이 의료 처치 · 증상 목록에 걸리면 닫는다 — 모델 0회
  ③ growth_review 는 측정을 그대로 정리한 readout 하나 — 모델 0회
  ④ 0–11개월 루틴은 리듬 안내(`rhythm_info` 행, 없으면 `rhythm.no_row`) 하나 — 모델 0회
  ⑤ 교육은 코드가 문서 행 [예시]를 골라 프롬프트에 싣는다. 모델에게 문서 검색 tool 은 없다.
     [예시]도 근거로 달 아이 기록도 없으면 `doc.no_row` 로 닫는다 — 모델 0회
  ⑥ 공통 `run_tool_loop` 로 대화한다. 출력 tool 이 통과하면 끝난다
  ⑦ 안전 필터 · 금지 표현으로 모자라면 같은 대화에 제외 목록 한 줄을 더해 1회 더 부른다
     (model_calls=2). 처음 통과한 것을 먼저 두고 빈자리만 채운다(`merge_drafts`)

도서(`book_suggestion`)는 포트(정보나루 · 이미 낸 책)가 없으면 게이트가 `closed.book_api` 로 닫는다.
검색이 실패하면(`UpstreamUnavailable`) 같은 문구로 닫는다 — 빈 목록이나 캐시로 꾸미지 않는다.
저장은 하지 않는다 — 결과의 suggestions 를 pipeline 이 저장한다.
pipeline 에 등록돼 있다 — context 는 entrypoint 가 만든다.
끝나면 관측 한 줄(`growth run …`)을 남긴다 — 20초에 끊기거나 예외로 끝나도 남긴다. 원문 · 관심사 ·
활동 이름 · 책 제목은 싣지 않는다. pipeline 의 `도메인 task …` 줄과 run_id 로 잇는다.
"""

import asyncio
import logging
import time
from collections.abc import Callable, Collection
from dataclasses import replace
from typing import Any

from app.agents.common.llm_client import LLMClient
from app.agents.common.readout import Readout
from app.agents.common.result import AgentStatus, DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.common.suggestion import (
    SuggestionDraft,
    bundle_summary,
    check_count,
    count_notice,
    merge_drafts,
)
from app.agents.common.tool_loop import run_tool_loop
from app.agents.growth.context import GrowthContext, GrowthRunState, build_gate
from app.agents.growth.gating import routine_mode
from app.agents.growth.prompt import build_system_prompt, recall_message, request_message
from app.agents.growth.readouts import (
    ASK_NEED_LEVEL,
    CLOSED_BOOK_API,
    DOC_NO_ROW,
    READOUTS,
    RHYTHM_NO_ROW,
)
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
from app.agents.growth.store.ports import UpstreamUnavailable
from app.agents.growth.tools.closures import closure_key
from app.agents.growth.tools.delta import asks_judgement, compute_growth_delta
from app.agents.growth.tools.docs import DocHit, search_growth_doc
from app.agents.growth.tools.memory import has_citable_memory
from app.rules.growth_topic import request_topic, routine_scope, strict_topic
from app.rules.term_match import normalize

logger = logging.getLogger(__name__)

AGENT = "growth"
# 대화 한 번의 왕복 상한. 기억 조회 두세 번 + 제출 + 거절 뒤 재제출
MAX_STEPS = 8
MAX_COMPLETION_TOKENS = 2000
# 재호출은 run 마감(20초)보다 이만큼 일찍 끝낸다. 마감에 걸리면 처음 통과한 후보까지 버려진다
RECALL_MARGIN_S = 2.0


async def run(
    task: DomainTask, context: GrowthContext, *, client: LLMClient | None = None
) -> DomainAgentResult:
    """Growth 요청 하나를 처리한다. 결과는 공통 `DomainAgentResult` 다.

    끝나면 관측 한 줄을 남긴다(`_observe`). pipeline 의 20초에 끊기거나 예외로 끝나도 남긴다.
    """
    started = time.perf_counter()
    status = "error"
    result: DomainAgentResult | None = None
    try:
        result = await _run(task, context, client=client)
        status = result.status
        return result
    except asyncio.CancelledError:
        status = "cancelled"  # pipeline 의 20초에 끊겼다
        raise
    finally:
        _observe(task, context, status, result, started)


async def _run(
    task: DomainTask, context: GrowthContext, *, client: LLMClient | None
) -> DomainAgentResult:
    task_type = task_type_of(task)
    gate = await build_gate(context, task_type)
    context.state.gate = gate
    context.state.topic = request_topic(task.request_texts, task.record_texts)
    context.state.strict_topic = strict_topic(task.request_texts, task.record_texts)
    context.state.routine_scope = routine_scope(task.request_texts, task.record_texts)
    context.state.said_texts = (*task.request_texts, *task.record_texts)
    context.state.recorded_subjects = task.recorded_subjects

    key = closed_readout_key(task_type, gate)
    if key is None and task_type is GrowthTaskType.ROUTINE_COACHING:
        # 같은 입력의 기록 조각도 본다 — "약 먹기 싫어해. 어떻게 하지?" 는 요청 조각에 약이 없다
        key = closure_key((*task.request_texts, *task.record_texts))
    if key is not None:
        context.state.closed_reason = key
        return _result(task_type, "blocked", readouts=(READOUTS.render(key),))

    if task_type is GrowthTaskType.GROWTH_REVIEW:
        assert context.state.birth_date is not None  # build_gate 가 채운다
        readout = compute_growth_delta(
            context.state.measurements,
            birth_date=context.state.birth_date,
            asks_judgement=asks_judgement(task.request_texts),
        )
        return _result(task_type, "completed", readouts=(readout,))

    if (
        task_type is GrowthTaskType.ROUTINE_COACHING
        and routine_mode(gate.stage.months, None) == "rhythm_info"
    ):
        return _result(task_type, "completed", readouts=(await _rhythm(context),))

    examples: tuple[DocHit, ...] = ()
    if task_type is GrowthTaskType.LEARNING_SUGGESTION:
        rules = context.state.rules
        assert rules is not None  # 교육이 열렸으면 build_gate 가 채운다
        active = [e for e in context.state.safety_entries if e.status == "active"]
        examples = await search_growth_doc(
            context.ports.docs,
            task_type=task_type,
            months=gate.stage.months,
            # 요청 주제의 영역 행에서 고른다. 주제가 없으면 영역으로 거르지 않는다
            areas=context.state.topic,
            # 같은 날 같은 아이는 같은 [예시] — 재호출도 같은 프롬프트다. 날이 바뀌면 바뀐다
            rotation=f"{context.child_id}|{context.today.isoformat()}",
            # 식품 아닌 알레르기의 대응표 낱말이 든 행은 싣지 않는다
            blocked=rules.related_blocks(active),
        )
        # 프롬프트에 실은 행만 근거로 달 수 있다
        context.state.seen_docs = {hit.row.id: hit.row for hit in examples}
        if not examples and not await has_citable_memory(context):
            # 문서 행도 아이 기록도 못 단다 — 어떤 후보도 build() 를 못 지나서 모델을 부르지 않는다
            context.state.closed_reason = DOC_NO_ROW
            return _result(task_type, "blocked", readouts=(READOUTS.render(DOC_NO_ROW),))

    allowed = tools_for(task_type, gate)
    llm = client or LLMClient(role="growth")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(context, task_type, examples)},
        {"role": "user", "content": request_message(task.request_texts, task.record_texts)},
    ]
    output = OUTPUT_TOOL[task_type]
    context.state.model_calls = 1
    try:
        completed = await _converse(llm, messages, context, allowed, output)
    except UpstreamUnavailable:
        # 도서 검색이 실패했다. 빈 목록이나 캐시로 꾸미지 않고 닫는다 — 모델은 한 번 불렀다
        logger.warning("Growth 도서 검색 실패 run_id=%s", context.run_id)
        context.state.closed_reason = CLOSED_BOOK_API
        return _result(
            task_type, "blocked", readouts=(READOUTS.render(CLOSED_BOOK_API),), model_calls=1
        )
    if not completed:
        return _result(task_type, "failed", model_calls=1)

    state = context.state
    if state.closing is not None or state.question is not None:
        return _ended(task_type, context)

    picked = state.suggestions
    model_calls = 1
    if state.excluded and len(picked) < state.capacity:
        # 안전 필터 · 금지 표현 · 주제 밖으로 빠졌다. 사유 없이 제외 목록만 주고 1회 더 부른다.
        # run 마감(20초)까지 남은 시간이 모자라면 재호출하지 않는다 — 마감에 걸려 처음 통과한
        # 후보까지 잃느니 그 후보로 끝낸다
        budget = _recall_budget(task)
        again: tuple[SuggestionDraft, ...] | None = None
        if budget is not None and budget <= 0:
            logger.warning("Growth 재호출을 건너뛴다 — 마감이 가깝다 run_id=%s", context.run_id)
        else:
            model_calls = 2
            state.model_calls = 2
            state.done_steps = frozenset(step for draft in picked if (step := step_of(draft)))
            if task_type is GrowthTaskType.BOOK_SUGGESTION:
                # 처음 고른 책과 빠진 책은 재호출에서 다시 낼 수 없다 — 이미 낸 책처럼 거절한다
                state.done_books = frozenset(
                    normalize(name) for name in (*(d.content for d in picked), *state.excluded)
                )
            try:
                again = await asyncio.wait_for(
                    _recall(llm, messages, task, context, allowed, output, picked), budget
                )
            except UpstreamUnavailable:
                # 재호출에서 다시 찾다가 실패했다. 처음 통과한 책은 그대로 낸다 (부분 결과)
                logger.warning("Growth 도서 재검색 실패 run_id=%s", context.run_id)
            except TimeoutError:
                logger.warning("Growth 재호출이 마감에 걸렸다 run_id=%s", context.run_id)
        if again is None and not picked:
            return _result(task_type, "failed", model_calls=model_calls)
        picked = merge_drafts(picked, again or (), key=_same(task_type))
        check_count(picked, exhausted=True)

    notice = count_notice(len(picked))
    readouts = (notice,) if notice else ()
    return _result(
        task_type, "completed", suggestions=picked, readouts=readouts, model_calls=model_calls
    )


def _observe(
    task: DomainTask,
    context: GrowthContext,
    status: str,
    result: DomainAgentResult | None,
    started: float,
) -> None:
    """run 하나에 한 줄 (growth_plan §11-5). 원문 · 관심사 · 활동 이름 · 책 제목은 싣지 않는다.

    status 는 결과의 것이고, 20초에 끊기면 cancelled · 예외면 error 다. 결과가 없으면 모델 호출 수는
    시작한 대화 수다. INFO 라 알림(ERROR 이상)으로 가지 않는다.
    """
    state = context.state
    gate = state.gate
    logger.info(
        "growth run run_id=%s child_id=%s stage=%s task_type=%s status=%s model_calls=%d "
        "evidence_mode=%s doc_hit=%d asked=%s closed_reason=%s latency_ms=%d",
        context.run_id,
        context.child_id,
        gate.stage.stage if gate is not None else None,
        task.task_type,
        status,
        result.model_calls if result is not None else state.model_calls,
        bundle_summary(result.suggestions).kind if result is not None else None,
        _doc_hits(state),
        bool(result.needs_observation) if result is not None else False,
        state.closed_reason,
        int((time.perf_counter() - started) * 1000),
    )


def _doc_hits(state: GrowthRunState) -> int:
    """이 run 이 모델에게 넘긴 `growth_doc` 행 수 — 교육 [예시] + 루틴 다음 단계.

    0–11개월 리듬 안내는 세지 않는다. closed_reason 이 rhythm.no_row 가 아니면 행을 찾은 것이다.
    """
    steps = sum(1 for picked in state.steps.values() if picked.step.row is not None)
    return len(state.seen_docs) + steps


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
        # 질문만 내면 run 의 질문 자리를 다른 Agent 가 가졌을 때 카드가 빈다. 0개 안내 한 줄을 같이
        # 실어 카드가 혼자 서게 한다 — 05 묶음 안내(`empty_notice`)도 이 문장이 된다 (#452 리뷰)
        notice = replace(READOUTS.render(ASK_NEED_LEVEL), code="empty")
        return _result(
            task_type, "completed", readouts=(notice,), question=state.question, model_calls=1
        )
    assert state.closing is not None
    state.closed_reason = state.closing
    return _result(task_type, "blocked", readouts=(READOUTS.render(state.closing),), model_calls=1)


async def _rhythm(context: GrowthContext) -> Readout:
    """0–11개월 루틴 — `rhythm_info` 행 본문 하나. 없으면 `rhythm.no_row`.

    한 구간에 행이 여럿이면 교육 [예시]처럼 아이 · 날짜로 돌려 고른다 — 같은 날 같은 아이는 같은
    안내다. 출처를 따라갈 수 있게 그 행을 `source_refs` 에 단다(화면에는 싣지 않는다).
    """
    gate = context.state.gate
    assert gate is not None
    hits = await search_growth_doc(
        context.ports.docs,
        task_type=GrowthTaskType.ROUTINE_COACHING,
        months=gate.stage.months,
        rotation=f"{context.child_id}|{context.today.isoformat()}",
    )
    if not hits:
        context.state.closed_reason = RHYTHM_NO_ROW
        return READOUTS.render(RHYTHM_NO_ROW)
    hit = hits[0]
    return Readout(
        kind="rhythm_info",
        title=hit.row.title,
        body=hit.row.body,
        authored_by="code",
        source_refs=(hit.ref,),
    )


def _same(task_type: GrowthTaskType) -> Callable[[SuggestionDraft], object]:
    """재호출 결과에서 "같은 추천" — 루틴은 옮긴 단계 행, 도서는 "제목(저자)", 교육은 정규화한
    제목 줄."""
    if task_type is GrowthTaskType.ROUTINE_COACHING:
        return lambda draft: step_of(draft) or normalize(draft.content)
    if task_type is GrowthTaskType.BOOK_SUGGESTION:
        return lambda draft: normalize(draft.content)
    return lambda draft: normalize(draft.content.split("\n", 1)[0])


def _recall_budget(task: DomainTask) -> float | None:
    """재호출에 쓸 수 있는 초. 마감이 없으면 None(제한 없음), 0 이하면 재호출하지 않는다."""
    if task.deadline is None:
        return None
    return task.deadline - time.perf_counter() - RECALL_MARGIN_S


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
