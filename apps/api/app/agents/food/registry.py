"""Food tool 이름 -> 함수 매핑, 인자 검증, task × Gate로 여닫는 tool 묶음.

어떤 tool을 열지는 모델이 아니라 여기 코드가 정한다.

  라벨: meal_recommendation
    infant_milk(0–3)       닫힘
    infant_weaning(4–11)   search·weaning·propose
    toddler·preschool(12+) search·analyze·repeated·(daycare)·propose
  라벨: nutrient_analysis
    infant_milk·infant_weaning(0–11)  닫힘
    toddler·preschool(12+)            search·analyze·repeated·(daycare)·
                                       nutrition·balance·report
  라벨: daycare_meal — 단계로 닫지 않음
    급식 행 있음  lookup·update·delete
    급식 행 없음  닫힘


- `meal_recommendation`은 `safety_ok=False`면 단계와 무관하게 닫힌다(`blocked.safety`) —
  알레르기 필터를 걸 수 없어서다. `nutrient_analysis`·`daycare_meal`은 영향받지 않는다.
- `consent_child_health`는 어느 라벨도 닫지 않는다. 동의가 없으면 `build_gate`가
  `allergy_states`를 빈 튜플로 채울 뿐이다.
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
    {"lookup_daycare_menu", "update_daycare_meal", "delete_daycare_meal"}
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
_TODDLER_NUTRIENT_BASE: frozenset[str] = frozenset(
    {
        "search_food_memory",
        "analyze_meal_records",
        "check_repeated_menus",
        "lookup_nutrition",
        "compare_diet_balance",
        "report_nutrient_analysis",
    }
)

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
        return None
    if task == FoodTaskType.NUTRIENT_ANALYSIS:
        return "unsupported.infant_nutrient" if stage in _INFANT_STAGES else None
    if task == FoodTaskType.DAYCARE_MEAL:
        return None if gate.data.daycare_meal else "closed.no_daycare"
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
        opened = set(_TODDLER_NUTRIENT_BASE)
        if gate.data.daycare_meal:
            opened.add("lookup_daycare_menu")

    return tuple(d.name for d in TOOL_DEFINITIONS if d.name in opened)


def requires_safety_check(task: FoodTaskType) -> bool:
    """모델 호출 전에 health_safety 를 읽어야 하는가. 식단 추천만 (S6 ①)."""
    return task == FoodTaskType.MEAL_RECOMMENDATION


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
    지금 핸들러는 NotImplementedError 를 낸다 — 삼키지 않고 그대로 올린다.
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
