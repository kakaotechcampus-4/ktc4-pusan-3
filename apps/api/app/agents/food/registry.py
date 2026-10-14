"""Food tool 이름 -> 함수 매핑, 인자 검증, task × Gate로 여닫는 tool 묶음.

어떤 tool을 열지는 모델이 아니라 여기 코드가 정한다.

  라벨: meal_recommendation
    infant_milk(0–3)       닫힘
    infant_weaning(4–11)   search·weaning·propose
    toddler·preschool(12+) search·analyze·repeated·(daycare)·propose
  라벨: nutrient_analysis
    infant_milk·infant_weaning(0–11)  닫힘
    toddler·preschool(12+)            열린 tool 없음 — 모델을 부르지 않고 코드가 출현 문장을 낸다
                                      (닫힘이 아니다. `closed_readout_key` 는 None 이다)
  라벨: daycare_meal — 단계로 닫지 않음
    급식 행 있음  lookup·update·delete·keep
    급식 행 없음  닫힘

- 🚨 열린 tool 중에 스텁(NotImplementedError)이 있으면 안 된다. tool 이 아직 스텁인 task 는
  `NOT_READY` 에 문구 키를 적어 닫고(`closed_readout_key`), `tools_for` 의 분기는 그대로 둔다 —
  구현되면 `NOT_READY` 에서 그 task 를 뺀다. `test_food_stub_gate.py` 가 막는다.
- 지금은 tool 12개가 다 구현돼 있고 `run()` 이 식단 추천 · 급식 갱신의 결과를 옮겨서
  `NOT_READY` 는 비어 있다.
- `meal_recommendation`·`nutrient_analysis`는 `safety_ok=False`면 닫힌다(`blocked.safety`) —
  알레르기 필터를 걸 수 없어서다. 단계 사유(`unsupported.*`)가 먼저다. 영양소 분석은 식품군
  안내를 내서 식단 추천과 같이 닫고, `daycare_meal`은 영향받지 않는다.
- `consent_child_health`는 어느 라벨도 닫지 않는다. 동의가 없으면 `build_gate`가
  `health_safety`를 읽지 않고 `context.state.safety` 를 빈 튜플로 둘 뿐이다.
- `(daycare)`는 `toddler`·`preschool` 에서만, 급식 행이 있을 때만 더해지는 tool이다.
  `infant_weaning`의 `meal_recommendation` 묶음은 급식 행과 무관하게 고정 3개다.
- `filter_food_safety`는 `CODE_TOOLS`.
- `daycare_meal` 은 쓰는 라벨이다(`WRITING_TASKS`). 같은 run 의 다른 Food task 보다 먼저 끝난다.
"""

from collections.abc import Awaitable, Callable, Collection
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agents.common.gate import Gate
from app.agents.common.tool_schema import ToolDefinition, build_tool_specs
from app.agents.food.context import FoodContext
from app.agents.food.result import ErrorCode, ToolResult, fail
from app.agents.food.schemas.common import FoodTaskType
from app.agents.food.schemas.tool_defs import TOOL_DEFINITIONS
from app.agents.food.tools.infant import guide_weaning_stage
from app.agents.food.tools.menu import (
    delete_daycare_meal,
    keep_daycare_meal,
    lookup_daycare_menu,
    update_daycare_meal,
)
from app.agents.food.tools.nutrition import (
    compare_diet_balance,
    lookup_nutrition,
    report_nutrient_analysis,
)
from app.agents.food.tools.recommend import propose_meal_candidates
from app.agents.food.tools.records import (
    analyze_meal_records,
    check_repeated_menus,
    search_food_memory,
)
from app.agents.food.tools.safety import filter_food_safety

ToolHandler = Callable[[FoodContext, Any], Awaitable[ToolResult]]

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "search_food_memory": search_food_memory,
    "analyze_meal_records": analyze_meal_records,
    "check_repeated_menus": check_repeated_menus,
    "lookup_daycare_menu": lookup_daycare_menu,
    "update_daycare_meal": update_daycare_meal,
    "delete_daycare_meal": delete_daycare_meal,
    "keep_daycare_meal": keep_daycare_meal,
    "lookup_nutrition": lookup_nutrition,
    "compare_diet_balance": compare_diet_balance,
    "guide_weaning_stage": guide_weaning_stage,
    "propose_meal_candidates": propose_meal_candidates,
    "report_nutrient_analysis": report_nutrient_analysis,
}

