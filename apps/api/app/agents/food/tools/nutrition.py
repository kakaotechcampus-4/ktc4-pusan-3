"""영양 tool 3개(영양성분 조회 / 식단 균형 비교 . 영양소 분석 보고)

정확한 섭취량을 계산하지 않고 식단에 담긴 영양소의 비중만 고려.
"""

from dataclasses import dataclass
from uuid import UUID

from app.agents.common.age import life_stage
from app.agents.food.context import FoodContext
from app.agents.food.menu_resolver import MenuResolver, Resolved
from app.agents.food.nutrient_actions import advice_for, check_summary
from app.agents.food.nutrient_wording import WORDING_MESSAGES, wording_problem
from app.agents.food.nutrition_eval import NutrientVerdict, evaluate_nutrient_bands
from app.agents.food.result import ErrorCode, ToolResult, fail, ok
from app.agents.food.schemas.common import EvidenceRef
from app.agents.food.schemas.nutrition import (
    CompareDietBalanceArgs,
    LookupNutritionArgs,
    NutrientReportArgs,
)
from app.agents.food.store.ports import SafetyEntry
from app.rules.age import months_between
from app.rules.menu_key import normalize_menu_key

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
    음식의 값으로 대체하지 않는다.

    영양성분 수치는 돌려주지 않는다 — 판정은 compare_diet_balance 가 카탈로그에서 직접 계산하고,
    모델은 숫자를 쓸 데가 없다 (Food_Tool_명세 §2, #357 리뷰).
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
                    "food_groups": sorted(row.food_groups),
                }
            )
        else:
            unresolved.append(name)

    return ok("query", "nutrition", found=found, unresolved=unresolved)


def _safety_entries(context: FoodContext) -> tuple[SafetyEntry, ...]:
    """이번 run 이 읽은 health_safety. None 은 조회 실패라 빈 목록으로 바꾸지 않는다.

    `build_gate` 가 task 시작에 채운다 — 동의가 없으면 `()`(거를 것 없음), 조회에 실패하면 None 이고
    그때는 영양소 분석이 `blocked.safety` 로 닫혀 여기까지 오지 않는다. 오면 build_gate 를 거치지
    않은 호출이라는 뜻이다. 알레르기 식품을 거르지 못한 채 안내를 만들면 안 된다.
    """
    entries = context.state.safety
    if entries is None:
        raise RuntimeError("안전 정보를 읽지 못한 채로 영양소 분석 안내를 만들 수 없다")
    return entries


@dataclass(frozen=True)
class DietBalance:
    """판정과 식품군 안내. compare_diet_balance 와 run() 의 코드 문장이 같이 쓴다."""

    verdict: NutrientVerdict
    actions: dict[str, str]  # 영양소 → 안내 문장. 안내 식품이 다 빠진 항목은 없다
    removed_foods: tuple[str, ...]  # 알레르기 · 질환 때문에 안내에서 뺀 식품 이름


async def diet_balance(
    context: FoodContext, *, verdict: NutrientVerdict | None = None
) -> DietBalance:
    """판정(`evaluate_nutrient_bands`)과 구간별 식품군 안내(`advice_for`).

    `verdict` 를 넘기면 다시 판정하지 않는다 — run() 의 코드 문장이 이미 run state 에 있는 판정을
    쓸 때다(같은 run 에서 기록을 두 번 읽지 않는다).
    """
    ports = context.ports
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    months = months_between(birth_date, context.today)
    stage = life_stage(birth_date, context.today).stage
    if verdict is None:
        verdict = await evaluate_nutrient_bands(context, months=months)
    if verdict.insufficient:
        return DietBalance(verdict=verdict, actions={}, removed_foods=())

    entries = _safety_entries(context)
    actions: dict[str, str] = {}
    removed: list[str] = []
    for nutrient, band in verdict.bands.items():
        advice = advice_for(nutrient, band, entries, stage=stage, months=months)
        if advice is None:
            continue
        if advice.text:
            actions[nutrient] = advice.text
        removed.extend(advice.removed)
    return DietBalance(verdict=verdict, actions=actions, removed_foods=tuple(removed))


