"""Growth 기억 조회 (모델 tool 4개).

    search_education_memory   observation_education 14일 + profile_affinity(education)
    search_activity_memory    observation_activity 14일 + profile_affinity(activity) — 참고용
    search_affinity           profile_affinity(education · activity) 만
    search_routine_memory     observation_routine 14일 + 코드가 고른 다음 단계(next_steps)

- 순서는 `common/evidence.rank_evidence` 가 정한다. 모델에게 맡기지 않는다.
- 돌려준 것만 `state.seen_evidence` 에 적는다 — 출력 tool 은 이 표 밖 id 를 거절한다.
- 결과에는 id · 종류 · 티어 · 이름 · polarity · 날짜만 싣는다. 발화 원문은 포트가 싣지 않는다.
- 다음 단계는 모델이 고르지 않는다. 관찰 subject 를 사슬 이름(`docs.match_chain`)에 맞추고,
  14일 안 도움 수준으로 `pick_next_step` 이 바로 다음 칸을 고른다. 맞는 사슬이 없으면 `doc.no_row`
  — 단계를 지어내지 않는다.
- 예절 · 습관은 사슬 대신 전략 행(`docs.strategy_rows`)이다. 예절은 행동 이름, 습관은 trigger 의
  상황 태그(`tools/triggers.py`)로 코드가 고른다. 없으면 같은 `doc.no_row` 다.

코드가 부르는 `has_citable_memory` 는 [예시]가 빈 교육 run 을 모델 전에 닫을지 정한다.
"""

import logging
from collections.abc import Sequence
from datetime import date, timedelta

from app.agents.common.evidence import (
    OBSERVATION_WINDOW_DAYS,
    TOP_N,
    ObservationRow,
    RankedEvidence,
    rank_evidence,
    top_refs,
)
from app.agents.common.suggestion import MAX_SUGGESTIONS
from app.agents.growth.context import GrowthContext
from app.agents.growth.doc_seed import chain_key
from app.agents.growth.readouts import DOC_NO_ROW
from app.agents.growth.result import ErrorCode, ToolResult, fail, ok
from app.agents.growth.schemas.memory import (
    SearchActivityMemoryArgs,
    SearchAffinityArgs,
    SearchEducationMemoryArgs,
    SearchRoutineMemoryArgs,
)
from app.agents.growth.store.ports import GrowthDocRow, RoutineObservation
from app.agents.growth.tools.closures import closure_key
from app.agents.growth.tools.docs import (
    STRATEGY_CATEGORIES,
    match_chain,
    routine_chains,
    strategy_rows,
)
from app.agents.growth.tools.routine import (
    ChainError,
    NextStep,
    PickedStep,
    current_assistance_level,
    pick_next_step,
)
from app.agents.growth.tools.triggers import trigger_tags
from app.rules.growth_topic import expand_keywords
from app.rules.staleness import is_stale
from app.rules.term_match import normalize

logger = logging.getLogger(__name__)

# 모델에게 돌려주는 근거 수 (공통 TOP_N 과 같다)
MEMORY_LIMIT = TOP_N
# keywords 와 subject 를 서로 포함 관계로 볼 때 짧은 쪽의 최소 길이
MIN_KEYWORD_MATCH = 2
# 자립 단계 사슬이 있는 카테고리. 예절 · 습관은 단계가 아니라 전략 행이다
SELF_RELIANCE = frozenset({"self_care", "mealtime", "household_task", "transition"})

_NO_RECORDS = "기록이 없다. 기억 근거 없이 [예시]를 바탕으로 낸다. 보호자에게 되묻지 않는다."
_NO_ROUTINE = (
    "생활 루틴 기록이 없다. propose_routine_plan 을 items 를 비우고 category · subject 만 넣어 "
    "부른다 — 코드가 보호자에게 되묻는다."
)


def _require_gate(context: GrowthContext) -> None:
    if context.state.gate is None:
        raise RuntimeError("Gate 없이 기억 조회가 불렸다 — run() 이 build_gate 를 먼저 부른다")