# 모델에게 보이지 않는 코드 tool. 코드가 정해진 지점에서 직접 부른다 (S6)
CODE_TOOLS: dict[str, Callable[..., Any]] = {"filter_food_safety": filter_food_safety}

# 같은 run의 다른 task가 읽는 행을 쓰는 라벨. pipeline이 이 task를 먼저 끝낸 뒤 나머지를 돌림
# ("급식 대신 두유 받았대. 저녁 뭐 먹일까?" 의 추천이 갱신된 급식을 읽어야 함)
WRITING_TASKS: frozenset[FoodTaskType] = frozenset({FoodTaskType.DAYCARE_MEAL})

_DEFINITIONS: dict[str, ToolDefinition] = {d.name: d for d in TOOL_DEFINITIONS}
TOOL_SPECS: list[dict[str, Any]] = build_tool_specs(
    [d for d in TOOL_DEFINITIONS if d.name in TOOL_HANDLERS]
)
_SPECS_BY_NAME: dict[str, dict[str, Any]] = {spec["function"]["name"]: spec for spec in TOOL_SPECS}

# 라벨별로 열리는 tool 이름
_DAYCARE_TOOLS: frozenset[str] = frozenset(
    {"lookup_daycare_menu", "update_daycare_meal", "delete_daycare_meal", "keep_daycare_meal"}
)
_WEANING_MEAL: frozenset[str] = frozenset(
    {"search_food_memory", "guide_weaning_stage", "propose_meal_candidates"}
)
_TODDLER_MEAL_BASE: frozenset[str] = frozenset(
    {
        "search_food_memory",
        "analyze_meal_records",
        "check_repeated_menus",
        "propose_meal_candidates",
    }
)
# 영양소 분석을 모델이 문장으로 다듬던 tool. 1차 출력은 모델을 부르지 않아 열지 않는다 —
# 구간을 다시 화면에 낼 때 `tools_for` 에서 이 집합을 연다
_NUTRIENT_MODEL_TOOLS: frozenset[str] = frozenset(
    {"lookup_nutrition", "compare_diet_balance", "report_nutrient_analysis"}
)

# 모델에게 열지 않고 코드 문구로 닫는 task → 문구 키. tool 이 아직 스텁인 task 를 여기에 적는다.
# 지금은 tool 12개가 다 구현되고 `run()` 이 결과를 옮겨서 비어 있다. 열린 tool 에 스텁이 남아 있으면
# `test_food_stub_gate.py` 가 막는다 (#357 리뷰 4-②)
NOT_READY: dict[FoodTaskType, str] = {}

# life_stage() 의 네 값 중 영아기 둘. 영양소 분석은 이 두 단계 전부에서 닫힌다
_INFANT_STAGES = ("infant_milk", "infant_weaning")


def closed_readout_key(task: FoodTaskType, gate: Gate) -> str | None:
    """이 (task, gate) 조합이 닫혀 있으면 코드 문구 키를, 열려 있으면 None 을 돌려준다.

    닫힌 경로는 모델을 0회 부른다 — `tools_for` 도 이 값이 있으면 빈 튜플을 낸다.
    문구는 `docs/agents/food/Food_Tool_명세.md` §4 에 있다.
    """
    stage = gate.stage.stage
    if task == FoodTaskType.MEAL_RECOMMENDATION:
        if stage == "infant_milk":
            return "unsupported.milk_meal"
        if not gate.safety_ok:
            return "blocked.safety"
        return NOT_READY.get(task)
    if task == FoodTaskType.NUTRIENT_ANALYSIS:
        if stage in _INFANT_STAGES:
            return "unsupported.infant_nutrient"
        # 영양 안내가 식품군("달걀 반찬 늘리기")을 말하므로, 걸러 낼 알레르기를 모르면 열지 않는다
        return None if gate.safety_ok else "blocked.safety"
    if task == FoodTaskType.DAYCARE_MEAL:
        if not gate.data.daycare_meal:
            return "closed.no_daycare"
        return NOT_READY.get(task)
    raise AssertionError(f"모르는 FoodTaskType: {task}")  # pragma: no cover


