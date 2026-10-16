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
    food_group_table,
    food_groups_of_category,
    food_groups_of_menu,
    parse_category_table,
    parse_food_group_table,
)
from app.agents.food.menu_resolver import MenuResolver, NotFoundMemory, Resolved
from app.agents.food.store import InMemoryMenuCatalog, InMemoryMenuSource
from app.core.mfds_nutri_raw import CORE_NUTRIENT_KEYS, RawNutriRow
from app.core.nutrition_pick import NutritionFacts
from app.rules.term_match import normalize

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def table_data(
    *rows: dict, codes: list[dict] | None = None, menus: list[dict] | None = None
) -> dict:
    data: dict = {
        "source": "테스트",
        "version": "t",
        "fetched_at": "2026-10-14",
        "categories": list(rows),
    }
    if codes is not None:
        data["codes"] = codes
    if menus is not None:
        data["menus"] = menus
    return data


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
        ("곡류", {"grains"}),
        ("어패류 및 기타 수산물", {"meat_fish_egg_legumes"}),
        ("우유 및 유제품류", {"dairy"}),
        ("채소류", {"vegetables"}),
        ("과일류", {"fruits"}),
        ("육류", {"meat_fish_egg_legumes"}),
        ("나물·숙채류", {"vegetables"}),
        ("김치류", {"vegetables"}),
    ],
)
def test_대분류가_기대_식품군으로_간다(category: str, expected: set[str]) -> None:
    assert food_groups_of_category(category) == frozenset(expected)


def test_띄어쓰기가_달라도_같은_대분류다() -> None:
    assert food_groups_of_category("우유및유제품류") == food_groups_of_category("우유 및 유제품류")


@pytest.mark.parametrize("category", ["표에없는분류", "", "   ", None])
def test_표에_없는_대분류는_추정하지_않고_빈_집합이다(category: str | None) -> None:
    assert food_groups_of_category(category) == frozenset()


# ── 코드 줄 · 메뉴 이름 줄 (#459 · #420) ──────────────────────────────
def facts(
    *, category: str = "", db_group: str = "원재료성", code: str | None = None
) -> NutritionFacts:
    return NutritionFacts(
        food_code="F1",
        name="가짜",
        category=category,
        serving_g=None,
        per_100g={},
        source_version="t",
        db_group=db_group,
        category_code=code,
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("09", {"meat_fish_egg_legumes"}),  # 닭고기 · 쇠고기
        ("10", {"meat_fish_egg_legumes"}),  # 달걀
        ("13", {"dairy"}),  # 우유
    ],
)
def test_원재료성_행은_대분류_이름이_비면_코드로_찾는다(code: str, expected: set[str]) -> None:
    assert food_groups_of_menu("가짜", facts(code=code)) == frozenset(expected)


@pytest.mark.parametrize("code", ["12", "21", "99", None])
def test_표에_없는_코드는_빈_집합이다(code: str | None) -> None:
    # 12 해조류 · 21 은 일부러 줄을 두지 않았다(#459)
    assert food_groups_of_menu("가짜", facts(code=code)) == frozenset()


def test_코드는_원재료성_행에만_쓴다() -> None:
    # 코드 뜻은 DB 그룹마다 다르다 — 음식 13 은 나물·숙채류, 가공식품 09 는 음료류다
    assert food_groups_of_menu("가짜", facts(db_group="음식", code="13")) == frozenset()
    assert food_groups_of_menu("가짜", facts(db_group="가공식품", code="09")) == frozenset()


def test_대분류_이름이_있으면_코드로_다시_찾지_않는다() -> None:
    # 이름이 있는데 표에 없으면(가공식품류 · 버섯류) 일부러 뺀 대분류다
    assert food_groups_of_menu("가짜", facts(category="가공식품류", code="09")) == frozenset()


def test_메뉴_이름_줄이_대분류보다_먼저다() -> None:
    # 쑥갓나물_두부 는 나물·숙채류(채소)지만 두부가 들었다
    dish = facts(category="나물·숙채류", db_group="음식")

    assert food_groups_of_menu(normalize("쑥갓나물_두부"), dish) == frozenset(
        {"vegetables", "meat_fish_egg_legumes"}
    )
    assert food_groups_of_menu(normalize("쑥갓나물"), dish) == frozenset({"vegetables"})


def test_밥류는_곡류만_든_메뉴만_이름_줄로_곡류다() -> None:
    # 밥류 대분류에는 비빔밥 · 덮밥이 섞여 줄이 없다 — 곡류만 적으면 채소 0일이 된다
    rice = facts(category="밥류", db_group="음식")

    assert food_groups_of_menu(normalize("쌀밥"), rice) == frozenset({"grains"})
    assert food_groups_of_menu(normalize("비빔밥"), rice) == frozenset()


def test_커밋된_대응표의_코드_줄은_원재료성_실제_코드다() -> None:
    assert dict(food_group_table().codes) == {
        "09": normalize("육류"),
        "10": normalize("난류"),
        "13": normalize("우유 및 유제품류"),
    }


def test_코드를_따옴표_없이_적으면_읽을_때_실패한다() -> None:
    # YAML 은 09 를 숫자 9 로 읽는다 — 그대로 두면 "09" 와 안 맞아 조용히 빈다
    data = table_data(
        {"category": "육류", "groups": ["meat_fish_egg_legumes"]},
        codes=[{"code": 9, "category": "육류"}],
    )

    with pytest.raises(ValueError, match="두 자리"):
        parse_food_group_table(data)


def test_코드가_가리키는_대분류가_표에_없으면_실패한다() -> None:
    with pytest.raises(ValueError, match="해조류"):
        parse_food_group_table(table_data(codes=[{"code": "12", "category": "해조류"}]))


def test_같은_코드를_두_번_적으면_실패한다() -> None:
    row = {"code": "09", "category": "육류"}
    data = table_data({"category": "육류", "groups": ["meat_fish_egg_legumes"]}, codes=[row, row])

    with pytest.raises(ValueError, match="09"):
        parse_food_group_table(data)


def test_같은_메뉴를_두_번_적으면_실패한다() -> None:
    row = {"menu": "쌀밥", "groups": ["grains"]}

    with pytest.raises(ValueError, match="쌀밥"):
        parse_food_group_table(table_data(menus=[row, row]))


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
