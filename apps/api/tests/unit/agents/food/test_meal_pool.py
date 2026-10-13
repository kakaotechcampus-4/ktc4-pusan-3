"""후보 풀 · 샘플링 — 알레르기 · 연령 · 최근 추천 · 단계로 거르고 가중치로 뽑는다.

풀은 코드가 만든다. 모델은 샘플로 보여 준 후보 중에서만 고른다. 레시피 · 재료 · 아이는 전부 가짜다.
"""

from collections.abc import Sequence
from datetime import date

import pytest

from app.agents.food import meal_pool
from app.agents.food.meal_pool import (
    POOL_MAX,
    RECENT_EATEN_DAYS,
    SAMPLE_MAX,
    SAMPLE_MIN,
    PoolItem,
    build_pool,
    remember_sample,
    sample_pool,
)
from app.agents.food.nutrition_eval import NutrientVerdict
from app.agents.food.store import (
    InMemoryDaycareMeals,
    InMemoryFoodMemory,
    InMemoryMenuCatalog,
    InMemoryRecipeCatalog,
    InMemorySuggestionHistory,
    MenuCatalogRow,
    SafetyEntry,
)
from tests.unit.agents.food.support import (
    CHILD,
    affinity,
    context,
    daycare,
    menu_row,
    observation,
)

EGG = [SafetyEntry(kind="allergy", label="달걀", status="active")]
SHRIMP = [SafetyEntry(kind="allergy", label="새우", status="active")]
FOUR_YEARS = date(2022, 8, 10)  # 50개월 — preschool
ONE_YEAR = date(2025, 8, 10)  # 14개월 — toddler 가 막 시작


def recipe(name: str, *ingredients: str, **overrides: object) -> MenuCatalogRow:
    return menu_row(
        name, source="mafra_recipe", ingredients=ingredients or ("가짜재료",), **overrides
    )


def recipes(*rows: MenuCatalogRow) -> InMemoryRecipeCatalog:
    return InMemoryRecipeCatalog(rows)


def keys(pool: Sequence[PoolItem]) -> list[str]:
    return [item.row.menu_key for item in pool]


def weight_of(pool: Sequence[PoolItem], key: str) -> float:
    return next(item.weight for item in pool if item.row.menu_key == key)


# ── 풀에서 빠지는 것 ──────────────────────────────────────────────────
async def test_알레르기에_걸리는_레시피는_풀에_없다() -> None:
    ctx = context(
        recipes=recipes(
            recipe("계란말이", "달걀", "파", allergen_codes=frozenset({1})),
            recipe("두부조림", "두부", "간장"),
        ),
        safety=EGG,
    )

    assert keys(await build_pool(ctx)) == ["두부조림"]


async def test_재료_이름에만_알레르기_식품이_있어도_코드가_비었으면_사전으로_거른다() -> None:
    ctx = context(
        recipes=recipes(recipe("볶음밥", "밥", "새우"), recipe("두부조림", "두부")),
        safety=SHRIMP,
    )

    assert keys(await build_pool(ctx)) == ["두부조림"]


async def test_연령_금지_식품은_풀에_없다() -> None:
    # 팝콘 · 사탕 같은 질식 위험 식품은 48개월 미만에게 막는다
    rows = [recipe("팝콘", "옥수수"), recipe("두부조림", "두부")]

    assert keys(await build_pool(context(recipes=recipes(*rows)))) == ["두부조림"]  # 28개월
    assert sorted(keys(await build_pool(context(recipes=recipes(*rows), birth=FOUR_YEARS)))) == [
        "두부조림",
        "팝콘",
    ]


async def test_제품마다_다른_성분_때문에만_막히는_레시피도_풀에_없다() -> None:
    wheat = [SafetyEntry(kind="allergy", label="밀", status="active")]
    ctx = context(
        recipes=recipes(recipe("불고기", "소고기", "간장", "양파"), recipe("두부조림", "두부")),
        safety=wheat,
    )

    assert keys(await build_pool(ctx)) == ["두부조림"]


async def test_재료를_모르는_레시피는_풀에_없다() -> None:
    unknown = menu_row("미지의국", source="mafra_recipe", ingredients=())

    ctx = context(recipes=recipes(unknown, recipe("두부조림")))

    assert keys(await build_pool(ctx)) == ["두부조림"]


