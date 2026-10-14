"""배치가 쓰는 카탈로그 행 만들기 — 요청 중 조회와 같은 함수를 부른다 (FT-19).

영양DB 행은 `menu_resolver.nutrition_catalog_row`, 레시피 행은 `recipe_catalog.build_recipe_row`
하나로만 만든다. 여기는 원문 전체를 그 함수들에 차례로 넘기는 것뿐이다. 요청 경로에서는 부르지
않는다(`ingredient_table` 처럼 배치 전용).
"""

from collections.abc import Iterable, Mapping
from datetime import datetime

from app.agents.food.menu_resolver import nutrition_catalog_row
from app.agents.food.recipe_catalog import IngredientEntry, build_recipe_row
from app.agents.food.store.ports import MenuCatalogRow
from app.core.mafra_recipe_raw import RecipeSync
from app.core.mfds_nutri_raw import RawNutriRow
from app.core.nutrition_pick import SOURCE_VERSION, NutritionFacts, nutrition_facts_by_key
from app.rules.menu_key import normalize_menu_key


def nutrition_catalog_rows(rows: Iterable[RawNutriRow], *, now: datetime) -> list[MenuCatalogRow]:
    """품목대표 전체 → `menu_catalog` 행. 쓸 수 있는 행이 없는 이름은 빠진다(요청 중과 같다)."""
    picked = nutrition_facts_by_key(rows)
    return [nutrition_catalog_row(key, facts, now=now) for key, facts in sorted(picked.items())]


def recipe_catalog_rows(
    sync: RecipeSync,
    table: Mapping[str, IngredientEntry],
    *,
    now: datetime,
    nutrition: Mapping[str, NutritionFacts],
) -> list[MenuCatalogRow]:
    """레시피 전체 → `recipe_catalog` 행. 영양값은 레시피 이름이 영양DB 와 정확히 같을 때만
    붙인다(`build_recipe_row` 의 약속 — 키가 같은지는 `normalize_menu_key` 로 본다)."""
    return [
        build_recipe_row(
            recipe, table, now=now, nutrition=nutrition.get(normalize_menu_key(recipe.name))
        )
        for recipe in sync.recipes
    ]


def facts_from_catalog(row: MenuCatalogRow) -> NutritionFacts | None:
    """`menu_catalog` 의 영양DB 행 → 레시피 배치가 붙일 영양값. 사람이 넣은 행은 None —
    영양값은 영양DB 에서만 채운다(own_table §1 `nutrients`)."""
    if row.source != "mfds_nutri" or not row.food_code:
        return None
    return NutritionFacts(
        food_code=row.food_code,
        name=row.display_name,
        category="",
        serving_g=row.serving_g,
        per_100g=dict(row.nutrients),
        source_version=row.source_version or SOURCE_VERSION,
    )
