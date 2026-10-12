"""영양 tool 3개 — 영양성분 조회 · 식단 균형 비교 · 영양소 분석 보고.

정본은 `docs/agents/food/Food_Tool_명세.md` §2 · `docs/agents/food/영양소_계산_설계.md`.
"""

from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from app.agents.food.context import FoodContext
from app.agents.food.schemas.nutrition import LookupNutritionArgs
from app.agents.food.store import InMemoryMenuCatalog, MenuCatalogRow, in_memory_ports
from app.agents.food.tools.nutrition import lookup_nutrition

KST = ZoneInfo("Asia/Seoul")
CHILD = UUID(int=1)
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=KST)

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


@pytest.fixture
def context() -> FoodContext:
    ports = in_memory_ports(catalog=InMemoryMenuCatalog([DOENJANG_ROW]))
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