async def test_최근_추천한_메뉴는_풀에서_뺀다() -> None:
    ctx = context(
        recipes=recipes(recipe("된장찌개"), recipe("두부조림")),
        suggestions=InMemorySuggestionHistory({CHILD: frozenset({"된장찌개"})}),
    )

    assert keys(await build_pool(ctx)) == ["두부조림"]


async def test_단계가_안_맞는_레시피는_풀에_없다() -> None:
    rows = [
        recipe("취학전메뉴", stage_min="preschool"),
        recipe("유아메뉴", stage_min="toddler"),
        recipe("태깅없는메뉴"),  # 비면 toddler 로 본다
    ]

    assert sorted(keys(await build_pool(context(recipes=recipes(*rows))))) == [
        "유아메뉴",
        "태깅없는메뉴",
    ]
    assert len(await build_pool(context(recipes=recipes(*rows), birth=FOUR_YEARS))) == 3


async def test_이유기_아이에게는_태깅이_없는_레시피가_나가지_않는다() -> None:
    ctx = context(recipes=recipes(recipe("태깅없는메뉴")), birth=date(2026, 3, 10))  # 7개월

    assert await build_pool(ctx) == ()


async def test_안전_정보를_못_읽었으면_풀을_만들지_않는다() -> None:
    ctx = context(recipes=recipes(recipe("두부조림")), safety=None)

    with pytest.raises(RuntimeError, match="안전 정보"):
        await build_pool(ctx)


async def test_풀은_삼십_개까지다() -> None:
    ctx = context(recipes=recipes(*(recipe(f"메뉴{chr(0xAC00 + i * 7)}") for i in range(40))))

    assert len(await build_pool(ctx)) == POOL_MAX


# ── 이름이 같은 레시피 ────────────────────────────────────────────────
async def test_이름이_같은_레시피는_풀에서_하나로_묶고_알레르기와_재료는_합집합이다() -> None:
    beef = recipe(
        "샤브샤브",
        "쇠고기",
        "배추",
        source_id="a",
        allergen_codes=frozenset({16}),
        main_ingredients=("쇠고기",),
        food_groups=frozenset({"meat_fish_egg_legumes"}),
    )
    shrimp = recipe(
        "샤브샤브",
        "새우",
        "배추",
        source_id="b",
        allergen_codes=frozenset({9}),
        main_ingredients=("새우",),
        food_groups=frozenset({"vegetables"}),
    )

    [item] = await build_pool(context(recipes=recipes(beef, shrimp)))

    assert item.row.allergen_codes == {9, 16}
    assert set(item.row.ingredients) == {"쇠고기", "새우", "배추"}
    assert set(item.row.main_ingredients) == {"쇠고기", "새우"}
    assert item.row.food_groups == {"meat_fish_egg_legumes", "vegetables"}


async def test_이름이_같은_레시피_중_하나만_알레르기여도_묶음_전체가_빠진다() -> None:
    # 하나만 고르면 다른 쪽 알레르기를 놓친다
    beef = recipe("샤브샤브", "쇠고기", "배추", source_id="a", allergen_codes=frozenset({16}))
    shrimp = recipe("샤브샤브", "새우", "배추", source_id="b", allergen_codes=frozenset({9}))

    ctx = context(recipes=recipes(beef, shrimp), safety=SHRIMP)

    assert await build_pool(ctx) == ()


async def test_이름이_같은_레시피_중_단계가_높은_쪽을_따른다() -> None:
    early = recipe("샤브샤브", "배추", source_id="a", stage_min="toddler")
    late = recipe("샤브샤브", "배추", source_id="b", stage_min="preschool")

    assert await build_pool(context(recipes=recipes(early, late))) == ()  # 28개월은 toddler


# ── 가중치 ────────────────────────────────────────────────────────────
async def test_기피는_풀에_남고_가중치만_낮다() -> None:
    ctx = context(
        recipes=recipes(recipe("브로콜리볶음", "브로콜리"), recipe("두부조림", "두부")),
        memory=InMemoryFoodMemory({CHILD: [affinity("브로콜리", polarity=-1)]}, {}),
    )

    pool = await build_pool(ctx)

    assert sorted(keys(pool)) == ["두부조림", "브로콜리볶음"]  # 지우지 않는다
    assert 0 < weight_of(pool, "브로콜리볶음") < weight_of(pool, "두부조림")


