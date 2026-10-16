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


def nutrition_catalog_rows(
    rows: Iterable[RawNutriRow],
    *,
    now: datetime,
    borrowed: Mapping[str, frozenset[str]] | None = None,
) -> list[MenuCatalogRow]:
    """품목대표 전체 → `menu_catalog` 행. 쓸 수 있는 행이 없는 이름은 빠진다(요청 중과 같다).

    `borrowed` 는 `menu_key` → 이름이 같은 레시피의 식품군(`borrowable_food_groups`). 대응표에 없는
    메뉴만 쓴다."""
    picked = nutrition_facts_by_key(rows)
    lent = borrowed or {}
    return [
        nutrition_catalog_row(key, facts, now=now, borrowed=lent.get(key, frozenset()))
        for key, facts in sorted(picked.items())
    ]


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


def borrowable_food_groups(recipes: Iterable[MenuCatalogRow]) -> dict[str, frozenset[str]]:
    """레시피 행 → 이름이 같은 영양DB 메뉴가 빌릴 식품군 (#420).

    찌개 · 국 같은 음식 메뉴는 대분류가 여러 식품군을 섞어 대응표에 줄이 없다. 이름이 정확히 같은
    레시피가 있으면 그 주재료 식품군을 쓴다 — 비슷한 이름으로 추정하지 않는다(`nutrition_pick`
    규칙 1, 키 맞추기는 `recipe_catalog_rows` 의 영양값과 같다). 해석된 레시피(재료가 다 정리됨) 중
    식품군이 있는 것만 쓴다. 같은 이름 레시피가 여럿이고 식품군이 다르면 고르지 않고 빌려 주지
    않는다.
    """
    seen: dict[str, set[frozenset[str]]] = {}
    for row in recipes:
        if row.resolved and row.food_groups:
            seen.setdefault(row.menu_key, set()).add(row.food_groups)
    return {key: next(iter(groups)) for key, groups in seen.items() if len(groups) == 1}


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