async def compare_diet_balance(context: FoodContext, args: CompareDietBalanceArgs) -> ToolResult:
    """기간 내 식단의 영양소 비중을 연령별 기준과 비교한다. 계산은 전부 코드가 한다.

    `evaluate_nutrient_bands`(판정 창 고정 7일)에 그대로 맡긴다 — `args.period` 는 조회
    tool(`analyze_meal_records` 등)의 기간 라벨과 달리 이 판정의 창 길이를 바꾸지 않는다.
    기록·해석이 모자라면(`insufficient`) 구간 없이 건수만 돌려준다. 구간이 나온 항목만
    `nutrient_actions.advice_for` 로 식품군 안내를 받고, 아이의 알레르기 때문에 안내 식품이
    다 빠지면 구간만 말한다 — 다른 급원 식품을 지어내지 않는다(리뷰 초점 5).
    """
    balance = await diet_balance(context)
    verdict = balance.verdict
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

    context.state.basis_keys = set(verdict.bands)
    # report_nutrient_analysis 의 check_summary 가 요약 문장에 이 이름이 남았는지 본다(N-14)
    context.state.removed_foods = balance.removed_foods
    return ok(
        "query",
        "diet_balance",
        bands=dict(verdict.bands),
        unscored=list(verdict.unscored),
        food_group_actions=list(balance.actions.values()),
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

    검사 순서 — ⓪ 판정(`compare_diet_balance`)이 있는지, `insufficient` 와 findings 유무가
    맞는지 ① 문장 규칙(`nutrient_wording.wording_problem` — 수치 · 처방 · 몸 상태 · 아이 주어 ·
    식사 주어) ② `check_summary`(별칭 + 안전 사전) ③ `basis_keys` 밖 항목 ④ `seen_refs` 밖 근거
    제거(거절이 아니라 제외). ⓪~③ 중 하나라도 걸리면 findings 전체를 거절한다 — 분석 결과는
    저장하지 않고 반환만 한다.

    안전 정보(`context.state.safety`)가 None 이면 RuntimeError 다 — 조회 실패를 빈 목록으로 보고
    알레르기 식품 검사를 건너뛰지 않는다.
    """
    ports = context.ports
    birth_date = await ports.profile.birth_date(child_id=context.child_id)
    months = months_between(birth_date, context.today)
    stage = life_stage(birth_date, context.today).stage
    entries = _safety_entries(context)

    # 판정은 코드가 한다 — compare_diet_balance 를 안 부르고 모델 문장만 나가거나, 빈 findings 가
    # ok 로 끝나 화면이 비는 일이 없게 한다
    verdict = context.state.nutrient_verdict
    if verdict is None:
        return fail(
            "report",
            "nutrient_analysis",
            ErrorCode.INVALID_ARGS,
            "compare_diet_balance 를 먼저 부른다. 구간 판정 없이는 findings 를 낼 수 없다.",
        )
    # 기록이 부족하거나(insufficient) 모든 항목이 빈 칸이라 구간이 하나도 없으면 말할 구간이 없다 —
    # 그때 "균형 잡힌 식단" 같은 일반 문장은 근거 없는 안심이라 findings 를 비우게 한다
    has_bands = bool(verdict.bands) and not verdict.insufficient
    if not has_bands and args.findings:
        return fail(
            "report",
            "nutrient_analysis",
            ErrorCode.INVALID_ARGS,
            "기록이 부족하거나 판정한 항목이 없으면 findings 를 비운다. 구간을 말하지 않는다.",
        )
    if has_bands and not args.findings:
        return fail(
            "report",
            "nutrient_analysis",
            ErrorCode.INVALID_ARGS,
            "findings 가 비어 있다. compare_diet_balance 가 낸 구간을 적어도 하나는 말한다.",
        )

    for finding in args.findings:
        problem = wording_problem(finding)
        if problem is not None:
            return fail(
                "report", "nutrient_analysis", ErrorCode.INVALID_ARGS, WORDING_MESSAGES[problem]
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
