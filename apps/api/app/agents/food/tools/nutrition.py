"""영양 tool 3개(영양성분 조회 / 식단 균형 비교 . 영양소 분석 보고)

정확한 섭취량을 계산하지 않고 식단에 담긴 영양소의 비중만 고려.
"""

from app.agents.food.context import FoodContext
from app.agents.food.menu_resolver import MenuResolver, Resolved
from app.agents.food.nutrient_actions import advice_for
from app.agents.food.nutrition_eval import evaluate_nutrient_bands
from app.agents.food.result import ToolResult, ok
from app.agents.food.schemas.nutrition import (
    CompareDietBalanceArgs,
    LookupNutritionArgs,
    NutrientReportArgs,
)
from app.rules.age import life_stage, months_between
from app.rules.menu_key import normalize_menu_key


async def lookup_nutrition(context: FoodContext, args: LookupNutritionArgs) -> ToolResult:
    """음식이나 식재료의 영양성분을 조회한다.

    메뉴 카탈로그(`MenuCatalogStore.get_many`)에서 먼저 찾고, 없는 이름만 `MenuResolver` 로
    외부에서 찾는다. 이름이 정확히 같은 결과만 받고, 못 찾으면 남기지 않는다
    (food_agent_own_table §1). 조회되지 않은 음식은 `unresolved` 로 따로 돌려주고, 비슷한
    음식의 값으로 대체하지 않는다. 영양성분 수치는 조회된 원본 값을 그대로 낸다 — 모델이 값을
    추정하거나 보정하지 않는다.
    """
    resolver = MenuResolver(
        catalog=context.ports.catalog, source=context.ports.menu_source, now=context.now
    )
    resolutions = await resolver.resolve_many(args.foods)

    found: list[dict[str, object]] = []
    unresolved: list[str] = []
    for name in args.foods:
        resolution = resolutions.get(normalize_menu_key(name))
        if isinstance(resolution, Resolved) and resolution.row.nutrients:
            row = resolution.row
            found.append(
                {
                    "menu_key": row.menu_key,
                    "display_name": row.display_name,
                    "per_100g": dict(row.nutrients),
                    "food_groups": sorted(row.food_groups),
                }
            )
        else:
            unresolved.append(name)

    return ok("query", "nutrition", found=found, unresolved=unresolved)


async def compare_diet_balance(context: FoodContext, args: CompareDietBalanceArgs) -> ToolResult:
    """기간 내 식단의 영양소 비중을 연령별 기준과 비교한다. 계산은 전부 코드가 한다.

    `evaluate_nutrient_bands`(판정 창 고정 7일)에 그대로 맡긴다 — `args.period` 는 조회
    tool(`analyze_meal_records` 등)의 기간 라벨과 달리 이 판정의 창 길이를 바꾸지 않는다.
    기록·해석이 모자라면(`insufficient`) 구간 없이 건수만 돌려준다. 구간이 나온 항목만
    `nutrient_actions.advice_for` 로 식품군 안내를 받고, 아이의 알레르기 때문에 안내 식품이
    다 빠지면 구간만 말한다 — 다른 급원 식품을 지어내지 않는다(리뷰 초점 5).
    """
    ports = context.ports
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    months = months_between(birth_date, context.today)
    stage = life_stage(birth_date, context.today).stage

    verdict = await evaluate_nutrient_bands(context, months=months)

    if verdict.insufficient:
        context.state.basis_keys = set()
        return ok(
            "query",
            "diet_balance",
            bands={},
            unscored=list(verdict.unscored),
            food_group_actions=[],
            records=verdict.records,
            insufficient=True,
        )

    entries = context.state.safety or ()
    food_group_actions: list[str] = []
    for nutrient, band in verdict.bands.items():
        advice = advice_for(nutrient, band, entries, stage=stage, months=months)
        if advice is not None and advice.text:
            food_group_actions.append(advice.text)

    context.state.basis_keys = set(verdict.bands)
    return ok(
        "query",
        "diet_balance",
        bands=dict(verdict.bands),
        unscored=list(verdict.unscored),
        food_group_actions=food_group_actions,
        records=verdict.records,
        insufficient=False,
    )


async def report_nutrient_analysis(context: FoodContext, args: NutrientReportArgs) -> ToolResult:
    """영양소 분석 결과를 최종 검증하고 반환한다.

    DB 연결 후:
    - findings에 음식 추천, 근거 없는 정확한 수치, 진단 표현이 포함됐는지 확인한다.
      허용하지 않는 내용이 있으면 INVALID_ARGS를 반환한다.
    - evidence가 이번 실행에서 실제로 조회한 ref인지 확인하고,
      확인되지 않은 ref는 결과에서 제외한다.
    - 분석 결과는 저장하지 않고 반환만 한다. 영양 판정은 그 task의 run state에만 둔다.
    - 결핍 진단, 치료식, 영양제 관련 요청은 이 단계에서 처리하지 않는다.
    """
    raise NotImplementedError("DB 연결 후 구현")
