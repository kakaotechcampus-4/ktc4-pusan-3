"""Food 카탈로그 어댑터를 실제 DB 로 — 알레르기 칸이 오가며 바뀌지 않는다."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.agents.food.store.ports import MenuCatalogRow
from app.domains.food.agent_reader import DbMenuCatalog, DbRecipeCatalog, menu_values

NOW = datetime(2026, 10, 14, tzinfo=UTC)


@pytest.fixture
def opener(session):
    @asynccontextmanager
    async def same():
        yield session

    return same


def recipe_row(source_id: str = "7", **over) -> MenuCatalogRow:
    values = {
        "menu_key": "두부달걀부침",
        "display_name": "두부달걀부침",
        "source": "mafra_recipe",
        "resolved": True,
        "synced_at": NOW,
        "food_code": "D7",
        "serving_g": 300.0,
        "nutrients": {"energy": 120.5, "protein": 8.0},
        "ingredients": ("두부", "달걀", "식용유"),
        "main_ingredients": ("두부", "달걀"),
        "allergen_codes": frozenset({1, 5}),
        "food_groups": frozenset({"meat_fish_egg_legumes", "fats_sugars"}),
        "stage_min": "toddler",
        "source_version": "mafra_recipe",
        "source_id": source_id,
    }
    return MenuCatalogRow(**values | over)


async def test_레시피_행은_DB_를_오가도_칸_하나_바뀌지_않는다(opener):
    store = DbRecipeCatalog(opener)
    original = recipe_row()

    await store.put(original)

    assert await store.all_resolved() == [original]


async def test_해석_안_된_레시피는_후보로_읽지_않는다(opener):
    store = DbRecipeCatalog(opener)
    await store.put(recipe_row("1"))
    await store.put(recipe_row("2", resolved=False))

    assert [row.source_id for row in await store.all_resolved()] == ["1"]


async def test_메뉴_행도_오가며_바뀌지_않는다(opener):
    store = DbMenuCatalog(opener)
    original = MenuCatalogRow(
        menu_key="된장찌개",
        display_name="된장찌개",
        source="mfds_nutri",
        resolved=True,
        synced_at=NOW,
        food_code="D101",
        serving_g=300.0,
        nutrients={"energy": 45.95},
        allergen_codes=frozenset({5, 6}),
        food_groups=frozenset({"meat_fish_egg_legumes"}),
        source_version="mfds_nutri_db",
    )

    await store.put(original)

    assert await store.get("된장찌개") == original
    assert await store.get_many(("된장찌개", "없는메뉴")) == {"된장찌개": original}
    assert await store.get_many(()) == {}


async def test_메뉴_put_은_있는_행을_덮지_않는다(opener):
    store = DbMenuCatalog(opener)
    first = MenuCatalogRow(
        menu_key="된장찌개", display_name="된장찌개", source="manual", resolved=True, synced_at=NOW
    )
    await store.put(first)
    await store.put(replace(first, source="mfds_nutri", food_code="X"))

    assert await store.get("된장찌개") == first


def test_레시피_행은_메뉴_카탈로그_값으로_만들지_않는다():
    # DB 의 CHECK 앞에서 코드가 먼저 막는다 (InMemoryMenuCatalog._not_recipe 와 같은 규칙)
    with pytest.raises(ValueError):
        menu_values(recipe_row())


async def test_읽기_실패는_빈_목록이_아니라_예외다():
    @asynccontextmanager
    async def broken():
        raise ConnectionError("db")
        yield

    with pytest.raises(ConnectionError):
        await DbRecipeCatalog(broken).all_resolved()
