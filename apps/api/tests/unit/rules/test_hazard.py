"""app/rules/hazard.py — 위험 물질 사전검사 (#626).

순수 함수만 검증한다. LLM·DB 없음. 케이스 표는 `hazard_cases.py` 에 있다 —
`StaticRuleGuardrail` 테스트가 같은 표를 돈다 (#731).
"""

import pytest

from app.rules.hazard import FOOD_NAME_TERMS, PLAY_NAME_TERMS, find_hazards, hazard_names
from app.rules.hazard_rules import DEFAULT_RULES, HazardRules
from app.rules.term_match import Term, match_spans, normalize
from tests.unit.rules.hazard_cases import (
    FOOD_NAME_CASES,
    INPUT_HITS,
    INPUT_MISSES,
    PASSING_EVAL_CASES,
    PLAY_NAME_CASES,
)


@pytest.mark.parametrize(("text", "expected"), INPUT_HITS)
def test_위험_물질을_먹거나_마셨다는_말을_건다(text, expected) -> None:
    assert find_hazards(text) == expected


@pytest.mark.parametrize("text", INPUT_MISSES)
def test_위험_물질이_아닌_말은_걸지_않는다(text) -> None:
    assert find_hazards(text) == ()


@pytest.mark.parametrize(
    "case", PASSING_EVAL_CASES, ids=[case.case_id for case in PASSING_EVAL_CASES]
)
def test_위험_물질_구간이_없는_eval_케이스는_규칙에_걸리지_않는다(case) -> None:
    assert find_hazards(case.text) == ()


@pytest.mark.parametrize(("names", "expected"), FOOD_NAME_CASES)
def test_음식_이름_칸의_위험_물질(names, expected) -> None:
    assert hazard_names(names, FOOD_NAME_TERMS) == expected


@pytest.mark.parametrize(("names", "expected"), PLAY_NAME_CASES)
def test_놀이_배움_이름_칸은_마약류만_본다(names, expected) -> None:
    assert hazard_names(names, PLAY_NAME_TERMS) == expected


def test_match_spans_는_guard_가_덮지_않은_자리만_돌려준다() -> None:
    wheat = Term(key="6", aliases=("밀",), guards=("밀크",))
    assert match_spans(normalize("밀크티와 통밀빵"), (wheat,)) == (("6", 5, 6),)


# 지어낸 이름으로 만든 대체 규칙. with_intake 순서를 일부러 HAZARD_KINDS 와 반대로 뒀다
_CUSTOM = HazardRules(
    named=(Term("drug", ("가상마약",)),),
    with_intake=(
        Term("chemical", ("가상세제",)),
        Term("alcohol", ("가상술",)),
        Term("tobacco", ("가상담배",)),
    ),
    with_swallow=(Term("object", ("가상구슬",)),),
    intake=DEFAULT_RULES.intake,
    swallow=DEFAULT_RULES.swallow,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("가상세제를 한 모금 마셨어", ("chemical",)),
        ("가상마약 냄새가 났어", ("drug",)),
        ("가상구슬을 삼켰어", ("object",)),
        ("가상세제 마시고 가상술도 마셨어", ("alcohol", "chemical")),  # 순서는 HAZARD_KINDS
        ("소주를 마셨어", ()),  # 넘긴 규칙에 없는 이름은 기본 규칙에 있어도 안 건다
    ],
)
def test_넘긴_규칙으로_건다(text, expected) -> None:
    assert find_hazards(text, rules=_CUSTOM) == expected


@pytest.mark.parametrize(
    "text",
    [
        "가상세제는 안 마셨어",
        "가상세제는 먹지 마",
        "아빠가 가상술을 마셨어",
        "가상술 마시는 아빠 옆에서 주스 마셨어",
        "가상세제로 닦고 밥 먹었어",
    ],
)
def test_넘긴_규칙에도_판정_방법은_그대로다(text) -> None:
    assert find_hazards(text, rules=_CUSTOM) == ()
