"""적재 스크립트 — 받기를 다 끝낸 뒤 한 트랜잭션으로 쓴다 (FT-19 · own_table §1-1)."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.agents.food.recipe_catalog import parse_ingredient_table
from app.core.mafra_recipe_raw import RawIngredient, RawRecipe, RecipeSync
from app.core.mfds_nutri_raw import CORE_NUTRIENT_KEYS, RawNutriRow
from app.domains.food import repository
from app.domains.food.models import MenuCatalog, RecipeCatalog
from scripts.load_food_catalog import Loaded, run

NOW = datetime(2026, 10, 14, tzinfo=UTC)


def raw(name: str, code: str = "D1") -> RawNutriRow:
    return RawNutriRow(
        food_code=code,
        name=name,
        db_group="음식",
        db_class="품목대표",
        food_weight="300g",
        nutrients={key: "1.5" for key in CORE_NUTRIENT_KEYS},
    )


def table(status: str = "ok"):
    return parse_ingredient_table(
        {
            "source": "mafra_recipe",
            "version": "test",
            "fetched_at": "2026-10-14",
            "ingredients": [
                {"raw": "두부", "names": ["두부"], "codes": [5], "status": status},
                {"raw": "된장", "names": ["된장"], "codes": [5], "status": "ok"},
            ],
        }
    )


SYNC = RecipeSync(
    recipes=(
        RawRecipe("1", "된장찌개", ingredients=(RawIngredient("두부"), RawIngredient("된장"))),
        RawRecipe(
            "2", "두부부침", ingredients=(RawIngredient("두부"), RawIngredient("모르는재료"))
        ),
    ),
    recipe_rows=2,
    ingredient_rows=4,
    orphan_ingredient_rows=0,
    unnamed_ingredient_rows=0,
)


@pytest.fixture
def opener(session):
    @asynccontextmanager
    async def same():
        yield session

    return same


async def _rows():
    return [raw("된장찌개"), raw("미역국", "D2")]


async def _sync():
    return SYNC


async def _count(session, model) -> int:
    return await session.scalar(select(func.count()).select_from(model))


async def test_영양DB_를_먼저_싣고_레시피에_영양값을_붙인다(session, opener):
    loaded = await run(
        nutrition=True,
        recipes=True,
        fetch_nutrition=_rows,
        fetch_recipes=_sync,
        open_session=opener,
        table=table,
        now=NOW,
    )

    assert loaded == Loaded(
        nutrition_rows=2, recipe_rows=2, resolved_recipes=1, nutrition_received=2
    )
    recipes = {r.source_id: r for r in (await session.scalars(select(RecipeCatalog))).all()}
    assert recipes["1"].food_code == "D1" and recipes["1"].resolved
    assert recipes["2"].resolved is False  # 정리 표에 없는 재료


async def test_받다가_실패하면_아무것도_쓰지_않는다(session, opener):
    async def broken():
        raise RuntimeError("mafra 장애")

    with pytest.raises(RuntimeError):
        await run(
            nutrition=True,
            recipes=True,
            fetch_nutrition=_rows,
            fetch_recipes=broken,
            open_session=opener,
            table=table,
            now=NOW,
        )

    assert await _count(session, MenuCatalog) == 0


async def test_행_만들기에서_터지면_영양DB_도_되돌린다(session, opener):
    bad = RecipeSync(
        recipes=(
            RawRecipe("9", "()", ingredients=(RawIngredient("두부"),)),
        ),  # 정규화하면 이름이 빈다
        recipe_rows=1,
        ingredient_rows=1,
        orphan_ingredient_rows=0,
        unnamed_ingredient_rows=0,
    )

    async def bad_sync():
        return bad

    with pytest.raises(ValueError):
        await run(
            nutrition=True,
            recipes=True,
            fetch_nutrition=_rows,
            fetch_recipes=bad_sync,
            open_session=opener,
            table=table,
            now=NOW,
        )

    assert await _count(session, MenuCatalog) == 0


async def test_정리_표가_되돌려지면_다시_적재할_때_resolved_가_풀린다(session, opener):
    kwargs = dict(
        nutrition=False,
        recipes=True,
        fetch_nutrition=_rows,
        fetch_recipes=_sync,
        open_session=opener,
        now=NOW,
    )
    await run(table=table, **kwargs)
    await run(table=lambda: table(status="draft"), **kwargs)

    assert await repository.list_resolved_recipes(session) == []


async def test_dry_run_은_세기만_하고_쓰지_않는다(session, opener):
    loaded = await run(
        nutrition=True,
        recipes=False,
        fetch_nutrition=_rows,
        fetch_recipes=_sync,
        open_session=opener,
        table=table,
        now=NOW,
        dry_run=True,
    )

    assert loaded.nutrition_rows == 2
    assert await _count(session, MenuCatalog) == 0


def _syncing(*recipes: RawRecipe):
    async def fetch() -> RecipeSync:
        return RecipeSync(
            recipes=recipes,
            recipe_rows=len(recipes),
            ingredient_rows=0,
            orphan_ingredient_rows=0,
            unnamed_ingredient_rows=0,
        )

    return fetch


def _soup(recipe_id: str, name: str) -> RawRecipe:
    return RawRecipe(recipe_id, name, ingredients=(RawIngredient("두부"), RawIngredient("된장")))


async def test_소스에서_사라진_레시피는_후보에서_내려간다(session, opener):
    """MAFRA 가 내린 레시피에 옛 재료 판정이 남아 후보 풀에 계속 나가면 안 된다."""
    kwargs = dict(nutrition=False, recipes=True, fetch_nutrition=_rows, open_session=opener)
    kwargs |= dict(table=table, now=NOW)
    both = await run(
        fetch_recipes=_syncing(_soup("1", "된장찌개"), _soup("3", "두부된장국")), **kwargs
    )
    assert both.resolved_recipes == 2

    later = await run(fetch_recipes=_syncing(_soup("1", "된장찌개")), **kwargs)

    assert later.retired_recipes == 1
    assert [r.source_id for r in await repository.list_resolved_recipes(session)] == ["1"]
    kept = await session.scalar(
        select(RecipeCatalog.resolved).where(RecipeCatalog.source_id == "3")
    )
    assert kept is False  # 지우지 않고 내린다


async def test_영양DB_가_한_건도_안_오면_적재하지_않는다(session, opener):
    async def empty():
        return []

    await run(
        nutrition=True,
        recipes=False,
        fetch_nutrition=_rows,
        fetch_recipes=_sync,
        open_session=opener,
        table=table,
        now=NOW,
    )

    with pytest.raises(RuntimeError):
        await run(
            nutrition=True,
            recipes=False,
            fetch_nutrition=empty,
            fetch_recipes=_sync,
            open_session=opener,
            table=table,
            now=NOW,
        )

    assert await _count(session, MenuCatalog) == 2  # 이전 적재본 그대로


async def test_레시피가_한_건도_안_오면_후보_풀을_비우지_않는다(session, opener):
    kwargs = dict(nutrition=False, recipes=True, fetch_nutrition=_rows, open_session=opener)
    kwargs |= dict(table=table, now=NOW)
    await run(fetch_recipes=_syncing(_soup("1", "된장찌개")), **kwargs)

    with pytest.raises(RuntimeError):
        await run(fetch_recipes=_syncing(), **kwargs)

    assert [r.source_id for r in await repository.list_resolved_recipes(session)] == ["1"]


async def test_받은_영양DB_행과_적재한_메뉴_수를_따로_센다(session, opener):
    async def fetch():
        return [
            raw("된장찌개"),
            raw("된장찌개", "D9"),  # 같은 이름 — 한 메뉴로 모인다
            RawNutriRow(  # 핵심 영양 칸이 비어 메뉴가 되지 못한다
                food_code="D3",
                name="빈영양국",
                db_group="음식",
                db_class="품목대표",
                food_weight="300g",
                nutrients={},
            ),
        ]

    loaded = await run(
        nutrition=True,
        recipes=False,
        fetch_nutrition=fetch,
        fetch_recipes=_sync,
        open_session=opener,
        table=table,
        now=NOW,
    )

    assert (loaded.nutrition_received, loaded.nutrition_rows) == (3, 1)
