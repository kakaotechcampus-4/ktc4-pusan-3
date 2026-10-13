"""식품 대분류 → 6개 식품군. `reference/food_groups.yaml` 을 읽어 코드가 대조한다.

영양DB 행(`NutritionFacts.category`)의 식품 대분류를 식품군으로 옮긴다. 모델이 고르지 않고,
표에 없는 대분류는 빈 집합이다 — 이름으로 식품군을 추정하지 않는다. 식품군 이름은 `food_doc.yaml`
이 쓰는 여섯 개(`doc_seed.FOOD_GROUPS`)와 같아야 하고, 어긋나면 읽을 때 바로 실패한다.

레시피 행의 식품군은 이 표가 아니라 재료 정리 표(`recipe_ingredients.yaml`)의 재료별 칸에서 모은다
(`recipe_catalog.py`).
"""

from collections.abc import Mapping
from functools import cache
from typing import Any

from app.agents.common.reference import load_reference
from app.agents.food.doc_seed import FOOD_GROUPS
from app.rules.term_match import normalize

_TOP_KEYS = frozenset({"source", "version", "fetched_at", "categories"})
_ROW_KEYS = frozenset({"category", "groups"})


def parse_category_table(data: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """대응표를 검사해 정규화한 대분류 이름별로 돌려준다. 모양이 틀리면 `ValueError`."""
    if extra := set(data) - _TOP_KEYS:
        raise ValueError(f"food_groups.yaml 에 정해지지 않은 칸이 있다: {sorted(extra)}")
    table: dict[str, frozenset[str]] = {}
    for row in data.get("categories") or []:
        if not isinstance(row, Mapping):
            raise ValueError("food_groups.yaml 의 항목은 객체여야 한다")
        if extra := set(row) - _ROW_KEYS:
            raise ValueError(f"food_groups.yaml 항목에 정해지지 않은 칸: {sorted(extra)}")
        name = str(row.get("category") or "").strip()
        key = normalize(name)
        if not key:
            raise ValueError("food_groups.yaml 에 대분류 이름이 빈 항목이 있다")
        groups = row.get("groups")
        if not isinstance(groups, list) or not groups:
            raise ValueError(f"food_groups.yaml {name!r}: groups 가 비었다 — 비우려면 줄을 뺀다")
        if unknown := sorted(set(groups) - FOOD_GROUPS):
            raise ValueError(f"food_groups.yaml {name!r}: 정해지지 않은 식품군 {unknown}")
        if key in table:
            raise ValueError(f"food_groups.yaml {name!r} 이 두 번 적혀 있다")
        table[key] = frozenset(groups)
    return table


@cache
def category_table() -> dict[str, frozenset[str]]:
    """`reference/food_groups.yaml`. 정규화한 대분류 이름 → 식품군."""
    return parse_category_table(load_reference("food_groups.yaml"))


def food_groups_of_category(category: str | None) -> frozenset[str]:
    """식품 대분류의 식품군. 비었거나 표에 없으면 빈 집합 — 추정하지 않는다."""
    key = normalize(category or "")
    return category_table().get(key, frozenset()) if key else frozenset()
