"""영양 tool 3개 — 영양성분 조회 · 식단 균형 비교 · 영양소 분석 보고.

정본은 `docs/agents/food/Food_Tool_명세.md` §2 · `docs/agents/food/영양소_계산_설계.md`.
"""

from dataclasses import fields
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from app.agents.food.context import FoodContext
from app.agents.food.nutrition_eval import NutrientVerdict
from app.agents.food.schemas.common import EvidenceRef, MealPeriod
from app.agents.food.schemas.nutrition import (
    CompareDietBalanceArgs,
    LookupNutritionArgs,
    NutrientReportArgs,
)
from app.agents.food.store import (
    DaycareMealRow,
    FoodPorts,
    InMemoryDaycareMeals,
    InMemoryMenuCatalog,
    InMemoryProfile,
    MenuCatalogRow,
    SafetyEntry,
    in_memory_ports,
)
from app.agents.food.tools.nutrition import (
    compare_diet_balance,
    lookup_nutrition,
    report_nutrient_analysis,
)

UNSEEN_ID = str(uuid4())

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=KST)
BIRTH_DATE = date(2024, 6, 2)  # NOW 기준 28개월 — 섭취기준 1-2y 구간
TODAY = NOW.date()

DOENJANG_NUTRIENTS = {
    "energy": 80.0,
    "protein": 10.0,
    "fat": 3.0,
    "carbohydrate": 5.0,
    "calcium": 20.0,
    "iron": 1.5,
    "sodium": 500.0,
}
DOENJANG_ROW = MenuCatalogRow(
    menu_key="된장찌개",
    display_name="된장찌개",
    source="mfds_nutri",
    resolved=True,
    synced_at=NOW,
    serving_g=300.0,
    nutrients=dict(DOENJANG_NUTRIENTS),
    food_groups=frozenset({"고기생선달걀콩", "채소"}),
)

# ── Task 8 전용 데이터 — 단백질이 낮은 식단 ──────────────────────────────
_LOW_PROTEIN_MENU = "저단백식품"
_FILLER_MENU = "필러"


def _low_protein_row() -> MenuCatalogRow:
    # protein %kcal = (2*4)/100*100 = 8% — AMDR(10~20%) 미만 → low
    return MenuCatalogRow(
        menu_key=_LOW_PROTEIN_MENU,
        display_name=_LOW_PROTEIN_MENU,
        source="manual",
        resolved=True,
        synced_at=NOW,
        serving_g=1000.0,
        nutrients={
            "protein": 2.0,
            "fat": 5.0,
            "carbohydrate": 20.0,
            "calcium": 50.0,
            "iron": 1.0,
            "sodium": 100.0,
            "energy": 100.0,
        },
        food_groups=frozenset({"곡류"}),
    )


def _filler_row() -> MenuCatalogRow:
    # 기록 행 수만 채우는 메뉴 — 다른 영양소 판정을 건드리지 않게 나트륨 한 칸만 둔다
    return MenuCatalogRow(
        menu_key=_FILLER_MENU,
        display_name=_FILLER_MENU,
        source="manual",
        resolved=True,
        synced_at=NOW,
        serving_g=200.0,
        nutrients={"sodium": 5.0},
        food_groups=frozenset(),
    )


def _week_of_rows(menu_key: str) -> list[DaycareMealRow]:
    """최근 7일(판정 창), 하루 두 행 — AMDR·상한 항목은 이 창 하나만 본다(Task 6)."""
    rows: list[DaycareMealRow] = []
    for offset in range(7):
        day = (TODAY - timedelta(days=1)) - timedelta(days=offset)
        rows.append(
            DaycareMealRow(
                id=uuid4(),
                child_id=CHILD,
                serve_date=day,
                meal_slot="lunch",
                menu_keys=(menu_key,),
                amount_factor=1.0,
                amount_known=True,
            )
        )
        rows.append(
            DaycareMealRow(
                id=uuid4(),
                child_id=CHILD,
                serve_date=day,
                meal_slot="snack_am",
                menu_keys=(_FILLER_MENU,),
                amount_factor=1.0,
                amount_known=True,
            )
        )
    return rows


