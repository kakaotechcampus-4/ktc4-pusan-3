"""Food tool 이름 -> 함수 매핑, 인자 검증, task × 식이 단계 묶음.

어떤 tool 을 열지는 모델이 아니라 여기 코드가 정한다.

  식단 추천 × 유아기   search · analyze · repeated · daycare_menu · propose (5)
  식단 추천 × 영아기   search · weaning · propose (3)
  영양소 분석 × 유아기 search · analyze · repeated · daycare_menu · nutrition · balance · report (7)
  영양소 분석 × 영아기 없음

- 식품을 제안하는 tool(propose_meal_candidates)은 식단 추천에만 있다.
- filter_food_safety는 CODE_TOOLS로, TOOL_SPECS, TOOL_HANDLERS에 없어서 모델이 부를 수 없음
"""

from collections.abc import Awaitable, Callable, Collection
from typing import Any

from pydantic import BaseModel, ValidationError

from app.agents.common.tool_schema import ToolDefinition, build_tool_specs
from app.agents.food.context import FoodContext
from app.agents.food.result import ErrorCode, ToolResult, fail
from app.agents.food.schemas.common import FeedingStage, FoodTaskType
from app.agents.food.schemas.tool_defs import TOOL_DEFINITIONS
from app.agents.food.tools.infant import guide_weaning_stage
from app.agents.food.tools.menu import lookup_daycare_menu
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
    "lookup_nutrition": lookup_nutrition,
    "compare_diet_balance": compare_diet_balance,
    "guide_weaning_stage": guide_weaning_stage,
    "propose_meal_candidates": propose_meal_candidates,
    "report_nutrient_analysis": report_nutrient_analysis,
}

# 모델에게 보이지 않는 코드 tool. 코드가 정해진 지점에서 직접 부른다 (S6)
CODE_TOOLS: dict[str, Callable[..., Any]] = {"filter_food_safety": filter_food_safety}

_DEFINITIONS: dict[str, ToolDefinition] = {d.name: d for d in TOOL_DEFINITIONS}
TOOL_SPECS: list[dict[str, Any]] = build_tool_specs(
    [d for d in TOOL_DEFINITIONS if d.name in TOOL_HANDLERS]
)
_SPECS_BY_NAME: dict[str, dict[str, Any]] = {spec["function"]["name"]: spec for spec in TOOL_SPECS}

# ── 묶음 — food.md "연령별 Tool 변화" ────────────────────────────
COMMON: tuple[str, ...] = ("search_food_memory", "propose_meal_candidates")
STAGE_TOOLS: dict[FeedingStage, tuple[str, ...]] = {
    FeedingStage.INFANT: ("guide_weaning_stage",),
    FeedingStage.TODDLER: (
        "analyze_meal_records",
        "check_repeated_menus",
        "lookup_daycare_menu",
        "lookup_nutrition",
        "compare_diet_balance",
        "report_nutrient_analysis",
    ),
}
TASK_TOOLS: dict[FoodTaskType, frozenset[str]] = {
    FoodTaskType.MEAL_RECOMMENDATION: frozenset(
        {
            "search_food_memory",
            "analyze_meal_records",
            "check_repeated_menus",
            "lookup_daycare_menu",
            "guide_weaning_stage",
            "propose_meal_candidates",
        }
    ),
    FoodTaskType.NUTRIENT_ANALYSIS: frozenset(
        {
            "search_food_memory",
            "analyze_meal_records",
            "check_repeated_menus",
            "lookup_daycare_menu",
            "lookup_nutrition",
            "compare_diet_balance",
            "report_nutrient_analysis",
        }
    ),
}
# task 마다 마지막에 한 번 부르는 출력 tool. 이게 열리지 않으면 그 조합은 지원하지 않는다
OUTPUT_TOOL: dict[FoodTaskType, str] = {
    FoodTaskType.MEAL_RECOMMENDATION: "propose_meal_candidates",
    FoodTaskType.NUTRIENT_ANALYSIS: "report_nutrient_analysis",
}


def _bundle(task: FoodTaskType, stage: FeedingStage) -> tuple[str, ...]:
    """(COMMON ∪ STAGE_TOOLS[stage]) ∩ TASK_TOOLS[task]. 순서는 TOOL_DEFINITIONS 를 따른다."""
    opened = (set(COMMON) | set(STAGE_TOOLS[stage])) & TASK_TOOLS[task]
    return tuple(d.name for d in TOOL_DEFINITIONS if d.name in opened)


def supports(task: FoodTaskType, stage: FeedingStage) -> bool:
    return OUTPUT_TOOL[task] in _bundle(task, stage)


def tools_for(task: FoodTaskType, stage: FeedingStage) -> tuple[str, ...]:
    """이번 task 에 모델에게 열 tool. 지원하지 않는 조합이면 빈 튜플 — 모델을 부르지 않는다."""
    return _bundle(task, stage) if supports(task, stage) else ()


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
            ErrorCode.UNKNOWN_TOOL,
            f"'{name}' 은 이번 요청에서 쓸 수 없는 tool 이다. 제공된 tool 중에서 고른다.",
        )

    try:
        args = definition.args.model_validate(arguments)
    except ValidationError as exc:
        return fail(
            None,
            name,
            ErrorCode.VALIDATION_ERROR,
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