async def test_선호는_가중치가_높다() -> None:
    ctx = context(
        recipes=recipes(recipe("딸기푸딩", "딸기"), recipe("두부조림", "두부")),
        memory=InMemoryFoodMemory({CHILD: [affinity("딸기", polarity=1)]}, {}),
    )

    pool = await build_pool(ctx)

    assert weight_of(pool, "딸기푸딩") > weight_of(pool, "두부조림")


def verdict(*, low: tuple[str, ...] = (), insufficient: bool = False) -> NutrientVerdict:
    return NutrientVerdict(
        bands={nutrient: "low" for nutrient in low},
        unscored=(),
        records=12,
        resolved_ratio=1.0,
        insufficient=insufficient,
        food_groups=frozenset(),
    )


@pytest.fixture
def fake_verdict(monkeypatch: pytest.MonkeyPatch):
    def install(result: NutrientVerdict) -> list[int]:
        calls: list[int] = []

        async def fake(context, *, months: int) -> NutrientVerdict:
            calls.append(months)
            return result

        monkeypatch.setattr(meal_pool, "evaluate_nutrient_bands", fake)
        return calls

    return install


async def test_부족_식품군은_기피를_상쇄하고_풀_상단에_남는다(fake_verdict) -> None:
    fake_verdict(verdict(low=("calcium",)))
    ctx = context(
        recipes=recipes(
            recipe("우유찜", "우유", food_groups=frozenset({"dairy"})),
            recipe("두부조림", "두부"),
        ),
        memory=InMemoryFoodMemory({CHILD: [affinity("우유", polarity=-1)]}, {}),
    )

    pool = await build_pool(ctx)

    # 우유를 싫어하지만 칼슘이 적으면 우유 든 메뉴가 평소 순위 아래로 내려가지 않는다
    assert weight_of(pool, "우유찜") >= weight_of(pool, "두부조림")


async def test_부족_식품군이_없으면_식품군_가중치가_없다(fake_verdict) -> None:
    fake_verdict(verdict(low=()))
    ctx = context(
        recipes=recipes(
            recipe("우유찜", "우유", food_groups=frozenset({"dairy"})), recipe("두부조림", "두부")
        )
    )

    pool = await build_pool(ctx)

    assert weight_of(pool, "우유찜") == weight_of(pool, "두부조림")


async def test_기록이_모자라면_영양_가중치를_주지_않는다(fake_verdict) -> None:
    fake_verdict(verdict(low=("calcium",), insufficient=True))
    ctx = context(
        recipes=recipes(
            recipe("우유찜", "우유", food_groups=frozenset({"dairy"})), recipe("두부조림", "두부")
        )
    )

    pool = await build_pool(ctx)

    assert weight_of(pool, "우유찜") == weight_of(pool, "두부조림")


async def test_영양_판정은_12개월_이상일_때만_부른다(fake_verdict) -> None:
    calls = fake_verdict(verdict(low=("calcium",)))

    await build_pool(
        context(
            recipes=recipes(recipe("두부조림", stage_min="infant_weaning")), birth=date(2026, 3, 10)
        )
    )
    assert calls == []

    await build_pool(context(recipes=recipes(recipe("두부조림")), birth=ONE_YEAR))
    assert len(calls) == 1


async def test_오늘_먹은_식품군과_겹치면_가중치가_낮다(fake_verdict) -> None:
    fake_verdict(verdict())
    protein = frozenset({"meat_fish_egg_legumes"})
    ctx = context(
        recipes=recipes(
            recipe("두부조림", "두부", food_groups=protein),
            recipe("시금치나물", "시금치", food_groups=frozenset({"vegetables"})),
        ),
        memory=InMemoryFoodMemory({}, {CHILD: [observation("점심고기", days_ago=0)]}),
        catalog=InMemoryMenuCatalog([menu_row("점심고기", food_groups=protein)]),
    )

    pool = await build_pool(ctx)

    assert weight_of(pool, "두부조림") < weight_of(pool, "시금치나물")