@pytest.fixture
def context() -> FoodContext:
    ports = in_memory_ports(
        catalog=InMemoryMenuCatalog([DOENJANG_ROW, _low_protein_row(), _filler_row()]),
        daycare=InMemoryDaycareMeals(_week_of_rows(_LOW_PROTEIN_MENU)),
        profile=InMemoryProfile({CHILD: BIRTH_DATE}),
    )
    return FoodContext(child_id=CHILD, run_id="run-1", now=NOW, timezone=KST, ports=ports)


@pytest.fixture
def context_egg_allergy(context: FoodContext) -> FoodContext:
    context.state.safety = (SafetyEntry(kind="allergy", label="달걀", status="active"),)
    return context


@pytest.fixture
def context_milk_allergy_calcium_low() -> FoodContext:
    calcium_only_row = MenuCatalogRow(
        menu_key="저칼슘식품",
        display_name="저칼슘식품",
        source="manual",
        resolved=True,
        synced_at=NOW,
        serving_g=1000.0,
        nutrients={"calcium": 20.0},  # EAR(400mg) 에 한참 못 미친다 — low
        food_groups=frozenset({"채소"}),
    )
    ports = in_memory_ports(
        catalog=InMemoryMenuCatalog([calcium_only_row, _filler_row()]),
        daycare=InMemoryDaycareMeals(_week_of_rows("저칼슘식품")),
        profile=InMemoryProfile({CHILD: BIRTH_DATE}),
    )
    ctx = FoodContext(child_id=CHILD, run_id="run-1", now=NOW, timezone=KST, ports=ports)
    ctx.state.safety = (SafetyEntry(kind="allergy", label="우유", status="active"),)
    return ctx


@pytest.fixture
def context_8rows() -> FoodContext:
    # 판정 창은 7일뿐이라 8행을 넣으려면 그중 하루에 두 끼를 둔다
    rows = [
        DaycareMealRow(
            id=uuid4(),
            child_id=CHILD,
            serve_date=(TODAY - timedelta(days=1)) - timedelta(days=offset),
            meal_slot="lunch",
            menu_keys=(_LOW_PROTEIN_MENU,),
            amount_factor=1.0,
            amount_known=True,
        )
        for offset in range(7)  # 최근 7일, 하루 한 행
    ]
    rows.append(
        DaycareMealRow(
            id=uuid4(),
            child_id=CHILD,
            serve_date=TODAY - timedelta(days=1),
            meal_slot="snack_am",
            menu_keys=(_LOW_PROTEIN_MENU,),
            amount_factor=1.0,
            amount_known=True,
        )
    )  # 8번째 행 — 최근 7일 중 하루에 한 끼 더
    ports = in_memory_ports(
        catalog=InMemoryMenuCatalog([_low_protein_row()]),
        daycare=InMemoryDaycareMeals(rows),
        profile=InMemoryProfile({CHILD: BIRTH_DATE}),
    )
    return FoodContext(child_id=CHILD, run_id="run-1", now=NOW, timezone=KST, ports=ports)


# ── lookup_nutrition (Task 7) ────────────────────────────────────────────
async def test_카탈로그에_있는_메뉴를_돌려준다(context) -> None:
    result = await lookup_nutrition(context, LookupNutritionArgs(foods=["된장찌개"]))

    assert result.success
    assert result.data["found"][0]["menu_key"] == "된장찌개"


async def test_못_찾은_이름은_따로_돌려주고_비슷한_값으로_대체하지_않는다(context) -> None:
    result = await lookup_nutrition(context, LookupNutritionArgs(foods=["맛있는된장찌개"]))

    assert result.data["found"] == []
    assert result.data["unresolved"] == ["맛있는된장찌개"]


