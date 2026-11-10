"""라이브 eval 6곳이 회차를 같은 이름으로 센다 (#816) — LLM 은 부르지 않는다."""

import importlib

import pytest

from app.agents import prompt_registry

SUITES = {
    "tests.eval.agents.food.test_food_run": "food",
    "tests.eval.agents.growth.test_growth_live": "growth",
    "tests.eval.agents.growth.test_book_live": "growth",
    "tests.eval.agents.health.test_health_run": "health",
    "tests.eval.agents.supervisor.test": "supervisor",
    "tests.eval.agents.memory.test_memory": "memory",
    "tests.eval.agents.activity.test_activity_live": "activity",
}


@pytest.mark.parametrize(("module", "suite"), SUITES.items())
def test_suite_이름은_평가_문서_이름이다(module, suite):
    loaded = importlib.import_module(module)

    assert loaded.TALLY_SUITE == suite
    assert suite in prompt_registry.PROMPT_NAMES


def test_Food_C1_은_c1_에도_들어가고_실패는_그대로_센다():
    from tests.eval.agents.food.test_food_run import Verdict

    verdict = Verdict()
    verdict.fail("공통3", c1=True)
    verdict.fail("공통1")

    assert verdict.c1 == ["공통3"] and verdict.failures == ["공통3", "공통1"]


def test_Growth_C1_은_c1_에도_들어가고_실패는_그대로_센다():
    from tests.eval.agents.growth.test_growth_live import Verdict

    verdict = Verdict()
    verdict.fail("공통1", c1=True)
    verdict.fail("공통6")

    assert verdict.c1 == ["공통1"] and verdict.failures == ["공통1", "공통6"]


def test_Health_C1_은_표식으로_구분하고_합쳐도_남는다():
    from tests.eval.agents.health.judges import C1_TAG, Verdict, c1_msg

    first = Verdict()
    first.fail("공통 — 진단명", c1=True)
    second = Verdict(failures=["일반", c1_msg("약 이름")])

    merged = first.extend(second)

    assert merged.c1 == [C1_TAG + "공통 — 진단명", C1_TAG + "약 이름"]
    assert len(merged.failures) == 3


def test_Memory_c1_케이스는_실행_문제를_뺀_실패만_C1_이다():
    from tests.eval.agents.memory.test_memory import CASES, split_c1

    case = next(c for c in CASES if c.case_id == "T19")

    c1, general = split_c1(
        case, ["금지된 호출: ['create_observation_health']", "재시도 예산(1회) 초과"]
    )

    assert c1 == ["금지된 호출: ['create_observation_health']"]
    assert general == ["재시도 예산(1회) 초과"]


def test_Memory_c1_케이스에서도_필수_tool_누락과_되묻기_문장은_일반_실패다():
    from tests.eval.agents.memory.test_memory import CASES, split_c1

    case = next(c for c in CASES if c.case_id == "T10")

    c1, general = split_c1(
        case,
        [
            "query_observation_food: 최소 1회 필요, 실제 0회",
            "되묻지 않고 끝냈다",
            "범위 밖이라고 안내하지 않음: '…'",
            "지우지 않았다",
            "금지된 호출: ['update_observation_food']",
        ],
    )

    assert c1 == ["지우지 않았다", "금지된 호출: ['update_observation_food']"]
    assert len(general) == 3


def test_Memory_입력_id_는_모델이_여럿이면_모델을_붙인다():
    from tests.eval.agents.memory.test_memory import input_id

    assert input_id("T19", None) == "T19"
    assert input_id("T19", "model-a") == "T19@model-a"
