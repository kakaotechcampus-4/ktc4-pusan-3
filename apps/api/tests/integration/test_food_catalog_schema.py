"""Food 카탈로그 두 표 — DB 가 막는 것 (own_table §1 · §1-1)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.domains.food.models import MenuCatalog, RecipeCatalog

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def menu(**over) -> MenuCatalog:
    return MenuCatalog(
        **{
            "menu_key": "된장찌개",
            "display_name": "된장찌개",
            "source": "mfds_nutri",
            "synced_at": NOW,
        }
        | over
    )


def recipe(**over) -> RecipeCatalog:
    values = {
        "source": "mafra_recipe",
        "source_id": "1",
        "menu_key": "샤브샤브",
        "display_name": "샤브샤브",
        "synced_at": NOW,
    }
    return RecipeCatalog(**values | over)


async def test_기본값으로_넣을_수_있다(session):
    session.add_all([menu(), recipe()])
    await session.flush()


@pytest.mark.parametrize(
    "bad",
    [
        {"source": "mafra_recipe"},  # 레시피 행은 recipe_catalog 몫이다
        {"allergen_codes": [20]},  # 19종 밖
        {"allergen_codes": [0]},
        {"stage_min": "adult"},
        {"food_groups": ["snacks"]},
        {"serving_g": 0},
    ],
)
async def test_menu_catalog_이_막는_값(session, bad):
    session.add(menu(**bad))
    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.parametrize(
    "bad",
    [{"source": "mfds_nutri"}, {"allergen_codes": [20]}, {"stage_min": "adult"}],
)
async def test_recipe_catalog_이_막는_값(session, bad):
    session.add(recipe(**bad))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_이름이_같은_레시피도_따로_행을_둔다(session):
    # own_table §1-1 — 샤브샤브 등 4쌍은 재료가 다르다
    session.add_all([recipe(source_id="1"), recipe(source_id="2")])
    await session.flush()


async def test_같은_레시피_번호는_두_번_넣지_못한다(session):
    session.add_all([recipe(source_id="1"), recipe(source_id="1", display_name="다른 이름")])
    with pytest.raises(IntegrityError):
        await session.flush()