def _window(context: GrowthContext) -> tuple[date, date]:
    today = context.today
    return today - timedelta(days=OBSERVATION_WINDOW_DAYS), today


def _related(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return len(short) >= MIN_KEYWORD_MATCH and short in long


def _matching(
    ranked: Sequence[RankedEvidence], keywords: Sequence[str]
) -> tuple[RankedEvidence, ...]:
    """keywords 중 하나라도 맞는 근거. 비면 전부다. 줄 세운 뒤에 거르므로 순서는 같다.

    같은 영역의 낱말(`rules/growth_topic`)도 맞는다 — 요청은 "국어" 인데 기록은 "글 읽기" 다.
    """
    wanted = [key for key in map(normalize, expand_keywords(keywords)) if key]
    if not wanted:
        return tuple(ranked)
    return tuple(
        item
        for item in ranked
        if any(
            _related(key, normalize(item.subject)) or _related(key, normalize(item.label))
            for key in wanted
        )
    )


def _remember(context: GrowthContext, ranked: Sequence[RankedEvidence]) -> list[dict]:
    picked = top_refs(ranked, limit=MEMORY_LIMIT)
    context.state.seen_evidence.update(picked)
    return [
        {
            "id": str(item.ref.id),
            "kind": item.ref.kind,
            "tier": item.tier,
            "label": item.label,
            "polarity": item.polarity,
            "observed_on": item.observed_on.isoformat(),
        }
        for item in picked.values()
    ]


async def has_citable_memory(context: GrowthContext) -> bool:
    """교육 대화가 근거로 달 수 있는 아이 기록이 하나라도 있는가. 모델에게 보이지 않는다.

    [예시]가 빈 교육 run 은 이게 없으면 모델 전에 닫는다(`doc.no_row`). 문서 행도 아이 기록도
    못 달면 build() 가 모든 후보를 거절해, 모델이 왕복 상한까지 돌고 failed 로 끝난다.
    범위는 교육에 열린 조회 tool 넷과 같다 — 교육 · 놀이 · 루틴 관찰 14일과 education ·
    activity 관심. 순위는 `rank_evidence`, 6개월 판정은 build() 와 같은 `is_stale` 이다.
    """
    _require_gate(context)
    start, today = _window(context)
    memory = context.ports.memory
    child_id = context.child_id
    affinities = await memory.affinities(child_id=child_id, domains=("education", "activity"))
    education = await memory.education(child_id=child_id, date_from=start, date_to=today)
    activities = await memory.activities(child_id=child_id, date_from=start, date_to=today)
    routine = await memory.routine(child_id=child_id, date_from=start, date_to=today)
    rows = [
        *(("observation_education", row) for row in education),
        *(("observation_activity", row) for row in activities),
        *(("observation_routine", row) for row in routine),
    ]
    ranked = rank_evidence(
        tuple(affinities),
        tuple(
            ObservationRow(
                id=row.id,
                kind=kind,
                subject=row.subject,
                polarity=row.polarity,
                observed_on=row.observed_on,
            )
            for kind, row in rows
        ),
        today=today,
    )
    return any(not is_stale(item.observed_on, today) for item in ranked)


async def search_education_memory(
    context: GrowthContext, args: SearchEducationMemoryArgs
) -> ToolResult:
    _require_gate(context)
    start, today = _window(context)
    memory = context.ports.memory
    rows = await memory.education(child_id=context.child_id, date_from=start, date_to=today)
    affinities = await memory.affinities(child_id=context.child_id, domains=("education",))
    ranked = rank_evidence(
        tuple(affinities),
        tuple(
            ObservationRow(
                id=row.id,
                kind="observation_education",
                subject=row.subject,
                label=row.topic,
                polarity=row.polarity,
                observed_on=row.observed_on,
            )
            for row in rows
        ),
        today=today,
    )
    found = _matching(ranked, args.keywords)
    if not found:
        return fail("query", "search_education_memory", ErrorCode.NO_RECORDS, _NO_RECORDS)
    return ok("query", "search_education_memory", records=_remember(context, found))


async def search_activity_memory(
    context: GrowthContext, args: SearchActivityMemoryArgs
) -> ToolResult:
    """놀이 기록은 **참고**다. Growth 는 읽기만 하고 observation_activity 에 쓰지 않는다."""
    _require_gate(context)
    start, today = _window(context)
    memory = context.ports.memory
    rows = await memory.activities(child_id=context.child_id, date_from=start, date_to=today)
    affinities = await memory.affinities(child_id=context.child_id, domains=("activity",))
    ranked = rank_evidence(
        tuple(affinities),
        tuple(
            ObservationRow(
                id=row.id,
                kind="observation_activity",
                subject=row.subject,
                label=row.activity,
                polarity=row.polarity,
                observed_on=row.observed_on,
            )
            for row in rows
        ),
        today=today,
    )
    found = _matching(ranked, args.keywords)
    if not found:
        return fail("query", "search_activity_memory", ErrorCode.NO_RECORDS, _NO_RECORDS)
    return ok("query", "search_activity_memory", records=_remember(context, found))


async def search_affinity(context: GrowthContext, args: SearchAffinityArgs) -> ToolResult:
    _require_gate(context)
    rows = await context.ports.memory.affinities(
        child_id=context.child_id, domains=tuple(dict.fromkeys(args.domains))
    )
    ranked = rank_evidence(tuple(rows), (), today=context.today)
    if not ranked:
        return fail("query", "search_affinity", ErrorCode.NO_RECORDS, _NO_RECORDS)
    return ok("query", "search_affinity", records=_remember(context, ranked))


async def search_routine_memory(
    context: GrowthContext, args: SearchRoutineMemoryArgs
) -> ToolResult:
    """루틴 관찰 14일과, 코드가 고른 다음 단계 — 자립은 사슬의 다음 칸, 예절 · 습관은 전략 행.

    `next_steps` 에는 단계 행이 있는 것만 싣는다. 단계가 없는 이유(되묻기 · 사슬 끝 · 사슬 없음)는
    `state.steps` 에 남겨 출력 tool 이 코드 문구로 닫는다 — 모델에게 고르게 하지 않는다.
    """
    _require_gate(context)
    start, today = _window(context)
    rows = await context.ports.memory.routine(
        child_id=context.child_id, date_from=start, date_to=today
    )
    if args.category is not None:
        rows = [row for row in rows if row.routine_category == args.category]
    if not rows:
        return fail("query", "search_routine_memory", ErrorCode.NO_RECORDS, _NO_ROUTINE)

    ranked = rank_evidence(
        (),
        tuple(
            ObservationRow(
                id=row.id,
                kind="observation_routine",
                subject=row.subject,
                polarity=row.polarity,
                observed_on=row.observed_on,
            )
            for row in rows
        ),
        today=today,
    )
    picked = top_refs(ranked, limit=MEMORY_LIMIT)
    context.state.seen_evidence.update(picked)
    by_id = {row.id: row for row in rows}
    shown = [by_id[key] for key in picked]
    for row in shown:
        context.state.seen_routine[row.id] = row

    await _pick_steps(context, shown)
    return ok(
        "query",
        "search_routine_memory",
        records=[
            {
                "id": str(row.id),
                "subject": row.subject,
                "category": row.routine_category,
                "assistance_level": row.assistance_level,
                "completion_status": row.completion_status,
                "trigger": row.trigger,
                "observed_on": row.observed_on.isoformat(),
            }
            for row in shown
        ],
        next_steps=[
            {
                "step_id": str(picked_step.step.row.id),
                "subject": picked_step.subject,
                "category": picked_step.category,
                "title": picked_step.step.row.title,
                "body": picked_step.step.row.body,
            }
            for picked_step in context.state.steps.values()
            if picked_step.step.row is not None
        ],
    )


async def _pick_steps(context: GrowthContext, rows: Sequence[RoutineObservation]) -> None:
    """관찰마다가 아니라 **사슬마다** 한 번 고른다.

    같은 행동을 "양치" · "양치하기" 로 달리 적어도 한 사슬이다 — 그 사슬에 맞는 관찰을 다 모아 가장
    최근 도움 수준으로 단계를 고른다. 한 철자의 오래된 기록으로 이미 넘어선 단계를 시키지 않게 한다.
    맞는 사슬이 없는 subject 는 카테고리와 이름으로 따로 남긴다 (`doc.no_row`).
    예절 · 습관은 사슬이 없어서 `_pick_strategies` 가 따로 고른다.
    """
    gate = context.state.gate
    assert gate is not None
    months, today = gate.stage.months, context.today
    steps = context.state.steps
    chains_by_category: dict[str, dict[str, tuple[GrowthDocRow, ...]]] = {}
    for row in rows:
        category = row.routine_category
        if category not in SELF_RELIANCE:
            continue
        if category not in chains_by_category:
            chains_by_category[category] = await routine_chains(
                context.ports.docs, months=months, category=category
            )
        chain = match_chain(chains_by_category[category], row.subject)
        if chain is None:
            unmatched = f"{category}|{normalize(row.subject)}"
            if unmatched not in steps:
                steps[unmatched] = PickedStep(
                    subject=row.subject, category=category, step=NextStep(readout_key=DOC_NO_ROW)
                )
            continue
        key = chain_key(chain[0].doc_key)
        if key in steps:
            continue
        same = [
            obs
            for obs in rows
            if obs.routine_category == category and match_chain({key: chain}, obs.subject)
        ]
        level = current_assistance_level(same, category=category, subject=None, today=today)
        latest = max(same, key=lambda obs: obs.observed_on)
        try:
            step = pick_next_step(chain, assistance_level=level)
        except ChainError:
            # 도움 수준 태그가 이상한 사슬이다. 단계를 추측하지 않고 그 행동만 닫는다
            logger.warning("Growth 자립 단계 사슬의 태그가 이상해 뺀다 chain=%s", key)
            step = NextStep(readout_key=DOC_NO_ROW)
        steps[key] = PickedStep(subject=latest.subject, category=category, step=step)

    for category in sorted(STRATEGY_CATEGORIES):
        same = [obs for obs in rows if obs.routine_category == category]
        if same:
            await _pick_strategies(context, category, same)


async def _pick_strategies(
    context: GrowthContext, category: str, rows: Sequence[RoutineObservation]
) -> None:
    """예절 · 습관 관찰에 맞는 전략 행. 고른 행마다 한 칸, 카테고리마다 최대 3칸이다.

    행동 이름(subject)마다 고른다. 예절은 이름이 맞는 행, 습관은 그 행동 자신의 관찰에서 읽은
    trigger 의 상황 태그가 겹치는 행이다 — 다른 행동의 trigger 를 빌리지 않는다. 증상으로 닫히는
    subject(눈 깜빡임 …)와 맞는 행이 없는 subject 는 `doc.no_row` 한 칸으로 남긴다.
    이미 고른 칸도 3칸에 센다 — 다시 조회해도 3칸을 넘지 않는다.
    """
    gate = context.state.gate
    assert gate is not None
    steps = context.state.steps
    recent = sorted(rows, key=lambda obs: obs.observed_on, reverse=True)
    taken = sum(1 for p in steps.values() if p.category == category and p.step.row is not None)
    for subject in dict.fromkeys(obs.subject for obs in recent):
        found: tuple[GrowthDocRow, ...] = ()
        if closure_key((subject,)) is None:
            found = await strategy_rows(
                context.ports.docs,
                months=gate.stage.months,
                category=category,
                subject=subject,
                trigger_tags=trigger_tags([o.trigger for o in recent if o.subject == subject])
                if category == "habit"
                else (),
                rotation=f"{context.child_id}|{context.today.isoformat()}",
            )
        if not found:
            steps.setdefault(
                f"{category}|{normalize(subject)}",
                PickedStep(
                    subject=subject, category=category, step=NextStep(readout_key=DOC_NO_ROW)
                ),
            )
        for row in found:
            key = f"{category}|{row.doc_key}"
            if taken >= MAX_SUGGESTIONS or key in steps:
                continue
            steps[key] = PickedStep(subject=subject, category=category, step=NextStep(row=row))
            taken += 1