async def test_수치는_조회한_값_그대로다(context) -> None:
    result = await lookup_nutrition(context, LookupNutritionArgs(foods=["된장찌개"]))

    assert result.data["found"][0]["per_100g"] == DOENJANG_NUTRIENTS


# ── compare_diet_balance (Task 8) ────────────────────────────────────────
async def test_구간과_식품군_안내를_같이_돌려준다(context) -> None:
    result = await compare_diet_balance(
        context, CompareDietBalanceArgs(period=MealPeriod.LAST_7_DAYS)
    )

    assert result.data["bands"]["protein"] == "low"
    assert "고기" in " ".join(result.data["food_group_actions"])


async def test_달걀_알레르기면_안내에서_달걀이_빠진다(context_egg_allergy) -> None:
    # 안전 구멍 ① — 표는 아이를 모른다 (설계 §6 · 루트 §2)
    result = await compare_diet_balance(
        context_egg_allergy, CompareDietBalanceArgs(period=MealPeriod.LAST_7_DAYS)
    )

    joined = " ".join(result.data["food_group_actions"])
    assert "달걀" not in joined and "계란" not in joined


async def test_안내할_식품이_다_빠지면_구간만_돌려준다(context_milk_allergy_calcium_low) -> None:
    # 리뷰 초점 5 — 다른 급원 식품을 지어내지 않는다
    result = await compare_diet_balance(
        context_milk_allergy_calcium_low, CompareDietBalanceArgs(period=MealPeriod.LAST_7_DAYS)
    )

    assert result.data["bands"]["calcium"] == "low"
    assert result.data["food_group_actions"] == []


async def test_기록이_모자라면_구간_대신_건수를_돌려준다(context_8rows) -> None:
    result = await compare_diet_balance(
        context_8rows, CompareDietBalanceArgs(period=MealPeriod.LAST_7_DAYS)
    )

    assert result.data["insufficient"] is True
    assert result.data["records"] == 8
    assert result.data["bands"] == {}


async def test_돌려준_항목만_basis_keys_에_남는다(context) -> None:
    result = await compare_diet_balance(
        context, CompareDietBalanceArgs(period=MealPeriod.LAST_7_DAYS)
    )

    assert context.state.basis_keys == set(result.data["bands"])


# ── report_nutrient_analysis (Task 9) ────────────────────────────────────
@pytest.fixture
def context_with_basis(context: FoodContext) -> FoodContext:
    context.state.basis_keys = {"protein"}
    return context


@pytest.fixture
def context_egg_allergy_basis(context: FoodContext) -> FoodContext:
    context.state.basis_keys = {"iron"}
    context.state.safety = (SafetyEntry(kind="allergy", label="달걀", status="active"),)
    context.state.removed_foods = ("달걀노른자",)
    return context


@pytest.mark.parametrize(
    "finding",
    [
        "단백질이 하루 20g 부족해요",  # 수치
        "칼슘 섭취가 60% 수준이에요",  # %
        "철 결핍이 의심돼요",  # 진단
        "영양제를 챙겨 주세요",  # 처방
        "달걀 반찬을 늘려 주세요",  # 식품 제안
    ],
)
async def test_거절하는_문장(context, finding: str) -> None:
    result = await report_nutrient_analysis(
        context, NutrientReportArgs(period=MealPeriod.LAST_7_DAYS, findings=[finding])
    )

    assert not result.success
    assert result.error["code"] == "INVALID_ARGS"


async def test_구간_문장은_통과한다(context_with_basis) -> None:
    result = await report_nutrient_analysis(
        context_with_basis,
        NutrientReportArgs(
            period=MealPeriod.LAST_7_DAYS, findings=["단백질 비중이 기준보다 낮은 편이에요"]
        ),
    )

    assert result.success


