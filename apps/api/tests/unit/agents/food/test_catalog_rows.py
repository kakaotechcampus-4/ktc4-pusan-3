"""배치 행 만들기 — 요청 중 조회와 같은 함수 · 같은 행 (FT-19)."""

from dataclasses import replace
from datetime import UTC, datetime

from app.agents.food.catalog_rows import (
    facts_from_catalog,
    nutrition_catalog_rows,
    recipe_catalog_rows,
)
from app.agents.food.menu_resolver import MenuResolver, NotFoundMemory, Resolved
from app.agents.food.recipe_catalog import parse_ingredient_table
from app.agents.food.store import InMemoryMenuCatalog, InMemoryMenuSource
from app.core.mafra_recipe_raw import RawIngredient, RawRecipe, RecipeSync
from app.core.mfds_nutri_raw import CORE_NUTRIENT_KEYS, RawNutriRow

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def raw(name: str, *, code: str = "D101", klass: str = "품목대표") -> RawNutriRow:
    return RawNutriRow(
        food_code=code,
        name=name,
        db_group="음식",
        db_class=klass,
        category="찌개 및 전골류",
        food_weight="300g",
        nutrients={key: "1.5" for key in CORE_NUTRIENT_KEYS},
    )


def sync(*recipes: RawRecipe) -> RecipeSync:
    return RecipeSync(
        recipes=recipes,
        recipe_rows=len(recipes),
        ingredient_rows=0,
        orphan_ingredient_rows=0,
        unnamed_ingredient_rows=0,
    )


TABLE = parse_ingredient_table(
    {
        "source": "mafra_recipe",
        "version": "test",
        "fetched_at": "2026-10-14",
        "ingredients": [
            {"raw": "두부", "names": ["두부"], "codes": [5], "status": "ok"},
            {"raw": "된장", "names": ["된장"], "codes": [5], "status": "ok"},
        ],
    }
)


async def test_배치가_만든_영양DB_행은_요청_중에_만든_행과_같다():
    rows = (raw("된장찌개"), raw("된장찌개_냉이", code="D102"))
    batch = {row.menu_key: row for row in nutrition_catalog_rows(rows, now=NOW)}

    resolver = MenuResolver(
        catalog=InMemoryMenuCatalog(),
        source=InMemoryMenuSource({"된장찌개": rows}),
        now=NOW,
        not_found=NotFoundMemory(),
    )
    found = await resolver.resolve("된장찌개")

    assert isinstance(found, Resolved)
    assert found.row == batch["된장찌개"]


def test_쓸_수_없는_행은_카탈로그_행이_되지_않는다():
    # 상용제품만 있는 이름은 요청 중 조회도 받지 않는다
    assert nutrition_catalog_rows([raw("우유", klass="상용제품")], now=NOW) == []


def test_레시피_이름이_영양DB_와_같을_때만_영양값을_붙인다():
    [facts_row] = nutrition_catalog_rows([raw("된장찌개")], now=NOW)
    nutrition = {facts_row.menu_key: facts_from_catalog(facts_row)}
    recipes = sync(
        RawRecipe("1", "된장찌개", ingredients=(RawIngredient("두부"), RawIngredient("된장"))),
        RawRecipe("2", "두부된장국", ingredients=(RawIngredient("두부"), RawIngredient("된장"))),
    )

    made = {
        row.source_id: row
        for row in recipe_catalog_rows(recipes, TABLE, now=NOW, nutrition=nutrition)
    }

    assert made["1"].food_code == "D101" and made["1"].nutrients
    assert made["2"].food_code is None and made["2"].nutrients == {}
    assert made["1"].resolved and made["1"].allergen_codes == frozenset({5})


def test_영양DB_행이_아니면_영양값으로_되돌리지_않는다():
    [row] = nutrition_catalog_rows([raw("된장찌개")], now=NOW)
    assert facts_from_catalog(replace(row, source="manual")) is None
    facts = facts_from_catalog(row)
    assert (facts.food_code, facts.serving_g, facts.per_100g) == (
        row.food_code,
        row.serving_g,
        row.nutrients,
    )
