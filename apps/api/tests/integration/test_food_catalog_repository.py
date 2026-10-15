"""카탈로그 저장소 — 배치가 덮는 범위 · 요청 중 저장 (food_agent_own_table §1 · §1-1)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.domains.food import repository
from app.domains.food.models import MenuCatalog, RecipeCatalog

NOW = datetime(2026, 10, 14, tzinfo=UTC)
LATER = datetime(2026, 10, 15, tzinfo=UTC)


def menu(key: str, **over) -> dict:
    return {
        "menu_key": key,
        "display_name": key,
        "source": "mfds_nutri",
        "food_code": "D1",
        "nutrients": {"energy": 1.0},
        "resolved": True,
        "synced_at": NOW,
    } | over


def recipe(source_id: str, **over) -> dict:
    return {
        "source": "mafra_recipe",
        "source_id": source_id,
        "menu_key": f"레시피{source_id}",
        "display_name": f"레시피{source_id}",
        "ingredients": ["두부"],
        "resolved": True,
        "synced_at": NOW,
    } | over


async def test_배치는_영양DB_행을_새_값으로_고친다(session):
    await repository.refresh_nutrition_menus(session, rows=[menu("된장찌개")])
    await repository.refresh_nutrition_menus(
        session, rows=[menu("된장찌개", food_code="D2", synced_at=LATER)]
    )

    [row] = await repository.get_menus(session, keys=["된장찌개"])
    assert (row.food_code, row.synced_at) == ("D2", LATER)


async def test_배치는_사람이_넣은_행을_덮지_않는다(session):
    await repository.insert_menu_if_absent(
        session,
        values=menu("된장찌개", source="manual", food_code=None, ingredients=["된장", "두부"]),
    )

    await repository.refresh_nutrition_menus(session, rows=[menu("된장찌개", food_code="D9")])

    [row] = await repository.get_menus(session, keys=["된장찌개"])
    assert (row.source, row.food_code, row.ingredients) == ("manual", None, ["된장", "두부"])


async def test_배치는_stage_min_을_건드리지_않는다(session):
    await repository.refresh_nutrition_menus(session, rows=[menu("미음")])
    await session.execute(
        MenuCatalog.__table__.update().values(stage_min="infant_weaning")
    )  # 사람이 태깅했다 (FT-1)

    await repository.refresh_nutrition_menus(session, rows=[menu("미음", synced_at=LATER)])

    [row] = await repository.get_menus(session, keys=["미음"])
    assert row.stage_min == "infant_weaning"


async def test_요청_중_저장은_있는_행을_덮지_않는다(session):
    await repository.insert_menu_if_absent(session, values=menu("된장찌개", food_code="D1"))
    await repository.insert_menu_if_absent(session, values=menu("된장찌개", food_code="D2"))

    [row] = await repository.get_menus(session, keys=["된장찌개"])
    assert row.food_code == "D1"


async def test_배치에_영양DB_아닌_행을_넘기면_거절한다(session):
    with pytest.raises(ValueError):
        await repository.refresh_nutrition_menus(session, rows=[menu("x", source="manual")])


async def test_영양값을_붙일_때는_영양DB_행만_읽는다(session):
    await repository.refresh_nutrition_menus(session, rows=[menu("된장찌개")])
    await repository.insert_menu_if_absent(session, values=menu("두부조림", source="manual"))

    found = await repository.list_nutrition_menus(session, keys=["된장찌개", "두부조림"])
    assert [row.menu_key for row in found] == ["된장찌개"]


async def test_레시피는_같은_번호면_새_값으로_덮는다(session):
    await repository.upsert_recipes(session, rows=[recipe("1")])
    await repository.upsert_recipes(session, rows=[recipe("1", resolved=False, synced_at=LATER)])

    rows = (await session.scalars(select(RecipeCatalog))).all()
    assert [(r.source_id, r.resolved) for r in rows] == [("1", False)]


async def test_레시피_stage_min_은_새_값이_비면_남긴다(session):
    await repository.upsert_recipes(session, rows=[recipe("1", stage_min="preschool")])
    await repository.upsert_recipes(session, rows=[recipe("1", stage_min=None)])

    [row] = (await session.scalars(select(RecipeCatalog))).all()
    assert row.stage_min == "preschool"


async def test_해석된_레시피만_읽는다(session):
    await repository.upsert_recipes(session, rows=[recipe("1"), recipe("2", resolved=False)])

    assert [r.source_id for r in await repository.list_resolved_recipes(session)] == ["1"]


async def test_많은_행도_한_번에_넣는다(session):
    # 품목대표 전체는 8,807행 — 묶음 크기를 넘어도 다 들어간다
    rows = [menu(f"메뉴{i}") for i in range(repository.CHUNK + 5)]
    await repository.refresh_nutrition_menus(session, rows=rows)

    assert len(await repository.get_menus(session, keys=[r["menu_key"] for r in rows])) == len(rows)


async def test_이번_받기에_없는_레시피만_후보에서_내린다(session):
    await repository.upsert_recipes(
        session, rows=[recipe("1"), recipe("2"), recipe("9", source="mfds_recipe")]
    )

    retired = await repository.retire_missing_recipes(
        session, source="mafra_recipe", keep_ids=["1"]
    )

    assert retired == 1
    resolved = {(r.source, r.source_id) for r in await repository.list_resolved_recipes(session)}
    assert resolved == {("mafra_recipe", "1"), ("mfds_recipe", "9")}  # 다른 소스는 건드리지 않는다


async def test_내린_레시피도_행은_남는다(session):
    await repository.upsert_recipes(session, rows=[recipe("1"), recipe("2")])

    await repository.retire_missing_recipes(session, source="mafra_recipe", keep_ids=["1"])

    rows = {r.source_id: r.resolved for r in (await session.scalars(select(RecipeCatalog))).all()}
    assert rows == {"1": True, "2": False}


async def test_보존할_번호가_비면_거절한다(session):
    # 빈 받기로 호출하면 후보 풀 전체가 내려간다
    with pytest.raises(ValueError):
        await repository.retire_missing_recipes(session, source="mafra_recipe", keep_ids=[])
