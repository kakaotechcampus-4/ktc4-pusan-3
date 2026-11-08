"""app/agents/common/guardrail.py — 위험 물질 검사 자리 (#731).

`StaticRuleGuardrail` 이 규칙 함수와 같은 표(`tests/unit/rules/hazard_cases.py`)를 그대로
통과하는지 본다.
바꿔 끼운 Guardrail 이 사전검사 · 저장 게이트 두 곳에 같이 쓰이는지는
`tests/unit/agents/supervisor/test_pipeline.py` 의 위험 물질 절이 본다.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.agents.common.guardrail import GuardContext, GuardResult, StaticRuleGuardrail
from app.agents.memory.context import AgentContext
from app.agents.memory.store import InMemoryStore
from app.rules.hazard_rules import DEFAULT_RULES, HazardRules
from app.rules.term_match import Term
from tests.unit.rules.hazard_cases import (
    FOOD_NAME_CASES,
    INPUT_HITS,
    INPUT_MISSES,
    PASSING_EVAL_CASES,
    PLAY_NAME_CASES,
)

_GUARD = StaticRuleGuardrail()


async def _kinds(text: str, context: GuardContext) -> tuple[str, ...]:
    return (await _GUARD.check(text, context)).kinds


@pytest.mark.parametrize(("text", "expected"), INPUT_HITS)
async def test_한_줄_전체에서_위험_물질을_건다(text, expected) -> None:
    assert await _kinds(text, "input") == expected


@pytest.mark.parametrize("text", INPUT_MISSES)
async def test_한_줄_전체에서_위험_물질이_아닌_말은_걸지_않는다(text) -> None:
    assert await _kinds(text, "input") == ()


@pytest.mark.parametrize(
    "case", PASSING_EVAL_CASES, ids=[case.case_id for case in PASSING_EVAL_CASES]
)
async def test_위험_물질_구간이_없는_eval_케이스는_걸리지_않는다(case) -> None:
    assert await _kinds(case.text, "input") == ()


async def _name_kinds(names: list[str], context: GuardContext) -> tuple[str, ...]:
    # 저장 게이트처럼 칸마다 따로 본다
    kinds = [kind for name in names for kind in await _kinds(name, context)]
    return tuple(dict.fromkeys(kinds))


@pytest.mark.parametrize(("names", "expected"), FOOD_NAME_CASES)
async def test_음식_이름_칸(names, expected) -> None:
    assert await _name_kinds(names, "food_name") == expected


@pytest.mark.parametrize(("names", "expected"), PLAY_NAME_CASES)
async def test_놀이_배움_이름_칸은_마약류만_본다(names, expected) -> None:
    assert await _name_kinds(names, "play_name") == expected


async def test_넘긴_규칙으로_한_줄을_검사한다() -> None:
    rules = HazardRules(
        named=(Term("drug", ("가상마약",)),),
        with_intake=(
            Term("alcohol", ("가상술",)),
            Term("tobacco", ("가상담배",)),
            Term("chemical", ("가상세제",)),
        ),
        with_swallow=(Term("object", ("가상구슬",)),),
        intake=DEFAULT_RULES.intake,
        swallow=DEFAULT_RULES.swallow,
    )
    guard = StaticRuleGuardrail(rules)

    assert (await guard.check("가상세제를 한 모금 마셨어", "input")).kinds == ("chemical",)
    assert (await guard.check("소주를 마셨어", "input")).kinds == ()


def test_걸린_종류가_있을_때만_막는다() -> None:
    assert GuardResult(("alcohol",)).blocked is True
    assert GuardResult().blocked is False


def test_만드는_쪽이_안_넘기면_코드_규칙으로_검사한다() -> None:
    now = datetime(2026, 11, 7, tzinfo=UTC)
    context = AgentContext(
        child_id=UUID(int=1),
        source_writer=UUID(int=2),
        now=now,
        timezone=UTC,
        store=InMemoryStore(now=now),
    )

    assert isinstance(context.guardrail, StaticRuleGuardrail)