async def test_오늘_급식의_식품군도_겹침으로_본다(fake_verdict) -> None:
    fake_verdict(verdict())
    protein = frozenset({"meat_fish_egg_legumes"})
    ctx = context(
        recipes=recipes(
            recipe("두부조림", "두부", food_groups=protein),
            recipe("시금치나물", "시금치", food_groups=frozenset({"vegetables"})),
        ),
        daycare=InMemoryDaycareMeals([daycare("급식고기", days_ago=0)]),
        catalog=InMemoryMenuCatalog([menu_row("급식고기", food_groups=protein)]),
    )

    pool = await build_pool(ctx)

    assert weight_of(pool, "두부조림") < weight_of(pool, "시금치나물")


# ── 최근 3일 먹은 메뉴 ────────────────────────────────────────────────
def pool_with(*, observations=(), served=()) -> object:
    return context(
        recipes=recipes(recipe("두부조림", "두부"), recipe("시금치나물", "시금치")),
        memory=InMemoryFoodMemory({}, {CHILD: list(observations)}),
        daycare=InMemoryDaycareMeals(list(served)),
    )


def test_반복_창은_오늘을_포함한_3일이다() -> None:
    assert RECENT_EATEN_DAYS == 3


@pytest.mark.parametrize("days_ago", [0, 1, 2])
async def test_최근_3일_안에_먹은_메뉴는_풀에_남고_가중치만_낮다(days_ago: int) -> None:
    pool = await build_pool(pool_with(observations=[observation("두부조림", days_ago=days_ago)]))

    assert sorted(keys(pool)) == ["두부조림", "시금치나물"]  # 빼지 않는다
    assert 0 < weight_of(pool, "두부조림") < weight_of(pool, "시금치나물")


async def test_3일보다_전에_먹은_메뉴는_낮추지_않는다() -> None:
    pool = await build_pool(pool_with(observations=[observation("두부조림", days_ago=3)]))

    assert weight_of(pool, "두부조림") == weight_of(pool, "시금치나물")


async def test_급식으로_먹은_메뉴도_반복으로_센다() -> None:
    pool = await build_pool(pool_with(served=[daycare("두부조림", "밥", days_ago=1)]))

    assert weight_of(pool, "두부조림") < weight_of(pool, "시금치나물")


async def test_오늘_급식은_세고_내일_급식은_세지_않는다() -> None:
    today = await build_pool(pool_with(served=[daycare("두부조림", days_ago=0)]))
    tomorrow = await build_pool(pool_with(served=[daycare("두부조림", days_ago=-1)]))

    assert weight_of(today, "두부조림") < weight_of(today, "시금치나물")
    assert weight_of(tomorrow, "두부조림") == weight_of(tomorrow, "시금치나물")


async def test_반복은_메뉴_이름이_같을_때만이다() -> None:
    # "두부" 를 먹었다고 두부조림까지 반복으로 보지 않는다 — 재료가 같은 것은 반복이 아니다
    pool = await build_pool(pool_with(observations=[observation("두부", days_ago=1)]))

    assert weight_of(pool, "두부조림") == weight_of(pool, "시금치나물")


async def test_띄어쓰기만_다른_이름도_같은_메뉴로_본다() -> None:
    pool = await build_pool(pool_with(observations=[observation("두부 조림", days_ago=1)]))

    assert weight_of(pool, "두부조림") < weight_of(pool, "시금치나물")


# ── 샘플링 ────────────────────────────────────────────────────────────
def many(count: int, *, main: str | None = None) -> list[MenuCatalogRow]:
    names = [f"메뉴{chr(0xAC00 + i * 11)}" for i in range(count)]
    return [recipe(name, main_ingredients=(main,) if main else ()) for name in names]


async def pool_of(count: int) -> tuple[PoolItem, ...]:
    return await build_pool(context(recipes=recipes(*many(count))))


async def test_샘플은_십에서_십오_개다() -> None:
    pool = await pool_of(30)

    for seed in ("a", "b", "c", "d", "e"):
        assert SAMPLE_MIN <= len(sample_pool(pool, seed=seed)) <= SAMPLE_MAX


