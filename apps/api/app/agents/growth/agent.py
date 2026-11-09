"""Growth Agent 진입점 — 게이트 · 코드 닫힘 · tool calling 루프.

순서
  ① build_gate 로 월령 · 동의 · 안전 조회를 모은다. 닫혔으면 코드 문구로 끝 — 모델 0회
  ② 루틴 요청 · 같은 입력의 기록 조각이 의료 처치 · 증상 목록에 걸리면 닫는다 — 모델 0회
  ③ growth_review 는 측정을 그대로 정리한 readout 하나 — 모델 0회
  ④ 0–11개월 루틴은 리듬 안내(`rhythm_info` 행, 없으면 `rhythm.no_row`) 하나 — 모델 0회
  ⑤ 교육은 코드가 문서 행 [예시]를 골라 프롬프트에 싣는다. 모델에게 문서 검색 tool 은 없다.
     [예시]도 근거로 달 아이 기록도 없으면 `doc.no_row` 로 닫는다 — 모델 0회. 단 코드가 요청 주제를
     읽었는데 그 주제의 [예시]만 없으면 닫지 않는다(`state.requested`) — 보호자의 요청이 기준인
     일반 추천이다
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
from app.agents.common.result import AgentStatus, DomainAgentResult, DomainPending
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
from app.agents.growth.gating import EDUCATION_MIN_MONTH, routine_mode
from app.agents.growth.prompt import build_system_prompt, recall_message, request_message
from app.agents.growth.readouts import (
    ASK_LEARNING_CURRENT,
    ASK_NEED_LEVEL,
    ASKED_FIELD,
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
from app.agents.growth.tools.memory import (
    SELF_RELIANCE,
    category_chains,
    has_citable_memory,
    has_topic_memory,
)
from app.agents.growth.tools.routine import ask_question
from app.rules.growth_topic import (
    SCREEN,
    doc_areas,
    doc_tags,
    request_topic,
    routine_scope,
    routine_words,
    screen_use,
    strict_topic,
    topic_word,
)
from app.rules.term_match import normalize

logger = logging.getLogger(__name__)

AGENT = "growth"
# 대화 한 번의 왕복 상한. 기억 조회 두세 번 + 제출 + 거절 뒤 재제출
MAX_STEPS = 8
MAX_COMPLETION_TOKENS = 2000
# 재호출은 run 마감(20초)보다 이만큼 일찍 끝낸다. 마감에 걸리면 처음 통과한 후보까지 버려진다
RECALL_MARGIN_S = 2.0
# 루틴 대화가 자료 없음으로 끝난 뒤 교육 흐름으로 한 번 더 도는 데 필요한 남은 시간(초). 교육 대화는
# 모델 1회 + 재호출이라 이보다 모자라면 넘기지 않고 지금처럼 `doc.no_row` 로 끝낸다
FALLBACK_MIN_S = 8.0
# 넘긴 교육 대화는 run 마감보다 이만큼 일찍 끊고 루틴의 `doc.no_row` 로 끝낸다. 재호출 여유
# (`RECALL_MARGIN_S`)보다 짧아서, 교육의 재호출이 먼저 끊겨 처음 통과한 후보로 끝날 자리가 남는다
FALLBACK_MARGIN_S = 1.0


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
    task: DomainTask,
    context: GrowthContext,
    *,
    client: LLMClient | None,
    from_routine: bool = False,
) -> DomainAgentResult:
    """`from_routine` 은 루틴 대화가 단계 · 전략 행이 없어 `doc.no_row` 로 끝나 교육 흐름으로 넘어온
    run 이다(`_fall_back_to_education`). 라벨은 교육이고, [예시]를 찾지 않는다 — 그 행동의 공식
    행이 없어서 넘어왔고, 다른 영역 행을 근거로 달면 근거가 틀린다. 먼저 쓴 모델 호출 수는 이어서
    센다."""
    task_type = task_type_of(task)
    prior_calls = context.state.model_calls
    if from_routine:
        task_type = GrowthTaskType.LEARNING_SUGGESTION
        gate = await build_gate(context, task_type)
    elif _screen_candidate(task, task_type):
        # 게임 · 영상 고민은 루틴 단계를 낼 사슬이 없다 — 교육 흐름으로 연다. 교육 게이트가 알레르기
        # 조회도 하고(실패하면 교육처럼 닫힌다) 월령도 읽는다. 돌 전이면 교육이 열리지 않으니 루틴
        # (리듬 안내)으로 되돌린다 — 이때만 게이트를 다시 만든다
        gate = await build_gate(context, GrowthTaskType.LEARNING_SUGGESTION)
        if gate.stage.months >= EDUCATION_MIN_MONTH:
            task_type = GrowthTaskType.LEARNING_SUGGESTION
        else:
            gate = await build_gate(context, task_type)
    else:
        gate = await build_gate(context, task_type)
    context.state.gate = gate
    context.state.task_type = task_type.value
    if from_routine:
        # 주제 영역이 아니라 보호자가 말한 루틴 낱말이 이 run 의 주제다. 영역으로 [예시]를 고르거나
        # 후보 글을 거르지 않는다
        context.state.routine_words = routine_words(task.request_texts, task.record_texts)
    else:
        # 되묻기에 답하고 이어서 도는 교육 run 은 맥락에 요청 조각만 남아("어떻게 도와줘야 좋을까?")
        # 무엇을 물었는지 모른다. 물은 주제를 요청 조각에 더해 읽는다
        asked_texts = (
            (*task.request_texts, task.answered_subject)
            if task_type is GrowthTaskType.LEARNING_SUGGESTION and task.answered_subject
            else task.request_texts
        )
        context.state.topic = request_topic(asked_texts, task.record_texts)
        context.state.strict_topic = strict_topic(asked_texts, task.record_texts)
    context.state.routine_scope = routine_scope(task.request_texts, task.record_texts)
    # "보호자가 말했는가" 를 코드가 확인하는 근거다 — 해석한 문장이 아니라 원문을 읽는다
    context.state.said_texts = (*task.originals(), *task.record_texts, *task.answer_texts)
    context.state.recorded_subjects = task.recorded_subjects
    context.state.answered_subject = task.answered_subject

    key = closed_readout_key(task_type, gate)
    if key is None and task_type is GrowthTaskType.ROUTINE_COACHING:
        # 같은 입력의 기록 조각도 본다 — "약 먹기 싫어해. 어떻게 하지?" 는 요청 조각에 약이 없다.
        # 해석한 문장이 낱말을 빼먹어도 닫히게 보호자 원문도 같이 본다
        key = closure_key(_closure_texts(task))
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

    if (
        task_type is GrowthTaskType.ROUTINE_COACHING
        and not from_routine
        and gate.stage.months >= EDUCATION_MIN_MONTH
        and await _no_step_rows(context)
    ):
        # 보호자가 말한 행동의 카테고리에 단계 사슬이 하나도 없다(등원 · 외출) — 모델이 어떤 행동
        # 이름을 골라도 `doc.no_row` 로 끝난다. 루틴 모델을 건너뛰고 그 닫힘에서 넘기는 것과 같은
        # 교육 흐름으로 바로 연다. 시간이 모자라거나 교육 대화가 끝을 못 내도 그 닫힘(`doc.no_row`)
        # 으로 끝난다
        context.state.closing = DOC_NO_ROW
        if _time_left(task) < FALLBACK_MIN_S:
            return _ended(task_type, context)
        return await _fall_back_to_education(
            task, context, client=client or LLMClient(role="growth")
        )

    examples: tuple[DocHit, ...] = ()
    if task_type is GrowthTaskType.LEARNING_SUGGESTION:
        rules = context.state.rules
        assert rules is not None  # 교육이 열렸으면 build_gate 가 채운다
        active = [e for e in context.state.safety_entries if e.status == "active"]
        if not from_routine:
            examples = await search_growth_doc(
                context.ports.docs,
                task_type=task_type,
                months=gate.stage.months,
                # 요청 주제의 영역 행에서 고른다. 주제가 없으면 영역으로 거르지 않는다
                areas=doc_areas(context.state.topic),
                tags=doc_tags(context.state.topic),
                # 같은 날 같은 아이는 같은 [예시] — 재호출도 같은 프롬프트다. 날이 바뀌면 바뀐다
                rotation=f"{context.child_id}|{context.today.isoformat()}",
                # 식품 아닌 알레르기의 대응표 낱말이 든 행은 싣지 않는다
                blocked=rules.related_blocks(active),
            )
        # 프롬프트에 실은 행만 근거로 달 수 있다
        context.state.seen_docs = {hit.row.id: hit.row for hit in examples}
        # 코드가 주제를 읽었는데 그 주제의 [예시] 행이 없다 — 보호자가 정한 요청이 기준인 일반
        # 추천을 낼 수 있다(`review_learning`). 주제를 못 읽은 막연한 요청은 해당하지 않는다.
        # 루틴에서 넘어온 run 은 [예시]를 찾지 않으니 보호자가 말한 루틴 낱말이 그 요청이다
        context.state.requested = (
            bool(context.state.routine_words)
            if from_routine
            else bool(context.state.topic) and not examples
        )
        if not examples and not context.state.requested and not await has_citable_memory(context):
            # 문서 행도 아이 기록도 요청도 근거로 못 단다 — 어떤 후보도 근거 검증을 못 지나서
            # 모델을 부르지 않는다
            context.state.closed_reason = DOC_NO_ROW
            return _result(task_type, "blocked", readouts=(READOUTS.render(DOC_NO_ROW),))

    allowed = tools_for(task_type, gate)
    llm = client or LLMClient(role="growth")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(context, task_type, examples)},
        {"role": "user", "content": request_message(task.request_texts, task.record_texts)},
    ]
    output = OUTPUT_TOOL[task_type]
    context.state.model_calls = prior_calls + 1
    try:
        completed = await _converse(llm, messages, context, allowed, output)
    except UpstreamUnavailable as exc:
        # 도서 검색이 실패 -> 빈 목록이나 캐시로 꾸미지 않고 닫는다
        # 사유는 어댑터가 남긴 단계와 상태 코드(검색어 · 키 · 주소는 없다)
        logger.warning(
            "Growth 도서 검색 실패 run_id=%s model_calls=1 reason=%s", context.run_id, exc
        )
        context.state.closed_reason = CLOSED_BOOK_API
        return _result(
            task_type, "blocked", readouts=(READOUTS.render(CLOSED_BOOK_API),), model_calls=1
        )
    state = context.state
    model_calls = state.model_calls
    if not completed:
        return _result(task_type, "failed", model_calls=model_calls)

    if _no_row_in_routine(task_type, state, from_routine) and _time_left(task) >= FALLBACK_MIN_S:
        return await _fall_back_to_education(task, context, client=llm)
    if state.closing is not None or state.question is not None:
        return _ended(task_type, context)

    picked = state.suggestions
    if state.excluded and len(picked) < state.capacity:
        # 안전 필터 · 금지 표현 · 주제 밖으로 빠졌다. 사유 없이 제외 목록만 주고 1회 더 부른다.
        # run 마감(20초)까지 남은 시간이 모자라면 재호출하지 않는다 — 마감에 걸려 처음 통과한
        # 후보까지 잃느니 그 후보로 끝낸다
        budget = _recall_budget(task)
        again: tuple[SuggestionDraft, ...] | None = None
        if budget is not None and budget <= 0:
            logger.warning("Growth 재호출을 건너뛴다 — 마감이 가깝다 run_id=%s", context.run_id)
        else:
            model_calls += 1
            state.model_calls = model_calls
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
            except UpstreamUnavailable as exc:
                # 재호출에서 다시 찾다가 실패했다. 처음 통과한 책은 그대로 낸다 (부분 결과)
                logger.warning(
                    "Growth 도서 재검색 실패 run_id=%s model_calls=2 reason=%s",
                    context.run_id,
                    exc,
                )
            except TimeoutError:
                logger.warning("Growth 재호출이 마감에 걸렸다 run_id=%s", context.run_id)
        if again is None and not picked:
            return _result(task_type, "failed", model_calls=model_calls)
        picked = merge_drafts(picked, again or (), key=_same(task_type))
        check_count(picked, exhausted=True)

    notice = count_notice(len(picked))
    readouts = (notice,) if notice else ()
    question, pending = await _learning_question(task, context, task_type, picked, from_routine)
    return _result(
        task_type,
        "completed",
        suggestions=picked,
        readouts=readouts,
        question=question,
        pending=pending,
        model_calls=model_calls,
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
        state.task_type or task.task_type,
        status,
        result.model_calls if result is not None else state.model_calls,
        bundle_summary(result.suggestions).kind if result is not None else None,
        _doc_hits(state),
        bool(result.needs_observation) if result is not None else False,
        state.closed_reason,
        int((time.perf_counter() - started) * 1000),
    )


async def _learning_question(
    task: DomainTask,
    context: GrowthContext,
    task_type: GrowthTaskType,
    picked: tuple[SuggestionDraft, ...],
    from_routine: bool,
) -> tuple[str | None, DomainPending | None]:
    """교육 추천에 덧붙이는 질문 하나(`ask.learning_current`)와, 답을 이어받을 자리.

    보호자가 정한 주제의 아이 기록이 아직 없을 때만이다. 일반 추천은 그대로 나가고, 답이 교육
    기록으로 저장되면 같은 요청을 다시 돌아 개인화로 이어진다(pipeline 의 이어받기). 묻지 않는
    경우: 후보가 없을 때 · 이미 답을 받고 도는 run · 화면 고민(방금 한 말이 곧 기록이다) ·
    루틴에서 넘어온 run · 주제 낱말을 못 읽었을 때 · 그 주제의 기록이 이미 있을 때.
    """
    state = context.state
    if (
        task_type is not GrowthTaskType.LEARNING_SUGGESTION
        or from_routine
        or not picked
        or task.answered_subject
        or not state.topic
        or SCREEN in state.topic
    ):
        return None, None
    word = topic_word(task.request_texts, task.record_texts)
    if word is None or await has_topic_memory(context, state.topic):
        return None, None
    question = ask_question(ASK_LEARNING_CURRENT, word)
    state.question = question
    pending = DomainPending(
        agent=AGENT,
        kind=ASK_LEARNING_CURRENT,
        question=question,
        payload={"subject": word, "category": None, "field": ASKED_FIELD[ASK_LEARNING_CURRENT]},
    )
    return question, pending


def _no_row_in_routine(
    task_type: GrowthTaskType, state: GrowthRunState, from_routine: bool
) -> bool:
    """루틴 대화가 그 행동의 단계 · 전략 행이 없어 `doc.no_row` 로 끝났는가. 코드가 보호자가 말한
    루틴 행동(`routine_scope`)을 읽었을 때만이다 — 읽지 못한 행동은 지금처럼 자료 없음이다.
    닫힘(`closed.*`) · 되묻기 · 사슬 끝(`next_step.chain_end`)은 넘기지 않는다."""
    return (
        task_type is GrowthTaskType.ROUTINE_COACHING
        and not from_routine
        and state.closing == DOC_NO_ROW
        and bool(state.routine_scope)
    )


async def _no_step_rows(context: GrowthContext) -> bool:
    """보호자가 말한 루틴 행동의 카테고리에 이 월령의 단계 사슬이 하나도 없는가.

    코드가 행동을 읽었고(`routine_scope`) 그 카테고리가 모두 자립 사슬을 쓰는 것(`SELF_RELIANCE`)
    이며 문서에 사슬이 없을 때만이다. 카테고리에 사슬이 있으면 그 행동의 사슬이 있는지는 모델이
    고른 이름으로 맞춰 봐야 알아서 루틴 모델이 돈다. 행 수는 상수가 아니라 문서에서 읽는다 —
    사슬이 들어오면 저절로 루틴 모델로 되돌아간다. 같은 사슬 조회라 루틴 대화로 가도 문서를 다시
    읽지 않는다.
    """
    scope = context.state.routine_scope
    if not scope or not set(scope) <= SELF_RELIANCE:
        return False
    for category in scope:
        if await category_chains(context, category):
            return False
    return True


def _time_left(task: DomainTask) -> float:
    """run 마감(20초)까지 남은 초. 마감이 없으면 제한이 없다."""
    return float("inf") if task.deadline is None else task.deadline - time.perf_counter()


async def _fall_back_to_education(
    task: DomainTask, context: GrowthContext, *, client: LLMClient
) -> DomainAgentResult:
    """루틴 단계는 행에서만 나온다 — 행이 없는 루틴 행동은 모델이 단계를 쓰게 하지 않고, 교육 흐름의
    활동 제안으로 넘긴다. 루틴이 쓴 상태를 비우고(시작한 모델 호출 수만 이어 센다) 교육으로 돈다.

    교육 대화가 마감(`FALLBACK_MARGIN_S` 앞)까지 끝나지 않거나 결과를 못 내면(`failed`) 넘기기 전
    결과(루틴의 `doc.no_row`)로 끝낸다. 그대로 두면 Growth 가 통째로 실패로 세져 자료 없음 안내까지
    사라진다 — 넘기기가 넘기지 않은 것보다 나쁜 화면을 만들지 않게 한다."""
    no_row = _ended(GrowthTaskType.ROUTINE_COACHING, context)
    reason = context.state.closed_reason
    calls = context.state.model_calls
    context.state.reset()
    context.state.model_calls = calls
    budget = None if task.deadline is None else _time_left(task) - FALLBACK_MARGIN_S
    try:
        result = await asyncio.wait_for(
            _run(task, context, client=client, from_routine=True), budget
        )
    except TimeoutError:
        why = "마감에 걸렸다"
    else:
        if result.status != "failed":
            return result
        why = "결과를 못 냈다"
    logger.warning(
        "Growth 교육 넘기기가 %s — 루틴 자료 없음으로 끝낸다 run_id=%s", why, context.run_id
    )
    state = context.state
    state.task_type = GrowthTaskType.ROUTINE_COACHING.value
    state.closed_reason = reason
    return replace(no_row, model_calls=state.model_calls)


def _screen_candidate(task: DomainTask, task_type: GrowthTaskType) -> bool:
    """루틴 라벨로 온 화면 · 게임 고민을 교육 흐름으로 열어 볼 것인가(월령은 게이트가 정한다).

    루틴 낱말이 없고(`screen_use`) 닫힘(의료 처치 · 증상)에 안 걸릴 때만이다. 닫힘이 먼저 —
    루틴에서 닫히던 말은 교육으로 돌려도 닫혀야 한다.
    """
    return (
        task_type is GrowthTaskType.ROUTINE_COACHING
        and screen_use((*task.request_texts, *task.original_texts), task.record_texts)
        and closure_key(_closure_texts(task)) is None
    )


def _closure_texts(task: DomainTask) -> tuple[str, ...]:
    """닫힘(의료 처치 · 증상)을 볼 글. 요청 · 해석 전 보호자 원문 · 같은 입력의 기록 조각 · 답."""
    return (*task.request_texts, *task.original_texts, *task.record_texts, *task.answer_texts)


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
    pending: DomainPending | None = None,
    model_calls: int = 0,
) -> DomainAgentResult:
    return DomainAgentResult(
        agent=AGENT,
        task_type=task_type.value,
        status=status,
        suggestions=suggestions,
        readouts=readouts,
        needs_observation=(question,) if question else (),
        pending=pending,
        model_calls=model_calls,
    )


def _pending(state: GrowthRunState) -> DomainPending | None:
    """되묻기를 낸 자리. 답을 받는 pipeline 이 보관했다가 Memory 에 넘긴다."""
    if state.question is None or state.asked is None:
        return None
    key, subject, category = state.asked
    if key not in ASKED_FIELD:
        return None
    return DomainPending(
        agent=AGENT,
        kind=key,
        question=state.question,
        payload={"subject": subject, "category": category, "field": ASKED_FIELD[key]},
    )


def _ended(task_type: GrowthTaskType, context: GrowthContext) -> DomainAgentResult:
    """출력 tool 이 추천 없이 끝냈다 — 코드 문구(닫힘 · 단계 없음)나 되묻기 한 줄. 모델 1회."""
    state = context.state
    if state.question is not None:
        # 질문만 내면 run 의 질문 자리를 다른 Agent 가 가졌을 때 카드가 빈다. 0개 안내 한 줄을 같이
        # 실어 카드가 혼자 서게 한다 — 05 묶음 안내(`empty_notice`)도 이 문장이 된다 (#452 리뷰)
        notice = replace(READOUTS.render(ASK_NEED_LEVEL), code="empty")
        return _result(
            task_type,
            "completed",
            readouts=(notice,),
            question=state.question,
            pending=_pending(state),
            model_calls=state.model_calls,
        )
    assert state.closing is not None
    state.closed_reason = state.closing
    return _result(
        task_type,
        "blocked",
        readouts=(READOUTS.render(state.closing),),
        model_calls=state.model_calls,
    )


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
