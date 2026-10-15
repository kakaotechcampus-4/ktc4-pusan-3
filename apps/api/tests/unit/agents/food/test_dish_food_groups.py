"""음식 행 식품군 (#420) — 이름이 같은 레시피의 주재료 식품군을 빌린다.

완료 기준: 된장찌개 · 시금치나물 · 쌀밥만 먹은 7일치(가짜 아이)에서 식품군을 아는 메뉴가 절반을
넘어 출현 문장이 나온다. 대응표는 커밋된 `reference/food_groups.yaml` 을 그대로 쓴다(시금치나물은
나물·숙채류 줄, 쌀밥은 메뉴 이름 줄). 이름 · 영양값은 가짜다.
"""

from datetime import UTC, datetime, timedelta

from app.agents.food.catalog_rows import borrowable_food_groups, nutrition_catalog_rows
from app.agents.food.nutrient_wording import appearance_readouts
from app.agents.food.nutrition_eval import evaluate_nutrient_bands
from app.agents.food.recipe_catalog import build_recipe_row, parse_ingredient_table
from app.agents.food.store.ports import MenuCatalogRow
from app.core.mafra_recipe_raw import RawIngredient, RawRecipe
from app.core.mfds_nutri_raw import CORE_NUTRIENT_KEYS, RawNutriRow
from app.rules.menu_key import normalize_menu_key
from tests.unit.agents.food.test_nutrition_eval import (  # 기존 픽스처 재사용
    _WINDOW_END,
    BIRTH_DATE_3Y,
    _context,
    _observation,
)

NOW = datetime(2026, 10, 15, tzinfo=UTC)

TABLE = parse_ingredient_table(
    {
        "source": "테스트",
        "version": "t",
        "fetched_at": "2026-10-15",
        "ingredients": [
            {
                "raw": "두부",
                "names": ["두부"],
                "codes": [5],
                "status": "ok",
                "food_group": "meat_fish_egg_legumes",
            },
            {
                "raw": "애호박",
                "names": ["애호박"],
                "codes": [],
                "status": "ok",
                "food_group": "vegetables",
            },
            {"raw": "된장", "names": ["된장"], "codes": [5], "status": "ok"},
        ],
    }
)
DOENJANG = RawRecipe(
    "1",
    "된장찌개",
    ingredients=(
        RawIngredient("두부", role="주재료"),
        RawIngredient("애호박", role="주재료"),
        RawIngredient("된장", role="양념"),
    ),
)


def recipe_row(
    name: str, groups: set[str], *, resolved: bool = True, sid: str = "1"
) -> MenuCatalogRow:
    return MenuCatalogRow(
        menu_key=normalize_menu_key(name),
        display_name=name,
        source="mafra_recipe",
        resolved=resolved,
        synced_at=NOW,
        food_groups=frozenset(groups),
        source_id=sid,
    )


def dish(name: str, category: str, code: str) -> RawNutriRow:
    return RawNutriRow(
        food_code=f"D-{name}",
        name=name,
        db_group="음식",
        db_class="품목대표",
        category=category,
        category_code=code,
        researched_on="20200101",
        food_weight="200g",
        nutrients={key: "10" for key in CORE_NUTRIENT_KEYS},
    )


# ── 빌려 줄 식품군 ────────────────────────────────────────────────────
def test_해석된_레시피의_식품군을_이름으로_빌려준다() -> None:
    lent = borrowable_food_groups([recipe_row("된장찌개", {"vegetables", "meat_fish_egg_legumes"})])

    assert lent == {"된장찌개": frozenset({"vegetables", "meat_fish_egg_legumes"})}


def test_해석_안_된_레시피와_식품군이_빈_레시피는_빌려주지_않는다() -> None:
    lent = borrowable_food_groups(
        [recipe_row("된장찌개", {"vegetables"}, resolved=False), recipe_row("미역국", set())]
    )

    assert lent == {}


def test_같은_이름_레시피의_식품군이_다르면_빌려주지_않는다() -> None:
    lent = borrowable_food_groups(
        [
            recipe_row("잡채", {"vegetables"}, sid="1"),
            recipe_row("잡채", {"vegetables", "meat_fish_egg_legumes"}, sid="2"),
            recipe_row("김치찌개", {"vegetables"}, sid="3"),
            recipe_row("김치찌개", {"vegetables"}, sid="4"),
        ]
    )

    # 어느 쪽인지 고르지 않는다 — 못 고르는 쪽으로 틀린다
    assert lent == {"김치찌개": frozenset({"vegetables"})}


# ── 완료 기준 ─────────────────────────────────────────────────────────
async def test_찌개_나물_쌀밥만_먹은_7일도_식품군_출현_문장이_나온다() -> None:
    lent = borrowable_food_groups([build_recipe_row(DOENJANG, TABLE, now=NOW)])
    rows = nutrition_catalog_rows(
        [
            dish("된장찌개", "찌개 및 전골류", "06"),
            # 음식 13 은 나물·숙채류다 — 원재료성 13(우유)으로 새면 dairy 가 된다
            dish("시금치나물", "나물·숙채류", "13"),
            dish("쌀밥", "밥류", "01"),
        ],
        now=NOW,
        borrowed=lent,
    )
    observations = [
        _observation(subject=name, amount_text="한 그릇", day=_WINDOW_END - timedelta(days=offset))
        for offset in range(7)
        for name in ("된장찌개", "시금치나물", "쌀밥")
    ]

    verdict = await evaluate_nutrient_bands(
        _context(observations=observations, catalog_rows=rows, birth_date=BIRTH_DATE_3Y),
        months=40,
    )

    assert verdict.appearance_ready
    body = appearance_readouts(verdict, blocked_groups=())[0].body
    assert "기록된 7일 중 곡류가 나온 날은 7일이에요." in body
    assert "기록된 7일 중 고기·생선·달걀·콩이 나온 날은 7일이에요." in body
    assert "기록된 7일 중 채소가 나온 날은 7일이에요." in body
    assert "기록된 7일 중 우유·유제품이 나온 날은 0일이에요." in body


def test_이름이_같은_레시피가_없는_음식은_비어_있다() -> None:
    rows = nutrition_catalog_rows([dish("미역국", "국 및 탕류", "05")], now=NOW, borrowed={})

    assert rows[0].food_groups == frozenset()