async def test_같은_seed_면_같은_샘플이다() -> None:
    pool = await pool_of(30)

    first = keys(sample_pool(pool, seed="run-1"))

    assert keys(sample_pool(pool, seed="run-1")) == first
    assert keys(sample_pool(pool, seed="run-1")) == first


async def test_같은_run_id_로_풀부터_세_번_만들어도_같은_샘플이다() -> None:
    rows = many(40)
    runs = []
    for _ in range(3):
        pool = await build_pool(context(recipes=recipes(*rows), run_id="run-7"))
        runs.append(keys(sample_pool(pool, seed="run-7")))

    assert runs[0] == runs[1] == runs[2]


async def test_seed_가_다르면_샘플이_달라진다() -> None:
    pool = await pool_of(30)

    assert keys(sample_pool(pool, seed="run-1")) != keys(sample_pool(pool, seed="run-2"))


async def test_풀이_샘플보다_작으면_풀_전체를_낸다() -> None:
    pool = await pool_of(2)  # 3개 미만이어도 재호출하지 않고 그대로 낸다

    assert sorted(keys(sample_pool(pool, seed="x"))) == sorted(keys(pool))


async def test_풀이_비면_샘플도_비었다() -> None:
    assert sample_pool((), seed="x") == ()


async def test_같은_주재료는_샘플에_두_개까지다() -> None:
    rows = [
        recipe(f"쇠고기{chr(0xAC00 + i * 13)}요리", main_ingredients=("쇠고기",)) for i in range(8)
    ]
    rows += [
        recipe("두부조림", main_ingredients=("두부",)),
        recipe("시금치나물", main_ingredients=("시금치",)),
    ]
    pool = await build_pool(context(recipes=recipes(*rows)))

    sample = sample_pool(pool, seed="run-1")

    beef = [item for item in sample if "쇠고기" in item.row.main_ingredients]
    assert len(beef) == 2
    assert {"두부조림", "시금치나물"} <= set(keys(sample))


async def test_주재료_칸이_비면_제약을_건너뛰고_풀을_줄이지_않는다() -> None:
    pool = await pool_of(30)  # 주재료 정보가 전혀 없다

    for seed in ("a", "b", "c"):
        # 제약 때문에 건너뛴 자리가 없다 — 정해진 크기(10~15)가 그대로 나온다
        assert SAMPLE_MIN <= len(sample_pool(pool, seed=seed)) <= SAMPLE_MAX


async def test_가중치가_높은_후보가_더_자주_뽑힌다() -> None:
    heavy = recipe("두부조림", "두부")
    light = [recipe(f"기피메뉴{chr(0xAC00 + i * 17)}", "브로콜리") for i in range(20)]
    ctx = context(
        recipes=recipes(heavy, *light),
        memory=InMemoryFoodMemory(
            {CHILD: [affinity("브로콜리", polarity=-1), affinity("두부", polarity=1)]}, {}
        ),
    )
    pool = await build_pool(ctx)

    picked = sum("두부조림" in keys(sample_pool(pool, seed=f"s{n}")) for n in range(40))

    assert picked >= 36  # 선호는 거의 늘 뽑힌다
    assert all(item.weight > 0 for item in pool)  # 기피도 0 이 되지 않는다


# ── 보여 준 샘플을 run state 에 ───────────────────────────────────────
async def test_보여_준_샘플을_menu_key_별로_run_state_에_적는다() -> None:
    pool = await pool_of(30)
    sample = sample_pool(pool, seed="run-1")
    ctx = context()

    remember_sample(ctx, sample)

    assert list(ctx.state.sampled) == keys(sample)
    assert ctx.state.sampled[keys(sample)[0]] is sample[0].row


async def test_샘플이_세_개_이상이면_더_채울_길이가_있고_재호출_뒤에는_없다() -> None:
    sample = sample_pool(await pool_of(30), seed="run-1")
    ctx = context()

    remember_sample(ctx, sample)
    assert ctx.state.exhausted is False

    remember_sample(ctx, sample, recalled=True)
    assert ctx.state.exhausted is True


async def test_샘플이_세_개_미만이면_처음부터_더_채울_길이가_없다() -> None:
    ctx = context()

    remember_sample(ctx, sample_pool(await pool_of(2), seed="run-1"))

    assert ctx.state.exhausted is True
    assert len(ctx.state.sampled) == 2