def tools_for(task: FoodTaskType, gate: Gate) -> tuple[str, ...]:
    """이번 task에 모델에게 열 tool. 닫혀 있으면 빈 튜플 — 모델을 부르지 않는다.

    순서는 `TOOL_DEFINITIONS` 순서를 따른다.
    """
    if closed_readout_key(task, gate) is not None:
        return ()

    if task == FoodTaskType.DAYCARE_MEAL:
        opened = set(_DAYCARE_TOOLS)
    elif task == FoodTaskType.MEAL_RECOMMENDATION:
        if gate.stage.stage == "infant_weaning":
            opened = set(_WEANING_MEAL)
        else:  # toddler · preschool — infant_milk 는 위에서 이미 닫혔다
            opened = set(_TODDLER_MEAL_BASE)
            if gate.data.daycare_meal:
                opened.add("lookup_daycare_menu")
    else:  # NUTRIENT_ANALYSIS — infant_* 는 위에서 이미 닫혔다
        # 1차 출력은 모델을 부르지 않는다 — 코드가 식품군 출현 문장을 낸다(N-17). 열린 tool 이 없을
        # 뿐 닫힘(`closed_readout_key`)과 다르다 — 이 task 는 `run()` 이 코드로 끝까지 간다
        opened: set[str] = set()

    return tuple(d.name for d in TOOL_DEFINITIONS if d.name in opened)


def answers_by_code(task: FoodTaskType) -> bool:
    """모델을 부르지 않고 코드가 결과를 내는 task. 영양소 분석 1차 출력이다(N-17)."""
    return task == FoodTaskType.NUTRIENT_ANALYSIS


# task 마다 결과를 내는 tool. run_tool_loop 이 이 안에서 끝낸다(output_accepted).
_OUTPUT_TOOLS: dict[FoodTaskType, frozenset[str]] = {
    FoodTaskType.MEAL_RECOMMENDATION: frozenset({"propose_meal_candidates"}),
    FoodTaskType.NUTRIENT_ANALYSIS: frozenset({"report_nutrient_analysis"}),
    # 급식 갱신은 tool 을 꼭 부른다(required). 쓰지 않고 끝내는 keep 이 없으면 묻는 말도 쓰기로 간다
    FoodTaskType.DAYCARE_MEAL: frozenset(
        {"update_daycare_meal", "delete_daycare_meal", "keep_daycare_meal"}
    ),
}


def output_tools_for(task: FoodTaskType) -> frozenset[str]:
    """이 task 의 출력 tool 이름. `run_tool_loop` 의 `output_tools` 로 그대로 넘긴다."""
    return _OUTPUT_TOOLS[task]


def specs_for(names: Collection[str]) -> list[dict[str, Any]]:
    return [_SPECS_BY_NAME[name] for name in _SPECS_BY_NAME if name in names]


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    context: FoodContext,
    *,
    allowed: Collection[str],
) -> ToolResult:
    """tool 을 한 번 실행한다. 허용 목록 → 이름 조회 → 인자 검증 → 실행 순서.

    모델에게 안 보여준 tool 을 이름으로 불러도 실행하지 않는다. allowed 는 tools_for() 결과다.
    핸들러 예외는 삼키지 않고 그대로 올린다(Tool_공통). 스텁 tool 은 모델에게 열지 않는다 —
    `tests/unit/agents/food/test_food_stub_gate.py` 가 막는다.
    """
    definition = _DEFINITIONS.get(name)
    handler = TOOL_HANDLERS.get(name)
    if name not in allowed or definition is None or handler is None:
        return fail(
            None,
            name,
            ErrorCode.TOOL_NOT_ALLOWED,
            f"'{name}' 은 이번 요청에서 쓸 수 없는 tool 이다. 제공된 tool 중에서 고른다.",
        )

    try:
        args = definition.args.model_validate(arguments)
    except ValidationError as exc:
        return fail(
            None,
            name,
            ErrorCode.INVALID_ARGS,
            f"인자가 스키마와 맞지 않는다: {_summarize(exc)}",
        )

    return await handler(context, args)


def _summarize(exc: ValidationError, limit: int = 3) -> str:
    """필드와 사유만 짧게. 값 원문(음식·알레르기 이름)은 싣지 않는다."""
    parts = [
        f"{'.'.join(str(item) for item in error['loc']) or '(root)'}: {error['msg']}"
        for error in exc.errors()[:limit]
    ]
    return " / ".join(parts)


def args_model(name: str) -> type[BaseModel] | None:
    return definition.args if (definition := _DEFINITIONS.get(name)) else None
