"""식품 대분류 → 6개 식품군. `reference/food_groups.yaml` 을 읽어 코드가 대조한다.

영양DB 행(`NutritionFacts`)을 식품군으로 옮긴다. 모델이 고르지 않고, 표에 없으면 빈 집합이다 —
이름으로 식품군을 추정하지 않는다. 식품군 이름은 `food_doc.yaml` 이 쓰는 여섯 개
(`doc_seed.FOOD_GROUPS`)와 같아야 하고, 어긋나면 읽을 때 바로 실패한다.

찾는 순서는 메뉴 이름 줄 → 대분류 이름 줄 → (원재료성이고 대분류 이름이 비었을 때) 대분류 코드
줄이다. 그래도 없으면 적재 배치가 이름이 같은 레시피의 주재료 식품군을 빌린다
(`catalog_rows.borrowable_food_groups`).

레시피 행의 식품군은 이 표가 아니라 재료 정리 표(`recipe_ingredients.yaml`)의 재료별 칸에서 모은다
(`recipe_catalog.py`).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any

from app.agents.common.reference import load_reference
from app.agents.food.doc_seed import FOOD_GROUPS
from app.core.nutrition_pick import NutritionFacts
from app.rules.term_match import normalize

# 대분류 코드로 찾는 DB 그룹. 코드 뜻이 그룹마다 달라 이 그룹만 본다 — 원재료성 09 는 육류,
# 음식 09 는 전·적 및 부침류다
CODE_GROUP = "원재료성"

_TOP_KEYS = frozenset({"source", "version", "fetched_at", "categories", "codes", "menus"})
_CATEGORY_KEYS = frozenset({"category", "groups"})
_CODE_KEYS = frozenset({"code", "category"})
_MENU_KEYS = frozenset({"menu", "groups"})
_CODE = re.compile(r"\d{2}")


@dataclass(frozen=True)
class FoodGroupTable:
    categories: Mapping[str, frozenset[str]]  # 정규화한 대분류 이름 → 식품군
    codes: Mapping[str, str]  # 원재료성 대분류 코드 → 정규화한 대분류 이름
    menus: Mapping[str, frozenset[str]]  # 정규화한 메뉴 이름 → 식품군


def parse_food_group_table(data: Mapping[str, Any]) -> FoodGroupTable:
    """대응표를 검사해 돌려준다. 모양이 틀리면 `ValueError`."""
    if extra := set(data) - _TOP_KEYS:
        raise ValueError(f"food_groups.yaml 에 정해지지 않은 칸이 있다: {sorted(extra)}")
    categories = _group_rows(data.get("categories"), name_key="category", keys=_CATEGORY_KEYS)
    codes: dict[str, str] = {}
    for row in data.get("codes") or []:
        _check_row(row, _CODE_KEYS)
        code = row.get("code")
        # 따옴표 없이 09 를 적으면 YAML 이 숫자 9 로 읽는다 — 글자로만 받는다
        if not isinstance(code, str) or not _CODE.fullmatch(code):
            raise ValueError(f'food_groups.yaml 코드 {code!r}: 두 자리 글자여야 한다("09")')
        name = normalize(str(row.get("category") or ""))
        if name not in categories:
            raise ValueError(
                f"food_groups.yaml 코드 {code}: categories 에 없는 대분류 {row.get('category')!r}"
            )
        if code in codes:
            raise ValueError(f"food_groups.yaml 코드 {code} 가 두 번 적혀 있다")
        codes[code] = name
    menus = _group_rows(data.get("menus"), name_key="menu", keys=_MENU_KEYS)
    return FoodGroupTable(categories=categories, codes=codes, menus=menus)


def parse_category_table(data: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """대응표의 대분류 줄만. 모양 검사는 표 전체에 한다."""
    return dict(parse_food_group_table(data).categories)


@cache
def food_group_table() -> FoodGroupTable:
    """`reference/food_groups.yaml`."""
    return parse_food_group_table(load_reference("food_groups.yaml"))


def category_table() -> Mapping[str, frozenset[str]]:
    """정규화한 대분류 이름 → 식품군."""
    return food_group_table().categories


def food_groups_of_category(category: str | None) -> frozenset[str]:
    """식품 대분류의 식품군. 비었거나 표에 없으면 빈 집합 — 추정하지 않는다."""
    key = normalize(category or "")
    return category_table().get(key, frozenset()) if key else frozenset()


def food_groups_of_menu(menu_key: str, facts: NutritionFacts) -> frozenset[str]:
    """영양DB 메뉴의 식품군. 메뉴 이름 줄 → 대분류 이름 줄 → (원재료성이고 대분류 이름이 비었을
    때) 대분류 코드 줄. 다 없으면 빈 집합이다 — 레시피에서 빌리는 것은 배치가 한다."""
    table = food_group_table()
    if menu_key and (groups := table.menus.get(menu_key)):
        return groups
    if facts.category.strip():
        # 이름이 있는데 표에 없으면 일부러 뺀 대분류다 — 코드로 다시 찾지 않는다
        return food_groups_of_category(facts.category)
    if facts.db_group == CODE_GROUP and facts.category_code:
        name = table.codes.get(facts.category_code.strip())
        return table.categories.get(name, frozenset()) if name else frozenset()
    return frozenset()


def _check_row(row: Any, keys: frozenset[str]) -> None:
    if not isinstance(row, Mapping):
        raise ValueError("food_groups.yaml 의 항목은 객체여야 한다")
    if extra := set(row) - keys:
        raise ValueError(f"food_groups.yaml 항목에 정해지지 않은 칸: {sorted(extra)}")


def _group_rows(rows: Any, *, name_key: str, keys: frozenset[str]) -> dict[str, frozenset[str]]:
    table: dict[str, frozenset[str]] = {}
    for row in rows or []:
        _check_row(row, keys)
        name = str(row.get(name_key) or "").strip()
        key = normalize(name)
        if not key:
            raise ValueError(f"food_groups.yaml 에 {name_key} 이름이 빈 항목이 있다")
        groups = row.get("groups")
        if not isinstance(groups, list) or not groups:
            raise ValueError(f"food_groups.yaml {name!r}: groups 가 비었다 — 비우려면 줄을 뺀다")
        if unknown := sorted(set(groups) - FOOD_GROUPS):
            raise ValueError(f"food_groups.yaml {name!r}: 정해지지 않은 식품군 {unknown}")
        if key in table:
            raise ValueError(f"food_groups.yaml {name!r} 이 두 번 적혀 있다")
        table[key] = frozenset(groups)
    return table
