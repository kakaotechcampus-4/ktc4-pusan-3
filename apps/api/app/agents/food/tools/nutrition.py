"""영양 tool 3개(영양성분 조회 / 식단 균형 비교 . 영양소 분석 보고)

정확한 섭취량을 계산하지 않고 식단에 담긴 영양소의 비중만 고려.
"""

import re
from uuid import UUID

from app.agents.food.context import FoodContext
from app.agents.food.menu_resolver import MenuResolver, Resolved
from app.agents.food.nutrient_actions import advice_for, check_summary
from app.agents.food.nutrition_eval import evaluate_nutrient_bands
from app.agents.food.result import ErrorCode, ToolResult, fail, ok
from app.agents.food.schemas.common import EvidenceRef
from app.agents.food.schemas.nutrition import (
    CompareDietBalanceArgs,
    LookupNutritionArgs,
    NutrientReportArgs,
)
from app.rules.age import life_stage, months_between
from app.rules.menu_key import normalize_menu_key

# 숫자 + 단위(% · g · mg · kcal) 또는 그 한글 표현. report_nutrient_analysis 의 findings 는
# 구간(낮은 편 · 적당 · 높은 편)으로만 말하고 수치를 쓰지 않는다 (Food_Tool_명세 §2).
_NUMERIC_UNIT = re.compile(r"\d+(\.\d+)?\s*(%|g|mg|kcal)|퍼센트|칼로리")

# 진단 · 처방 · 먹일 음식 제안 말. findings 는 식단 기반 비중·경향만 말한다
_PROHIBITED_WORDS = (
    "의심",
    "결핍",
    "영양제",
    "보충제",
    "처방",
    "복용",
    "늘려",
    "드세요",
    "먹이세요",
    "챙겨",
)

# 자유 텍스트 finding 에서 영양소 이름을 찾는다. basis_keys(이번에 실제로 조회한 항목) 밖을
# 말하면 모델이 항목을 지어낸 것이다 — "아연" 처럼 이 시스템이 다루지 않는 이름도 인식해야
# basis_keys 밖으로 걸러낼 수 있다
_NUTRIENT_NAMES: dict[str, str] = {
    "단백질": "protein",
    "칼슘": "calcium",
    "철분": "iron",
    "철": "iron",
    "탄수화물": "carbohydrate",
    "지방": "fat",
    "나트륨": "sodium",
    "당류": "sugars",
    "당분": "sugars",
    "포화지방": "saturated_fat",
    "아연": "zinc",
    "비타민": "vitamin",
    "마그네슘": "magnesium",
    "엽산": "folate",
}


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
    removed_foods: list[str] = []
    for nutrient, band in verdict.bands.items():
        advice = advice_for(nutrient, band, entries, stage=stage, months=months)
        if advice is None:
            continue
        if advice.text:
            food_group_actions.append(advice.text)
        removed_foods.extend(advice.removed)

    context.state.basis_keys = set(verdict.bands)
    # report_nutrient_analysis 의 check_summary 가 요약 문장에 이 이름이 남았는지 본다(N-14)
    context.state.removed_foods = tuple(removed_foods)
    return ok(
        "query",
        "diet_balance",
        bands=dict(verdict.bands),
        unscored=list(verdict.unscored),
        food_group_actions=food_group_actions,
        records=verdict.records,
        insufficient=False,
    )


def _seen(ref: EvidenceRef, seen_refs: set[tuple[str, UUID]]) -> bool:
    """ref.id 가 UUID 형식이 아니면 이번 run 에서 조회하지 않은 근거와 같이 뺀다 — 거절이 아니라
    제외다(§4 ⑤). 모델이 지어낸 id 하나가 findings 전체를 죽이면 안 된다.
    """
    try:
        ref_id = UUID(ref.id)
    except ValueError:
        return False
    return (ref.kind, ref_id) in seen_refs


async def report_nutrient_analysis(context: FoodContext, args: NutrientReportArgs) -> ToolResult:
    """영양소 분석 결과를 최종 검증하고 반환한다.

    검사 순서 — ⓪ `insufficient` 와 findings 유무가 맞는지 ① 단위·수치 정규식 ② 진단·처방·먹일
    음식 제안 말 ③ `check_summary`(별칭 + 안전 사전) ④ `basis_keys` 밖 항목 ⑤ `seen_refs` 밖
    근거 제거(거절이 아니라 제외). ⓪~④ 중 하나라도 걸리면 findings 전체를 거절한다 — 분석
    결과는 저장하지 않고 반환만 한다.
    """
    ports = context.ports
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    months = months_between(birth_date, context.today)
    stage = life_stage(birth_date, context.today).stage
    entries = context.state.safety or ()

    verdict = context.state.nutrient_verdict
    if verdict is not None and verdict.insufficient and args.findings:
        return fail(
            "report",
            "nutrient_analysis",
            ErrorCode.INVALID_ARGS,
            "기록이 부족하면 findings 를 비운다. 구간을 말하지 않는다.",
        )
    if verdict is not None and not verdict.insufficient and not args.findings:
        return fail(
            "report",
            "nutrient_analysis",
            ErrorCode.INVALID_ARGS,
            "findings 가 비어 있다. compare_diet_balance 가 낸 구간을 적어도 하나는 말한다.",
        )

    for finding in args.findings:
        if _NUMERIC_UNIT.search(finding):
            return fail(
                "report",
                "nutrient_analysis",
                ErrorCode.INVALID_ARGS,
                "findings 에 수치·단위 표현을 쓰지 않는다. 구간(낮은 편 · 적당 · 높은 편)으로만 "
                "말한다.",
            )
        if any(word in finding for word in _PROHIBITED_WORDS):
            return fail(
                "report",
                "nutrient_analysis",
                ErrorCode.INVALID_ARGS,
                "findings 에 진단 · 처방 · 먹일 음식 제안을 쓰지 않는다. 식단 기반 비중만 말한다.",
            )
        check = check_summary(
            finding, context.state.removed_foods, entries, stage=stage, months=months
        )
        if not check.ok:
            return fail(
                "report",
                "nutrient_analysis",
                ErrorCode.INVALID_ARGS,
                "findings 에 이 아이에게 안전하지 않거나 뺀 식품 이름이 남아 있다.",
            )
        mentioned = {key for word, key in _NUTRIENT_NAMES.items() if word in finding}
        if mentioned - context.state.basis_keys:
            return fail(
                "report",
                "nutrient_analysis",
                ErrorCode.INVALID_ARGS,
                "findings 에 이번에 조회하지 않은 영양 항목이 있다.",
            )

    evidence = [ref.model_dump() for ref in args.evidence if _seen(ref, context.state.seen_refs)]

    return ok(
        "report",
        "nutrient_analysis",
        findings=list(args.findings),
        data_gaps=list(args.data_gaps),
        evidence=evidence,
    )
