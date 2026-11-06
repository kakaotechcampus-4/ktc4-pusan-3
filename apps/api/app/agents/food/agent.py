"""Food Agent 진입점.

`build_gate` 로 이번 run 의 Gate 를 계산하고, 닫힌 조합이면 코드 문구로 모델 0회로 끝낸다. 결과는
공통 `DomainAgentResult` 다.

  식단 추천  코드가 후보 풀을 만들어 샘플을 뽑고(`meal_pool`), 문서 행 `[예시]` 를 조회한 뒤
            모델에게
            후보 중 3개를 고르게 한다. 사후 안전 필터로 빠져 3개가 안 되면 새 샘플로 재호출 1회 —
            처음 통과한 것을 먼저 두고 빈자리만 채운다(`merge_drafts`). 풀이 비면 모델을
            부르지 않는다.
  급식 갱신  모델이 조회 · 갱신 tool 을 불러 급식 행을 고친다. 화면 문구는 tool 이 정한다.
  영양소 분석 열려 있어도 모델을 부르지 않는다 — 판정의 식품군 출현을 코드 문장 틀로 낸다(N-17).
  섭취 확인  정해진 음식을 먹여도 되는지 묻는 말. 모델을 부르지 않고 등록 알레르기 · 연령 규칙으로
            코드가 답한다(`food_check`). "겹치는 걸 못 찾음" 이 "괜찮다" 는 아니다.

저장은 하지 않는다. 결과의 suggestions 를 pipeline 이 저장한다 (공통규약 §3).
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from app.agents.common.evidence import RankedEvidence, pick_followup
from app.agents.common.llm_client import LLMClient
from app.agents.common.readout import Readout
from app.agents.common.result import AgentStatus, DomainAgentResult
from app.agents.common.schemas.task import DomainTask
from app.agents.common.suggestion import MAX_SUGGESTIONS, count_notice, merge_drafts
from app.agents.common.tool_loop import run_tool_loop
from app.agents.food.context import FoodContext, MealPick, build_gate, require_safety
from app.agents.food.food_check import answer_food_check, request_notices
from app.agents.food.meal_pool import PoolItem, build_pool, remember_sample, sample_pool
from app.agents.food.prompt import (
    build_system_prompt,
    recall_message,
    request_message,
)
from app.agents.food.readouts import FOLLOWUP_PREFERENCE, POOL_EMPTY, READOUTS
from app.agents.food.registry import (
    answers_by_code,
    closed_readout_key,
    execute_tool,
    output_tools_for,
    specs_for,
    tools_for,
)
from app.agents.food.result import ToolResult
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.schemas.task import task_type_of
from app.agents.food.store.ports import MenuCatalogRow
from app.agents.food.tools.docs import search_food_doc
from app.agents.food.tools.nutrition import appearance_report
from app.agents.food.tools.records import memory_record, prefetch_food_memory
from app.agents.food.tools.safety import filter_food_safety
from app.rules.age import Stage

logger = logging.getLogger(__name__)

AGENT = "food"

# 모델이 tool 을 꼭 불러야 하는가. 둘 다 required 다(`_converse`)
_TOOL_CHOICE: dict[FoodTaskType, str] = {
    FoodTaskType.MEAL_RECOMMENDATION: "required",
    FoodTaskType.DAYCARE_MEAL: "required",
}

# 식단 추천 [예시] 로 보여 주는 문서 행 종류 (RAG_plan Food 절). 이유기는 이유식 단계 · 재료 행이다.
# infant_milk 는 `unsupported.milk_meal` 로 먼저 닫혀 여기 오지 않는다
_EXAMPLE_TYPES: dict[Stage, tuple[str, ...]] = {
    "infant_weaning": ("weaning_stage", "weaning_ingredient", "feeding_tip"),
    "toddler": ("meal_pattern", "feeding_tip"),
    "preschool": ("meal_pattern", "feeding_tip"),
}


async def run(
    task: DomainTask, context: FoodContext, *, client: LLMClient | None = None
) -> DomainAgentResult:
    """Food Agent 진입점."""
    task_type = task_type_of(task)
    gate = await build_gate(context)
    context.state.gate = gate
    context.state.utterance = "\n".join(task.request_texts)

    key = closed_readout_key(task_type, gate)
    if key is not None:
        return DomainAgentResult(
            agent=AGENT,
            task_type=task_type.value,
            status=_closed_status(key),
            readouts=(READOUTS.render(key),),
        )

    if answers_by_code(task_type):
        if task_type == FoodTaskType.FOOD_CHECK:
            return _check_foods_result(context)
        # 영양소 분석 1차 출력 — 모델을 부르지 않는다. 구간은 판정해 두되 문장은 식품군 출현만 쓴다
        return DomainAgentResult(
            agent=AGENT,
            task_type=task_type.value,
            status="completed",
            readouts=await appearance_report(context),
        )

    llm = client or LLMClient(role="food")
    if task_type == FoodTaskType.MEAL_RECOMMENDATION:
        return await _recommend(task, context, llm)
    return await _update_daycare(task, context, llm)


def _check_foods_result(context: FoodContext) -> DomainAgentResult:
    """섭취 확인 — 모델 없이 등록 알레르기 · 연령 규칙으로 코드가 답한다. 저장하지 않는다."""
    gate = context.state.gate
    assert gate is not None  # run() 이 build_gate 를 먼저 부른다
    readouts, question = answer_food_check(
        context.state.utterance,
        require_safety(context),
        consent=gate.consent_child_health,
        stage=gate.stage.stage,
        months=gate.stage.months,
    )
    return DomainAgentResult(
        agent=AGENT,
        task_type=FoodTaskType.FOOD_CHECK.value,
        status="completed",
        readouts=readouts,
        needs_observation=(question,) if question else (),
    )


def _closed_status(key: str) -> AgentStatus:
    """닫힌 문구 키 → 상태. 단계가 지원하지 않는 것(`unsupported.*`)만 따로, 나머지는 게이트다."""
    return "unsupported" if key.startswith("unsupported.") else "blocked"


def _failed(task_type: FoodTaskType, *, model_calls: int) -> DomainAgentResult:
    return DomainAgentResult(
        agent=AGENT, task_type=task_type.value, status="failed", model_calls=model_calls
    )


async def _update_daycare(
    task: DomainTask, context: FoodContext, llm: LLMClient
) -> DomainAgentResult:
    """급식 조회 · 갱신 · 삭제. 모델이 출력 tool(`update_daycare_meal` · `delete_daycare_meal`)을
    부르면 끝난다. 화면 문구는 tool 이 쓰기가 끝난 뒤에 `state.readouts` 에 담은 코드 문구다."""
    gate = context.state.gate
    assert gate is not None  # run() 이 build_gate 를 먼저 부른다
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(context, FoodTaskType.DAYCARE_MEAL)},
        {"role": "user", "content": context.state.utterance},
    ]
    allowed = tools_for(FoodTaskType.DAYCARE_MEAL, gate)
    if not await _converse(llm, messages, context, allowed, FoodTaskType.DAYCARE_MEAL):
        return _failed(FoodTaskType.DAYCARE_MEAL, model_calls=1)
    return DomainAgentResult(
        agent=AGENT,
        task_type=FoodTaskType.DAYCARE_MEAL.value,
        status="completed",
        readouts=context.state.readouts,
        model_calls=1,
    )


async def _recommend(task: DomainTask, context: FoodContext, llm: LLMClient) -> DomainAgentResult:
    """식단 추천 하나. 후보는 코드가 고르고 모델은 그중 3개를 고른다."""
    gate = context.state.gate
    assert gate is not None  # run() 이 build_gate 를 먼저 부른다
    task_type = FoodTaskType.MEAL_RECOMMENDATION
    # 요청 글에 알레르기 · 연령 금지 음식이 있으면 빼고 골랐다고 코드가 안내한다. 풀이 비어도 붙는다
    notices = request_notices(
        task.request_texts,
        require_safety(context),
        stage=gate.stage.stage,
        months=gate.stage.months,
    )

    pool = await build_pool(context)
    if not pool:
        # 안전 · 연령 · 최근 추천으로 다 빠졌다. 모델 없이 추천 없는 안내만 낸다 — 실패가 아니다
        return _recommendation(task_type, (), context, model_calls=0, followup=(), notices=notices)

    sample = sample_pool(pool, seed=f"{context.run_id}:sample")
    remember_sample(context, sample)
    examples = await search_food_doc(
        context.ports.docs,
        months=gate.stage.months,
        row_types=_EXAMPLE_TYPES[gate.stage.stage],
        safety=require_safety(context),
    )
    # 프롬프트에 실은 행만 근거로 달 수 있다. 재호출도 같은 system 프롬프트라 그대로 쓴다
    context.state.seen_docs = {row.id: row for row in examples}
    # 기억 조회를 건너뛰지 않아 전부 일반 추천이 되는 문제를 막는다(#662).
    # 결과는 seen_evidence 에도 적혀서 모델이 인용한 id 를 출력 검증이 같은 표로 대조한다
    records = _shown_records(context, await prefetch_food_memory(context))
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(context, task_type, examples)},
        {
            "role": "user",
            "content": request_message(task.request_texts, _rows(sample), records=records),
        },
    ]
    allowed = tools_for(task_type, gate)
    if not await _converse(llm, messages, context, allowed, task_type):
        return _failed(task_type, model_calls=1)

    picks = context.state.picks
    model_calls = 1
    if context.state.blocked_keys and len(picks) < MAX_SUGGESTIONS and not context.state.exhausted:
        # 안전 필터로 빠졌다. 사유 없이 제외 목록만 주고 1회 더 부른다 (Tool_공통.md §5-2)
        again = await _recall(task, context, llm, messages, allowed, pool, first=picks)
        if again is not None:
            model_calls = 2
            if again.completed:
                picks = merge_drafts(picks, again.picks, key=lambda pick: pick.menu_key)
            elif not picks:
                # 처음 것이 전부 빠졌고 재호출도 후보를 못 냈다. "조건에 맞는 메뉴가 없어요" 가
                # 아니라 모델이 실패한 것이다 — 1~2개가 남았으면 그것을 낸다
                return _failed(task_type, model_calls=2)

    _log_evidence(context, picks)
    return _recommendation(
        task_type,
        picks,
        context,
        model_calls=model_calls,
        followup=await _followup(context),
        notices=notices,
    )


def _shown_records(context: FoodContext, found: tuple[RankedEvidence, ...]) -> list[dict[str, Any]]:
    """선조회 근거 중 안전 필터를 통과한 것만 모델에게 보인다.

    등록 알레르기와 같은 음식의 선호 기록이 남아 있어도(알레르기를 나중에 등록한 아이) 그 이름을
    외부 모델로 내보내지 않는다. 모델이 부르지 않아도 항상 나가는 경로라 코드가 거른다 —
    `_followup` 이 되묻는 후보를 거르는 것과 같은 방식이다. 거른 근거는 모델이 못 본 것이므로
    `seen_evidence` 에서도 뺀다 — 인용하면 `EVIDENCE_REQUIRED` 로 거절된다.
    """
    gate = context.state.gate
    assert gate is not None  # run() 이 build_gate 를 먼저 부른다
    verdict = filter_food_safety(
        [
            MenuCatalogRow(
                menu_key=str(item.ref.id),
                display_name=item.label,
                source="manual",
                resolved=True,
                synced_at=context.now,
                ingredients=(item.label,),
            )
            for item in found
        ],
        require_safety(context),
        gate.stage.stage,
        months=gate.stage.months,
    )
    passed = {row.menu_key for row in verdict.passed}
    shown = [item for item in found if str(item.ref.id) in passed]
    for item in found:
        if str(item.ref.id) not in passed:
            context.state.seen_evidence.pop(item.ref.id, None)
            context.state.prefetched_evidence_ids.discard(item.ref.id)
    return [memory_record(item, context.today) for item in shown]


def _log_evidence(context: FoodContext, picks: tuple[MealPick, ...]) -> None:
    """추천 한 번의 근거 건수. 선조회가 실제 인용으로 이어졌는지 라이브 eval 과 운영 로그에서 센다.

    건수와 run_id 만 남긴다 — 기록 label · 음식 이름 · 알레르기 이름 · 원문은 싣지 않는다.
    선조회 결과가 있는데 아이 기록을 하나도 인용하지 않은 일반 추천이 섞였으면 경고로 센다. 그래도
    개인화를 강제하지 않는다 — 후보와 무관한 기록이면 일반 추천이 맞다.
    """
    state = context.state
    kinds = [pick.draft.kind for pick in picks]
    cited = {
        citation.ref.id
        for pick in picks
        for citation in pick.draft.citations
        if citation.ref.is_child_record
    }
    personalized = kinds.count("personalized")
    general = kinds.count("general")
    logger.info(
        "Food 추천 근거 run_id=%s prefetched=%d seen=%d cited=%d personalized=%d general=%d "
        "model_search=%s",
        context.run_id,
        len(state.prefetched_evidence_ids),
        len(state.seen_evidence),
        len(cited),
        personalized,
        general,
        state.memory_search_calls > 0,
    )
    if state.prefetched_evidence_ids and not cited and general:
        logger.warning(
            "Food 가 선조회한 아이 기록 근거를 쓰지 않았다 run_id=%s prefetched=%d general=%d",
            context.run_id,
            len(state.prefetched_evidence_ids),
            general,
        )


def _recommendation(
    task_type: FoodTaskType,
    picks: tuple[MealPick, ...],
    context: FoodContext,
    *,
    model_calls: int,
    followup: tuple[str, ...],
    notices: tuple[Readout, ...] = (),
) -> DomainAgentResult:
    drafts = tuple(pick.draft for pick in picks)
    # 개수 안내는 합친 결과로 센다 — 재호출 때 출력 tool 이 본 것은 재호출분뿐이다
    notice = count_notice(len(drafts), empty=READOUTS.get(POOL_EMPTY))
    return DomainAgentResult(
        agent=AGENT,
        task_type=task_type.value,
        status="completed",
        suggestions=drafts,
        readouts=(*notices, notice) if notice else notices,
        # 추천을 못 냈으면 되묻지 않는다. 되묻기는 한 번에 하나다(`pick_followup`)
        needs_observation=followup if drafts else (),
        model_calls=model_calls,
    )


@dataclass(frozen=True)
class _Recall:
    """재호출 결과. `completed` 가 아니면 모델이 출력 tool 을 못 끝낸 것이다."""

    completed: bool
    picks: tuple[MealPick, ...]


async def _recall(
    task: DomainTask,
    context: FoodContext,
    llm: LLMClient,
    messages: list[dict[str, Any]],
    allowed: Collection[str],
    pool: tuple[PoolItem, ...],
    *,
    first: tuple[MealPick, ...],
) -> _Recall | None:
    """재호출 1회. 같은 대화에 제외 목록과 새 후보 목록을 더해 이어 부른다 (Tool_공통.md §5-2).

    새 대화로 시작하면 모델이 기억 조회를 처음부터 다시 해 왕복이 늘고, pipeline 의 20초에 걸리면
    처음 통과한 것까지 잃는다. 풀에서 빠진 것(`blocked_keys`)과 이미 고른 것을 빼고 다시 뽑는다.
    남은 후보가 없으면 부를 이유가 없어서 None 이다. 제외 목록은 `menu_key` 만 넣고 사유는 없다.
    """
    blocked = set(context.state.blocked_keys)
    chosen = [pick.menu_key for pick in first]
    rest = tuple(item for item in pool if item.row.menu_key not in {*blocked, *chosen})
    if not rest:
        return None
    sample = sample_pool(rest, seed=f"{context.run_id}:recall")
    remember_sample(context, sample, recalled=True)
    messages.append(
        {
            "role": "user",
            "content": recall_message(
                task.request_texts, avoid=[*chosen, *sorted(blocked)], sample=_rows(sample)
            ),
        }
    )
    context.state.picks = ()
    context.state.notice = None
    completed = await _converse(llm, messages, context, allowed, FoodTaskType.MEAL_RECOMMENDATION)
    return _Recall(completed=completed, picks=context.state.picks)


async def _followup(context: FoodContext) -> tuple[str, ...]:
    """선호인지 기피인지 모르는 후보 하나를 되묻는다. 없으면 질문이 없다.

    🚨 아이가 먹지 못하는 식품은 되묻지 않는다 — "땅콩은 좋아하는 편인가요?" 는 알레르기 아이의
    보호자에게 먹여 보라는 신호로 읽힌다. 후보를 메뉴 행처럼 `filter_food_safety` 에 걸어 통과한
    것만
    남긴다(알레르기 · 질환 · 연령 규칙을 메뉴와 같은 사전으로 본다).
    """
    gate = context.state.gate
    assert gate is not None  # run() 이 build_gate 를 먼저 부른다
    affinities = await context.ports.memory.affinities(child_id=context.child_id)
    unknown = [row for row in affinities if row.state == "candidate" and row.polarity is None]
    verdict = filter_food_safety(
        [
            MenuCatalogRow(
                menu_key=str(row.id),
                display_name=row.merge_key,
                source="manual",
                resolved=True,
                synced_at=context.now,
                ingredients=(row.merge_key,),
            )
            for row in unknown
        ],
        require_safety(context),
        gate.stage.stage,
        months=gate.stage.months,
    )
    passed = {item.menu_key for item in verdict.passed}
    row = pick_followup(tuple(row for row in unknown if str(row.id) in passed))
    if row is None:
        return ()
    return (READOUTS.render(FOLLOWUP_PREFERENCE, menu=row.merge_key).body,)


def _rows(sample: tuple[PoolItem, ...]) -> list[MenuCatalogRow]:
    return [item.row for item in sample]


async def _converse(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    context: FoodContext,
    allowed: Collection[str],
    task_type: FoodTaskType,
) -> bool:
    """대화 한 번. 출력 tool 이 통과하면 True — 결과는 run state 에 있다.

    루프는 공통 `run_tool_loop` 다. `messages` 에 응답과 tool 결과를 이어 붙인다 — 재호출이 같은
    목록에 이어 쓴다. `tool_choice="required"` 라 말로 끝나지 않는다 — Activity 라이브에서 auto 는
    tool 을 0/3 부르고 required 는 3/3 불렀다(#320 리뷰). Food 급식 갱신도 같았다 — auto 에서는
    "다음 주 화요일 급식에 우유 빼고 두유로 해줘" 같은 분명한 지시에도 말로 답해 12건 중 4건이
    failed 였다(`tests/eval/agents/food/test_food_run.py`). 대신 묻는 말 · 가정도 쓰기로 올 수
    있어서 쓰지 않고 끝내는 `keep_daycare_meal` 을 열어 두고, 갱신 · 삭제 tool 이 원문을 코드로
    다시 본다(`daycare_intent`). 날짜를 못 정하면 tool 이 코드 문구로 되묻는다.
    말로 끝나거나 왕복 상한을 넘기면 False 다.
    """

    async def execute(name: str, arguments: dict[str, Any]) -> ToolResult:
        return await execute_tool(name, arguments, context, allowed=allowed)

    loop = await run_tool_loop(
        llm,
        messages,
        tools=specs_for(allowed),
        execute=execute,
        output_tools=output_tools_for(task_type),
        tool_choice=_TOOL_CHOICE[task_type],
    )
    if not loop.completed:
        # 원문은 남기지 않는다 — run_id 와 종료 사유만
        logger.warning(
            "Food 가 출력 tool 을 못 끝냈다 run_id=%s task=%s ended_by=%s",
            context.run_id,
            task_type.value,
            loop.ended_by,
        )
    return loop.completed