@pytest.fixture
def context_sufficient(context_with_basis: FoodContext) -> FoodContext:
    # compare_diet_balance 가 구간을 낸 상태 — findings 가 비어 있으면 모델이 아무것도
    # 말하지 않은 채 "ok" 로 끝난다(라이브 eval T14 에서 재현)
    context_with_basis.state.nutrient_verdict = NutrientVerdict(
        bands={"protein": "low"},
        unscored=(),
        records=14,
        resolved_ratio=1.0,
        insufficient=False,
        food_groups=frozenset(),
    )
    return context_with_basis


@pytest.fixture
def context_insufficient(context: FoodContext) -> FoodContext:
    context.state.basis_keys = set()
    context.state.nutrient_verdict = NutrientVerdict(
        bands={},
        unscored=(),
        records=2,
        resolved_ratio=1.0,
        insufficient=True,
        food_groups=frozenset(),
    )
    return context


async def test_기록이_충분한데_findings_가_비어있으면_거절한다(context_sufficient) -> None:
    result = await report_nutrient_analysis(
        context_sufficient, NutrientReportArgs(period=MealPeriod.LAST_7_DAYS, findings=[])
    )

    assert not result.success
    assert result.error["code"] == "INVALID_ARGS"


async def test_insufficient_인데_findings_가_있으면_거절한다(context_insufficient) -> None:
    # basis_keys 가 비어 있어 영양 항목을 말하면 이미 거절되므로, 항목명 없이도 걸리는지 본다
    result = await report_nutrient_analysis(
        context_insufficient,
        NutrientReportArgs(period=MealPeriod.LAST_7_DAYS, findings=["기록이 아직 많지 않아요"]),
    )

    assert not result.success
    assert result.error["code"] == "INVALID_ARGS"


async def test_basis_keys_밖_항목은_거절한다(context_with_basis) -> None:
    # compare_diet_balance 가 돌려주지 않은 항목으로 말하면 지어낸 것이다
    result = await report_nutrient_analysis(
        context_with_basis,
        NutrientReportArgs(period=MealPeriod.LAST_7_DAYS, findings=["아연 비중이 낮은 편이에요"]),
    )

    assert not result.success


async def test_이번_run_에서_조회하지_않은_근거는_뺀다(context_with_basis) -> None:
    result = await report_nutrient_analysis(
        context_with_basis,
        NutrientReportArgs(
            period=MealPeriod.LAST_7_DAYS,
            findings=["단백질 비중이 기준보다 낮은 편이에요"],
            evidence=[EvidenceRef(kind="observation_food", id=UNSEEN_ID, note="테스트")],
        ),
    )

    assert result.success
    assert result.data["evidence"] == []


async def test_뺀_알레르기_식품이_문장에_남으면_거절한다(context_egg_allergy_basis) -> None:
    # N-14 — 별칭과 안전 사전을 둘 다 건다 (설계 §6 · #326 리뷰)
    result = await report_nutrient_analysis(
        context_egg_allergy_basis,
        NutrientReportArgs(
            period=MealPeriod.LAST_7_DAYS, findings=["노른자가 든 반찬이 도움이 돼요"]
        ),
    )

    assert not result.success


async def test_분석_결과를_저장하지_않는다(context_with_basis) -> None:
    # 영양 분석 결과를 저장하는 포트 자체가 없다 — test_food_store.py 의
    # test_영양_구간을_저장하는_포트가_없다 가 구조로 고정한다. 여기서는 호출이 그대로
    # 성공하고 반환값 말고 다른 곳에 남기지 않는지만 본다
    result = await report_nutrient_analysis(
        context_with_basis,
        NutrientReportArgs(
            period=MealPeriod.LAST_7_DAYS, findings=["단백질 비중이 기준보다 낮은 편이에요"]
        ),
    )

    assert result.success
    assert "bands" not in {f.name for f in fields(FoodPorts)}
