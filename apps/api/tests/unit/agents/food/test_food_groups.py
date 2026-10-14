"""식품군 대응표 — 영양DB 식품 대분류 → 6개 식품군 (#396 T1).

대응표(`reference/food_groups.yaml`)는 코드가 대조한다. 이름이 `food_doc.yaml` 에 쓰인 식품군과
어긋나면 읽을 때 바로 실패하고, 표에 없는 대분류는 추정하지 않고 빈 집합이다.
영양DB 이름 · 값은 전부 가짜다(대분류 이름만 표에서 가져온다).
"""

from datetime import UTC, datetime

import pytest

from app.agents.food.doc_seed import food_doc_seed
from app.agents.food.food_groups import (
    FOOD_GROUPS,
    category_table,
    food_groups_of_category,
    parse_category_table,
)
from app.agents.food.menu_resolver import MenuResolver, NotFoundMemory, Resolved
from app.agents.food.store import InMemoryMenuCatalog, InMemoryMenuSource
from app.core.mfds_nutri_raw import CORE_NUTRIENT_KEYS, RawNutriRow

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def table_data(*rows: dict) -> dict:
    return {
        "source": "테스트",
        "version": "t",
        "fetched_at": "2026-10-14",
        "categories": list(rows),
    }


# ── 이름은 food_doc 시드와 같다 ───────────────────────────────────────
def test_식품군_여섯_이름은_food_doc_시드가_쓰는_이름과_같다() -> None:
    used = {group for entry in food_doc_seed() for group in entry.food_groups}

    assert used <= FOOD_GROUPS
    assert FOOD_GROUPS == {
        "grains",
        "meat_fish_egg_legumes",
        "vegetables",
        "fruits",
        "dairy",
        "fats_sugars",
    }


def test_대응표가_쓰는_식품군은_모두_여섯_이름_안이다() -> None:
    used = {group for groups in category_table().values() for group in groups}

    assert used
    assert used <= FOOD_GROUPS


def test_여섯_이름_밖_식품군을_적으면_읽을_때_실패한다() -> None:
    with pytest.raises(ValueError, match="vegetable"):
        parse_category_table(table_data({"category": "채소류", "groups": ["vegetable"]}))


def test_같은_대분류를_두_번_적으면_실패한다() -> None:
    row = {"category": "채소류", "groups": ["vegetables"]}

    with pytest.raises(ValueError, match="채소류"):
        parse_category_table(table_data(row, row))


def test_식품군이_빈_줄은_실패한다() -> None:
    # 비워 두려면 줄을 빼면 된다 — 빈 줄은 빈 칸을 채우다 만 것이다
    with pytest.raises(ValueError, match="채소류"):
        parse_category_table(table_data({"category": "채소류", "groups": []}))


def test_정해지지_않은_칸이_있으면_실패한다() -> None:
    with pytest.raises(ValueError, match="note2"):
        parse_category_table(
            table_data({"category": "채소류", "groups": ["vegetables"], "note2": "x"})
        )


# ── 대분류 → 식품군 ───────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("곡류 및 그 제품", {"grains"}),
        ("우유 및 유제품류", {"dairy"}),
        ("채소류", {"vegetables"}),
        ("과일류", {"fruits"}),
        ("육류", {"meat_fish_egg_legumes"}),
    ],
)
def test_대분류가_기대_식품군으로_간다(category: str, expected: set[str]) -> None:
    assert food_groups_of_category(category) == frozenset(expected)


def test_띄어쓰기가_달라도_같은_대분류다() -> None:
    assert food_groups_of_category("곡류및그제품") == food_groups_of_category("곡류 및 그 제품")


@pytest.mark.parametrize("category", ["표에없는분류", "", "   ", None])
def test_표에_없는_대분류는_추정하지_않고_빈_집합이다(category: str | None) -> None:
    assert food_groups_of_category(category) == frozenset()


# ── 영양DB 행 → 카탈로그 행 ───────────────────────────────────────────
def raw_row(name: str, *, category: str | None) -> RawNutriRow:
    return RawNutriRow(
        food_code="F1",
        name=name,
        db_group="원재료성",
        db_class="품목대표",
        category=category,
        researched_on="20200101",
        food_weight="250g",
        nutrients={key: "10" for key in CORE_NUTRIENT_KEYS},
    )


async def resolve_one(name: str, *, category: str | None) -> Resolved:
    resolver = MenuResolver(
        catalog=InMemoryMenuCatalog(),
        source=InMemoryMenuSource({name: [raw_row(name, category=category)]}),
        now=NOW,
        not_found=NotFoundMemory(),
    )
    resolution = await resolver.resolve(name)
    assert isinstance(resolution, Resolved)
    return resolution


async def test_영양DB로_해석한_행에_대응표대로_식품군이_채워진다() -> None:
    resolved = await resolve_one("가짜우유", category="우유 및 유제품류")

    assert resolved.row.food_groups == frozenset({"dairy"})


async def test_대분류가_표에_없거나_비면_식품군은_비어_있다() -> None:
    unknown = await resolve_one("가짜음식", category="표에없는분류")
    missing = await resolve_one("가짜원재료", category=None)

    assert unknown.row.food_groups == frozenset()
    assert missing.row.food_groups == frozenset()
