"""적재 스크립트 — 받기를 다 끝낸 뒤 한 트랜잭션으로 쓴다 (FT-19 · own_table §1-1)."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.agents.food.recipe_catalog import parse_ingredient_table
from app.core.config import settings
from app.core.mafra_recipe_raw import RawIngredient, RawRecipe, RecipeSync
from app.core.mfds_nutri_raw import CORE_NUTRIENT_KEYS, RawNutriRow
from app.domains.food import repository
from app.domains.food.models import MenuCatalog, RecipeCatalog
from scripts import load_food_catalog, recipe_fetch
from scripts.load_food_catalog import FetchedRecipes, RecipeBatch, SourceLoaded, run

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


def fetched(
    sync: RecipeSync, *, source: str = "mafra_recipe", skipped: tuple[str, ...] = ()
) -> FetchedRecipes:
    """한 소스를 받은 결과. 건너뛴 소스가 있으면 `skipped` 로 넘긴다."""
    return FetchedRecipes(batches=(RecipeBatch(source, sync),), skipped=skipped)  # type: ignore[arg-type]


@pytest.fixture
def opener(session):
    @asynccontextmanager
    async def same():
        yield session

    return same


async def _rows():
    return [raw("된장찌개"), raw("미역국", "D2")]


async def _sync():
    return fetched(SYNC)


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

    assert (loaded.nutrition_rows, loaded.nutrition_received) == (2, 2)
    assert (loaded.recipe_rows, loaded.resolved_recipes, loaded.retired_recipes) == (2, 1, 0)
    assert loaded.sources == (
        SourceLoaded("mafra_recipe", rows=2, resolved=1, retired=0, without_main=2),
    )
    assert loaded.skipped == ()
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
        return fetched(bad)

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


def sync_of(*recipes: RawRecipe) -> RecipeSync:
    return RecipeSync(
        recipes=recipes,
        recipe_rows=len(recipes),
        ingredient_rows=0,
        orphan_ingredient_rows=0,
        unnamed_ingredient_rows=0,
    )


def _syncing(*recipes: RawRecipe):
    async def fetch() -> FetchedRecipes:
        return fetched(sync_of(*recipes))

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


def grouped_table():
    return parse_ingredient_table(
        {
            "source": "mafra_recipe",
            "version": "test",
            "fetched_at": "2026-10-15",
            "ingredients": [
                {
                    "raw": "두부",
                    "names": ["두부"],
                    "codes": [5],
                    "status": "ok",
                    "food_group": "meat_fish_egg_legumes",
                },
                {
                    "raw": "애호박",
                    "names": ["애호박"],
                    "codes": [],
                    "status": "ok",
                    "food_group": "vegetables",
                },
                {"raw": "된장", "names": ["된장"], "codes": [5], "status": "ok"},
            ],
        }
    )


GROUPED_SYNC = RecipeSync(
    recipes=(
        RawRecipe(
            "1",
            "된장찌개",
            ingredients=(
                RawIngredient("두부", role="주재료"),
                RawIngredient("애호박", role="주재료"),
                RawIngredient("된장", role="양념"),
            ),
        ),
    ),
    recipe_rows=1,
    ingredient_rows=3,
    orphan_ingredient_rows=0,
    unnamed_ingredient_rows=0,
)


async def _grouped_sync():
    return fetched(GROUPED_SYNC)


async def _never():
    raise AssertionError("--nutrition 만 돌릴 때는 레시피를 받지 않는다")


async def _menu_groups(session) -> dict[str, list[str]]:
    rows = (await session.scalars(select(MenuCatalog))).all()
    return {row.menu_key: sorted(row.food_groups) for row in rows}


async def test_대응표에_없는_메뉴는_이름이_같은_레시피의_식품군을_빌린다(session, opener):
    loaded = await run(
        nutrition=True,
        recipes=True,
        fetch_nutrition=_rows,
        fetch_recipes=_grouped_sync,
        open_session=opener,
        table=grouped_table,
        now=NOW,
    )

    groups = await _menu_groups(session)
    assert groups["된장찌개"] == ["meat_fish_egg_legumes", "vegetables"]
    assert groups["미역국"] == []  # 이름이 같은 레시피가 없다 — 추정하지 않는다
    assert loaded.grouped_menus == 1


async def test_영양DB_만_다시_받아도_적재된_레시피에서_빌린다(session, opener):
    # 배치가 영양DB 행을 새 값으로 덮는다 — 레시피를 안 받았다고 빌린 식품군이 지워지면 안 된다
    common = dict(fetch_nutrition=_rows, open_session=opener, table=grouped_table, now=NOW)
    await run(nutrition=True, recipes=True, fetch_recipes=_grouped_sync, **common)

    loaded = await run(nutrition=True, recipes=False, fetch_recipes=_never, **common)

    assert (await _menu_groups(session))["된장찌개"] == ["meat_fish_egg_legumes", "vegetables"]
    assert loaded.grouped_menus == 1


# ── 여러 레시피 소스를 한 트랜잭션으로 (#510) ─────────────────────────────
def _fetching(*batches: RecipeBatch, skipped: tuple[str, ...] = ()):
    async def fetch() -> FetchedRecipes:
        return FetchedRecipes(batches=batches, skipped=skipped)  # type: ignore[arg-type]

    return fetch


def _mafra(*recipes: RawRecipe) -> RecipeBatch:
    return RecipeBatch("mafra_recipe", sync_of(*recipes))


def _nics(*recipes: RawRecipe) -> RecipeBatch:
    return RecipeBatch("nics_recipe", sync_of(*recipes))


def _unknown(recipe_id: str, name: str) -> RawRecipe:
    return RawRecipe(recipe_id, name, ingredients=(RawIngredient("모르는재료"),))


async def _recipes(session, source: str) -> dict[str, bool]:
    stmt = select(RecipeCatalog.source_id, RecipeCatalog.resolved).where(
        RecipeCatalog.source == source
    )
    return {source_id: resolved for source_id, resolved in (await session.execute(stmt)).all()}


def _recipes_only(opener, fetch, **over):
    kwargs = dict(
        nutrition=False,
        recipes=True,
        fetch_nutrition=_rows,
        fetch_recipes=fetch,
        open_session=opener,
        table=table,
        now=NOW,
    )
    return run(**kwargs | over)


async def test_두_소스가_함께_적재되고_번호가_같아도_행이_따로다(session, opener):
    loaded = await _recipes_only(
        opener,
        _fetching(
            _mafra(_soup("1", "된장찌개"), _soup("2", "두부국")),
            _nics(_soup("1", "열무김치"), _unknown("2", "미지의국")),
        ),
    )

    assert await _recipes(session, "mafra_recipe") == {"1": True, "2": True}
    assert await _recipes(session, "nics_recipe") == {"1": True, "2": False}
    assert loaded.sources == (
        SourceLoaded("mafra_recipe", rows=2, resolved=2, retired=0, without_main=2),
        SourceLoaded("nics_recipe", rows=2, resolved=1, retired=0, without_main=2),
    )
    assert (loaded.recipe_rows, loaded.resolved_recipes, loaded.retired_recipes) == (4, 3, 0)
    versions = {
        (r.source, r.source_version) for r in (await session.scalars(select(RecipeCatalog))).all()
    }
    assert versions == {("mafra_recipe", "mafra_recipe"), ("nics_recipe", "nics_recipe")}


async def test_한_소스가_0건이면_다른_소스도_쓰지_않는다(session, opener):
    with pytest.raises(RuntimeError):
        await _recipes_only(
            opener,
            _fetching(_mafra(_soup("1", "된장찌개")), _nics()),
            nutrition=True,
        )

    assert await _count(session, RecipeCatalog) == 0
    assert await _count(session, MenuCatalog) == 0


async def test_소스가_하나도_없으면_적재하지_않는다(session, opener):
    with pytest.raises(RuntimeError):
        await _recipes_only(opener, _fetching())

    assert await _count(session, RecipeCatalog) == 0


async def test_같은_소스를_두_번_넘기면_거절한다(session, opener):
    # 두 번째 묶음의 번호만 남기고 첫 번째를 내리는 사고를 막는다
    with pytest.raises(ValueError):
        await _recipes_only(
            opener, _fetching(_mafra(_soup("1", "된장찌개")), _mafra(_soup("2", "두부국")))
        )

    assert await _count(session, RecipeCatalog) == 0


async def test_두_번째_소스를_받다_실패하면_첫_소스도_쓰지_않는다(session, opener, monkeypatch):
    async def mafra():
        return sync_of(_soup("1", "된장찌개"))

    async def nics():
        raise RuntimeError("nics 장애")

    monkeypatch.setattr(recipe_fetch, "fetch_mafra", mafra)
    monkeypatch.setattr(recipe_fetch, "fetch_nics", nics)
    monkeypatch.setattr(settings, "NICS_RECIPE_API_KEY", "fake")

    with pytest.raises(RuntimeError, match="nics"):
        await _recipes_only(opener, load_food_catalog._fetch_recipes, nutrition=True)

    assert await _count(session, RecipeCatalog) == 0
    assert await _count(session, MenuCatalog) == 0


async def test_두_번째_소스를_쓰다_실패하면_첫_소스도_되돌린다(session, opener, monkeypatch):
    real = repository.upsert_recipes
    calls = 0

    async def flaky(sess, *, rows):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("DB 장애")
        return await real(sess, rows=rows)

    monkeypatch.setattr(repository, "upsert_recipes", flaky)

    with pytest.raises(RuntimeError, match="DB"):
        await _recipes_only(
            opener,
            _fetching(_mafra(_soup("1", "된장찌개")), _nics(_soup("1", "열무김치"))),
            nutrition=True,
        )

    assert calls == 2  # 첫 소스는 실제로 썼다가 되돌려진 것이다
    assert await _count(session, RecipeCatalog) == 0
    assert await _count(session, MenuCatalog) == 0


async def test_키가_없는데_그_소스_레시피가_후보_풀에_있으면_적재하지_않는다(session, opener):
    # 건너뛰면 그 소스의 행은 다시 만들지 못한다 — 정리 표에서 알레르기 코드를 고쳐도 옛 판정으로
    # 후보 풀에 남는다. 그래서 키 없이 건너뛰는 것은 그 소스 행이 후보 풀에 없을 때만이다
    both = _fetching(_mafra(_soup("1", "된장찌개")), _nics(_soup("1", "열무김치")))
    await _recipes_only(opener, both)

    with pytest.raises(RuntimeError, match="nics_recipe"):
        await _recipes_only(
            opener,
            _fetching(
                _mafra(_soup("1", "된장찌개"), _soup("2", "두부국")), skipped=("nics_recipe",)
            ),
            nutrition=True,
        )

    assert await _recipes(session, "mafra_recipe") == {"1": True}  # 새 레시피도 쓰지 않았다
    assert await _recipes(session, "nics_recipe") == {"1": True}
    assert await _count(session, MenuCatalog) == 0


async def test_그_소스_레시피가_후보_풀에_없으면_키_없이_건너뛴다(session, opener):
    # NICS 를 한 번도 싣지 않은 서버는 키 없이도 농식품정보원 · 영양DB 를 그대로 싣는다
    loaded = await _recipes_only(
        opener, _fetching(_mafra(_soup("1", "된장찌개")), skipped=("nics_recipe",))
    )

    assert loaded.skipped == ("nics_recipe",)
    assert [s.source for s in loaded.sources] == ["mafra_recipe"]
    assert await _recipes(session, "mafra_recipe") == {"1": True}


async def test_레시피_행은_한_번만_만든다(session, opener, monkeypatch):
    # 식품군 빌리기와 적재가 같은 행을 쓴다. 영양값은 영양DB 를 쓴 뒤에 붙인다
    real = load_food_catalog.recipe_catalog_rows
    calls: list[str] = []

    def counting(sync, table, *, now, source, nutrition):
        calls.append(source)
        return real(sync, table, now=now, source=source, nutrition=nutrition)

    monkeypatch.setattr(load_food_catalog, "recipe_catalog_rows", counting)

    await _recipes_only(
        opener,
        _fetching(_mafra(_soup("1", "된장찌개")), _nics(_soup("1", "열무김치"))),
        nutrition=True,
    )

    assert calls == ["mafra_recipe", "nics_recipe"]
    stew = await session.scalar(
        select(RecipeCatalog).where(
            RecipeCatalog.source == "mafra_recipe", RecipeCatalog.source_id == "1"
        )
    )
    assert stew.food_code == "D1"  # 영양값은 나중에 붙어도 실린다


async def test_한_소스의_누락_행이_다른_소스_행을_내리지_않는다(session, opener):
    await _recipes_only(
        opener,
        _fetching(
            _mafra(_soup("1", "된장찌개"), _soup("3", "두부된장국")),
            _nics(_soup("1", "열무김치"), _soup("3", "오이소박이")),
        ),
    )

    loaded = await _recipes_only(
        opener,
        _fetching(
            _mafra(_soup("1", "된장찌개")),  # 농식품정보원이 3 번을 내렸다
            _nics(_soup("1", "열무김치"), _soup("3", "오이소박이")),
        ),
    )

    assert {s.source: s.retired for s in loaded.sources} == {"mafra_recipe": 1, "nics_recipe": 0}
    assert await _recipes(session, "mafra_recipe") == {"1": True, "3": False}
    assert await _recipes(session, "nics_recipe") == {"1": True, "3": True}


async def test_NICS_레시피의_식품군도_빌리고_주재료_구간이_없으면_빌려_주지_않는다(session, opener):
    stew = RawRecipe(
        "5",
        "된장찌개",
        ingredients=(
            RawIngredient("두부", role="주재료"),
            RawIngredient("애호박", role="주재료"),
            RawIngredient("된장", role="양념"),
        ),
    )
    lumped = RawRecipe("6", "미역국", ingredients=(RawIngredient("두부", role="재료"),))

    loaded = await _recipes_only(
        opener,
        _fetching(_nics(stew, lumped)),
        nutrition=True,
        table=grouped_table,
    )

    groups = await _menu_groups(session)
    assert groups["된장찌개"] == ["meat_fish_egg_legumes", "vegetables"]
    assert groups["미역국"] == []  # `▶재료` 하나로 뭉친 레시피는 식품군이 비어 있다
    assert loaded.sources == (
        SourceLoaded("nics_recipe", rows=2, resolved=2, retired=0, without_main=1),
    )


async def test_NICS_행은_주재료_구간이_없어도_재료와_알레르기는_그대로_싣는다(session, opener):
    lumped = RawRecipe("6", "미역국", ingredients=(RawIngredient("두부", role="재료"),))

    await _recipes_only(opener, _fetching(_nics(lumped)), table=grouped_table)

    [row] = (await session.scalars(select(RecipeCatalog))).all()
    assert (row.resolved, row.food_groups, row.main_ingredients) == (True, [], [])
    assert row.ingredients == ["두부"] and 5 in row.allergen_codes


# ── 어떤 소스를 받을지 ────────────────────────────────────────────────
@pytest.fixture
def fetchers(monkeypatch):
    """농식품정보원 · NICS 받기를 가짜로 바꾸고 불린 순서를 기록한다."""
    calls: list[str] = []

    async def mafra():
        calls.append("mafra")
        return sync_of(_soup("1", "된장찌개"))

    async def nics():
        calls.append("nics")
        return sync_of(_soup("1", "열무김치"))

    monkeypatch.setattr(recipe_fetch, "fetch_mafra", mafra)
    monkeypatch.setattr(recipe_fetch, "fetch_nics", nics)
    return calls


async def test_NICS_키가_비면_받지_않고_건너뜀으로_센다(fetchers, monkeypatch):
    monkeypatch.setattr(settings, "NICS_RECIPE_API_KEY", "")

    got = await load_food_catalog._fetch_recipes()

    assert fetchers == ["mafra"]
    assert [b.source for b in got.batches] == ["mafra_recipe"]
    assert got.skipped == ("nics_recipe",)


async def test_NICS_키가_있으면_두_소스를_차례로_받는다(fetchers, monkeypatch):
    monkeypatch.setattr(settings, "NICS_RECIPE_API_KEY", "fake")

    got = await load_food_catalog._fetch_recipes()

    assert fetchers == ["mafra", "nics"]
    assert [b.source for b in got.batches] == ["mafra_recipe", "nics_recipe"]
    assert got.skipped == ()


async def test_농식품정보원은_필수라_실패하면_NICS_를_받지_않고_올린다(monkeypatch):
    calls: list[str] = []

    async def mafra():
        raise RuntimeError("mafra 장애")

    async def nics():
        calls.append("nics")
        return sync_of()

    monkeypatch.setattr(recipe_fetch, "fetch_mafra", mafra)
    monkeypatch.setattr(recipe_fetch, "fetch_nics", nics)
    monkeypatch.setattr(settings, "NICS_RECIPE_API_KEY", "fake")

    with pytest.raises(RuntimeError, match="mafra"):
        await load_food_catalog._fetch_recipes()
    assert calls == []
