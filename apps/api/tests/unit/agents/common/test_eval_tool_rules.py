"""라이브 eval 의 tool 금지 규칙 정의 검사 (#816) — LLM 은 부르지 않는다.

금지한 tool 이 오타이거나, 그 케이스 world 의 gate 에서 안 열리면 그 금지는 늘 통과한다.
쓰기 tool 금지(`c1`) · CI 묶음(`ci`) 표시가 정한 자리에만 있는지도 본다.
"""

import pytest

from tests.eval.agents.tally import is_write_tool
from tests.eval.agents.tool_rules import (
    ToolRule,
    check,
    ci_cases,
    forbidden_failures,
    opened_tools,
    rules,
)


@pytest.mark.parametrize("rule", rules(), ids=lambda r: f"{r.suite}-{r.case_id}")
async def test_금지_tool_은_그_케이스_gate_에서_열려_있다(rule):
    assert check(rule, await opened_tools(rule.suite, rule.case_id)) == []


def test_Food_Health_c1_은_쓰기_tool_금지가_있는_케이스에만():
    # Memory 의 c1 은 저장 결과로 판정하는 케이스(보호자 말이 아이 fact 가 됨)도 있어 따로 둔다
    assert all(
        any(is_write_tool(t) for t in r.forbidden)
        for r in rules()
        if r.c1 and r.suite in ("food", "health")
    )


def test_ci_케이스는_Agent_마다_3건():
    by_suite: dict[str, set[str]] = {}
    for rule in rules():
        if rule.ci:
            by_suite.setdefault(rule.suite, set()).add(rule.case_id)

    assert {s: len(ids) for s, ids in by_suite.items() if s != "activity"} == {
        "memory": 3,
        "food": 3,
        "growth": 3,
        "health": 3,
    }
    assert set(ci_cases()["activity"]) == {
        "test_기록이_없으면_일반_추천만_낸다",
        "test_강한_비가_오면_바깥_활동을_내지_않는다",
        "test_요청한_놀이를_따라간다",
    }


def test_check_는_없는_tool_을_잡는다():
    rule = ToolRule("memory", "T99", frozenset(), frozenset({"create_remindr"}), False, True)

    assert check(rule, frozenset({"create_event"}))


def test_check_는_닫힌_tool_을_잡는다():
    rule = ToolRule("food", "T99", frozenset(), frozenset({"delete_daycare_meal"}), True, True)

    assert check(rule, frozenset({"show_daycare_meal"}))


def test_없애_둔_tool_이름은_허용_목록에_있을_때만_통과한다():
    # 지운 tool 을 모델이 다시 부르는 회귀를 잡으려고 일부러 금지에 남긴 이름
    rule = ToolRule("memory", "T06", frozenset(), frozenset({"create_reminder"}), False, True)

    assert check(rule, frozenset({"create_event"})) == []


def test_Food_금지_tool_을_부르면_실패_문장을_낸다():
    from tests.eval.agents.food.test_food_run import CASES

    case = next(c for c in CASES if c.case_id == "T41/C48")

    assert forbidden_failures(case, ["lookup_daycare_menu", "delete_daycare_meal"])
    assert forbidden_failures(case, ["show_daycare_meal"]) == []
