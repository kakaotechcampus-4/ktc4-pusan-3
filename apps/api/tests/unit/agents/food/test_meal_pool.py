"""후보 풀 · 샘플링 — 알레르기 · 연령 · 최근 추천 · 단계로 거르고 가중치로 뽑는다.

풀은 코드가 만든다. 모델은 샘플로 보여 준 후보 중에서만 고른다. 레시피 · 재료 · 아이는 전부 가짜다.
"""

from collections.abc import Sequence
from datetime import date

import pytest

from app.agents.food import meal_pool
from app.agents.food.meal_pool import (
    DEFICIT_BOOST,
    POOL_MAX,
    RECENT_EATEN_DAYS,
    SAMPLE_MAX,
    SAMPLE_MIN,
    PoolItem,
    build_pool,
    explainable_groups,
    remember_sample,
    requested_rows,
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
from app.rules.menu_evidence import menu_evidence_id
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


async def test_이름이_같은_레시피의_주재료는_처음_나온_순서로_한_번씩_합친다() -> None:
    # 준비 목록(`preparation_items`)이 이 합집합을 입력으로 쓴다 (#474)
    first = recipe(
        "샤브샤브", "쇠고기", "배추", source_id="a", main_ingredients=("쇠고기", "소금", "배추")
    )
    second = recipe(
        "샤브샤브", "새우", "배추", source_id="b", main_ingredients=("새우", "쇠고기", "배추")
    )

    [item] = await build_pool(context(recipes=recipes(first, second)))

    assert item.row.main_ingredients == ("쇠고기", "소금", "배추", "새우")


async def test_이름이_같은_레시피_중_하나만_알레르기여도_묶음_전체가_빠진다() -> None:
    # 하나만 고르면 다른 쪽 알레르기를 놓친다
    beef = recipe("샤브샤브", "쇠고기", "배추", source_id="a", allergen_codes=frozenset({16}))
    shrimp = recipe("샤브샤브", "새우", "배추", source_id="b", allergen_codes=frozenset({9}))

    ctx = context(recipes=recipes(beef, shrimp), safety=SHRIMP)

    assert await build_pool(ctx) == ()


async def test_이름이_같은_레시피의_대표_행은_입력_순서와_무관하다() -> None:
    # 대표 행의 (source, source_id) 가 메뉴 근거 id 가 된다. 입력 순서에 따라 바뀌면 같은 메뉴의
    # 근거 id 가 run 마다 달라진다. 농식품정보원 번호가 더 커도 소스 이름순(mafra_recipe 먼저)이다
    mafra = recipe("열무김치", "열무", source_id="9", allergen_codes=frozenset({5}))
    nics = menu_row(
        "열무김치",
        source="nics_recipe",
        ingredients=("열무", "고춧가루"),
        source_id="3",
        allergen_codes=frozenset({6}),
    )

    [forward] = await build_pool(context(recipes=recipes(mafra, nics)))
    [backward] = await build_pool(context(recipes=recipes(nics, mafra)))

    for pool_item in (forward, backward):
        assert (pool_item.row.source, pool_item.row.source_id) == ("mafra_recipe", "9")
        assert pool_item.row.source_version == mafra.source_version
        assert pool_item.row.allergen_codes == {5, 6}  # 합집합은 그대로다
        assert set(pool_item.row.ingredients) == {"열무", "고춧가루"}
        # 대표가 아닌 쪽도 근거로 달 수 있게 남긴다
        assert pool_item.row.merged_from == (("nics_recipe", "3"),)
    assert menu_evidence_id(forward.row.source, forward.row.source_id) == menu_evidence_id(
        backward.row.source, backward.row.source_id
    )


async def test_같은_소스의_같은_이름_레시피는_번호_순으로_대표를_정한다() -> None:
    first = recipe("샤브샤브", "쇠고기", source_id="a")
    second = recipe("샤브샤브", "새우", source_id="b")

    [forward] = await build_pool(context(recipes=recipes(first, second)))
    [backward] = await build_pool(context(recipes=recipes(second, first)))

    assert forward.row.source_id == backward.row.source_id == "a"


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


def verdict(
    *,
    low: tuple[str, ...] = (),
    insufficient: bool = False,
    group_days: dict[str, int] | None = None,
    recorded_days: int = 7,
    appearance_ready: bool = False,
    unknown_days: dict[str, int] | None = None,
) -> NutrientVerdict:
    return NutrientVerdict(
        bands={nutrient: "low" for nutrient in low},
        unscored=(),
        records=12,
        resolved_ratio=1.0,
        insufficient=insufficient,
        food_groups=frozenset(group_days or ()),
        food_group_days=dict(group_days or {}),
        recorded_days=recorded_days,
        group_unknown_days=dict(unknown_days or {}),
        appearance_ready=appearance_ready,
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


# ── 채소 · 과일 부족 가중 (#411 T4) ──────────────────────────────────────
# 영양 구간에는 채소 · 과일에 대응하는 항목이 없다. 최근 7일 중 나온 날 수가 기록된 날의 절반보다
# 적으면 그 식품군을 부족 식품군에 더한다(N-19).
VEGETABLES = frozenset({"vegetables"})
FRUITS = frozenset({"fruits"})


def veg_and_plain_recipes() -> InMemoryRecipeCatalog:
    return recipes(
        recipe("시금치나물", "시금치", food_groups=VEGETABLES),
        recipe("사과조림", "사과", food_groups=FRUITS),
        recipe("두부조림", "두부"),
    )


async def test_7일_중_채소가_1일이면_채소_메뉴에_부족_가중치가_붙는다(fake_verdict) -> None:
    fake_verdict(verdict(group_days={"vegetables": 1, "fruits": 7}, appearance_ready=True))

    pool = await build_pool(context(recipes=veg_and_plain_recipes()))

    assert weight_of(pool, "시금치나물") == pytest.approx(
        DEFICIT_BOOST * weight_of(pool, "두부조림")
    )
    assert weight_of(pool, "사과조림") == weight_of(pool, "두부조림")  # 과일은 충분히 나왔다


async def test_기준_일수_이상_나왔으면_가중치가_없다(fake_verdict) -> None:
    # 기록된 7일의 절반(3.5일)을 넘는 4일부터 충분하다
    fake_verdict(verdict(group_days={"vegetables": 4, "fruits": 7}, appearance_ready=True))

    pool = await build_pool(context(recipes=veg_and_plain_recipes()))

    assert weight_of(pool, "시금치나물") == weight_of(pool, "두부조림")


async def test_기준_일수는_기록된_날_수로_본다(fake_verdict) -> None:
    # 4일만 기록했으면 절반은 2일 — 채소가 2일 나왔으면 충분하고 1일이면 부족하다
    fake_verdict(
        verdict(group_days={"vegetables": 2, "fruits": 4}, recorded_days=4, appearance_ready=True)
    )
    pool = await build_pool(context(recipes=veg_and_plain_recipes()))
    assert weight_of(pool, "시금치나물") == weight_of(pool, "두부조림")

    fake_verdict(
        verdict(group_days={"vegetables": 1, "fruits": 4}, recorded_days=4, appearance_ready=True)
    )
    pool = await build_pool(context(recipes=veg_and_plain_recipes()))
    assert weight_of(pool, "시금치나물") == pytest.approx(
        DEFICIT_BOOST * weight_of(pool, "두부조림")
    )


async def test_한_번도_안_나온_과일은_부족_식품군이다(fake_verdict) -> None:
    fake_verdict(
        verdict(group_days={"vegetables": 7}, appearance_ready=True)
    )  # fruits 키 없음 = 0일

    pool = await build_pool(context(recipes=veg_and_plain_recipes()))

    assert weight_of(pool, "사과조림") == pytest.approx(DEFICIT_BOOST * weight_of(pool, "두부조림"))
    assert weight_of(pool, "시금치나물") == weight_of(pool, "두부조림")


async def test_기록이_모자라면_채소_과일_가중치를_주지_않는다(fake_verdict) -> None:
    fake_verdict(verdict(group_days={}, insufficient=True, appearance_ready=False))

    pool = await build_pool(context(recipes=veg_and_plain_recipes()))

    assert weight_of(pool, "시금치나물") == weight_of(pool, "두부조림")
    assert weight_of(pool, "사과조림") == weight_of(pool, "두부조림")


async def test_구간은_막혀도_기록이_충분하면_채소_과일_가중치는_준다(fake_verdict) -> None:
    # 1인분 중량이 빈 메뉴 때문에 구간(insufficient)은 못 냈어도 출현은 센다
    fake_verdict(
        verdict(group_days={"vegetables": 1, "fruits": 7}, insufficient=True, appearance_ready=True)
    )

    pool = await build_pool(context(recipes=veg_and_plain_recipes()))

    assert weight_of(pool, "시금치나물") == pytest.approx(
        DEFICIT_BOOST * weight_of(pool, "두부조림")
    )


async def test_이유기_단계는_채소_과일_가중치를_주지_않는다(fake_verdict) -> None:
    fake_verdict(verdict(group_days={"vegetables": 0}, appearance_ready=True))
    rows = recipes(
        recipe("시금치죽", "시금치", food_groups=VEGETABLES, stage_min="infant_weaning"),
        recipe("두부죽", "두부", stage_min="infant_weaning"),
    )

    pool = await build_pool(context(recipes=rows, birth=date(2026, 3, 10)))

    assert weight_of(pool, "시금치죽") == weight_of(pool, "두부죽")


async def test_칼슘이_low_이고_채소도_부족하면_둘_다_가중한다(fake_verdict) -> None:
    fake_verdict(
        verdict(low=("calcium",), group_days={"vegetables": 1, "fruits": 7}, appearance_ready=True)
    )
    rows = recipes(
        recipe("우유찜", "우유", food_groups=frozenset({"dairy"})),
        recipe("시금치나물", "시금치", food_groups=VEGETABLES),
        recipe("두부조림", "두부"),
    )

    pool = await build_pool(context(recipes=rows))

    assert weight_of(pool, "우유찜") == pytest.approx(DEFICIT_BOOST * weight_of(pool, "두부조림"))
    assert weight_of(pool, "시금치나물") == pytest.approx(
        DEFICIT_BOOST * weight_of(pool, "두부조림")
    )


async def test_채소와_유제품이_함께_든_메뉴는_부족_가중치를_한_번만_받는다(fake_verdict) -> None:
    # 칼슘 low(dairy) 와 채소 부족(vegetables) 이 한 메뉴에 겹쳐도 DEFICIT_BOOST 는 한 번만 곱한다
    fake_verdict(verdict(low=("calcium",), group_days={"vegetables": 1}, appearance_ready=True))
    rows = recipes(
        recipe("시금치치즈전", "시금치", food_groups=frozenset({"dairy", "vegetables"})),
        recipe("두부조림", "두부"),
    )

    pool = await build_pool(context(recipes=rows))

    assert weight_of(pool, "시금치치즈전") == pytest.approx(
        DEFICIT_BOOST * weight_of(pool, "두부조림")
    )


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


# ── 안내 줄에 말해도 되는 식품군 (#739) ─────────────────────────────────────
# 가중(`_sparse_groups`)은 확인한 날만 센다. 문장은 보호자에게 하는 말이라, 식품군을 모르는 메뉴가
# 낀 날을 전부 "나왔다" 로 쳐도 절반 미만일 때만 낸다. 기록된 6일이면 절반은 3일이다.
def test_모르는_날을_다_나왔다고_쳐도_절반_미만이면_말한다() -> None:
    result = explainable_groups(
        verdict(
            group_days={"vegetables": 1, "fruits": 6},
            recorded_days=6,
            appearance_ready=True,
            unknown_days={"vegetables": 1},
        )
    )

    assert result == frozenset({"vegetables"})  # 1 + 1 = 2 < 3


def test_모르는_날까지_더해_절반이면_말하지_않는다() -> None:
    result = explainable_groups(
        verdict(
            group_days={"vegetables": 2, "fruits": 6},
            recorded_days=6,
            appearance_ready=True,
            unknown_days={"vegetables": 1},
        )
    )

    assert result == frozenset()  # 2 + 1 = 3, 절반 미만이 아니다


def test_모르는_날이_없으면_가중_기준과_같다() -> None:
    sparse = verdict(
        group_days={"vegetables": 2, "fruits": 3}, recorded_days=6, appearance_ready=True
    )

    assert explainable_groups(sparse) == frozenset({"vegetables"})  # 2 < 3, 3 은 아니다


def test_가중은_걸려도_모르는_날_때문에_말하지_않는_식품군이_있다() -> None:
    # 채소는 확인한 날 2일(< 3)이라 가중이 걸리지만 모르는 날 2일을 더하면 4 라서 문장은 없다
    sparse = verdict(
        group_days={"vegetables": 2, "fruits": 6},
        recorded_days=6,
        appearance_ready=True,
        unknown_days={"vegetables": 2},
    )

    assert meal_pool._sparse_groups(sparse) == frozenset({"vegetables"})
    assert explainable_groups(sparse) == frozenset()


def test_출현을_말할_수_없으면_말할_식품군도_없다() -> None:
    result = explainable_groups(verdict(group_days={"vegetables": 0}, appearance_ready=False))

    assert result == frozenset()


# ── 조건 맞는 레시피 — 요청 글에 이름 · 재료가 나온 레시피 (#740) ───────────────
def items(*rows: MenuCatalogRow) -> tuple[PoolItem, ...]:
    return tuple(PoolItem(row=row, weight=1.0) for row in rows)


CUCUMBER = recipe("오이무침", "오이", "참기름")
PAPRIKA = recipe("파프리카볶음", "파프리카", "올리브유")
JAPCHAE = recipe("잡채", "당면", "파프리카", "시금치")
TOFU = recipe("두부조림", "두부", "간장")
SUGAR_SNACK = recipe("설탕쿠키", "밀가루", "설탕")


def test_요청_글에_재료가_나온_레시피를_찾는다() -> None:
    found = requested_rows(items(CUCUMBER, TOFU), ["오이 들어간 음식 추천해줘"])

    assert keys(found) == ["오이무침"]


def test_이름이_나온_레시피도_찾는다() -> None:
    found = requested_rows(items(CUCUMBER, TOFU), ["두부조림 같은 반찬 추천해줘"])

    assert keys(found) == ["두부조림"]


def test_재료로만_들어간_레시피도_찾는다() -> None:
    found = requested_rows(items(PAPRIKA, JAPCHAE, TOFU), ["파프리카 들어간 반찬 추천해줘"])

    assert keys(found) == ["파프리카볶음", "잡채"]


def test_한_글자_재료는_조건으로_보지_않는다() -> None:
    radish = recipe("무나물", "무", "참기름")
    leek = recipe("파전", "파", "밀가루")

    assert requested_rows(items(radish, leek), ["파 들어간 반찬 추천해줘"]) == ()


def test_빼고_말고_같은_말이_붙은_낱말은_조건이_아니다() -> None:
    texts = (
        "설탕 적은 간식 추천해줘",
        "설탕 빼고 간식 추천해줘",
        "설탕 말고 간식",
        "설탕 없는 간식",
    )
    for text in texts:
        assert requested_rows(items(SUGAR_SNACK), [text]) == (), text


def test_부정하지_않은_다른_자리에_나오면_조건이다() -> None:
    found = requested_rows(items(SUGAR_SNACK), ["설탕 빼고 간식 말고 설탕쿠키 추천해줘"])

    assert keys(found) == ["설탕쿠키"]


def test_조건이_없는_요청은_빈_목록이다() -> None:
    assert requested_rows(items(CUCUMBER, TOFU), ["저녁 추천해줘"]) == ()


def test_요청_글이_여럿이면_모두_본다() -> None:
    found = requested_rows(items(CUCUMBER, TOFU), ["저녁 추천해줘", "오이 들어간 걸로"])

    assert keys(found) == ["오이무침"]


def test_조건_맞는_레시피는_샘플_앞에_먼저_들어간다() -> None:
    many_rows = [recipe(f"기본반찬{i}", f"기본재료{i}") for i in range(30)]
    pool = items(*many_rows, CUCUMBER)
    found = requested_rows(pool, ["오이 들어간 음식"])

    for seed in ("a", "b", "c", "d", "e", "f"):
        sample = sample_pool(pool, seed=seed, first=found)
        assert keys(sample)[0] == "오이무침"
        assert SAMPLE_MIN <= len(sample) <= SAMPLE_MAX


def test_조건_맞는_레시피에는_같은_주재료_제한을_걸지_않는다() -> None:
    same_main = [
        recipe(f"파프리카요리{i}", "파프리카", main_ingredients=("파프리카",)) for i in range(4)
    ]
    others = [recipe(f"기본반찬{i}", f"기본재료{i}") for i in range(30)]
    pool = items(*same_main, *others)
    found = requested_rows(pool, ["파프리카 들어간 반찬"])

    sample = sample_pool(pool, seed="run-1", first=found)

    assert [k for k in keys(sample) if k.startswith("파프리카요리")] == keys(found)
    assert len(found) == 4


def test_조건_맞는_레시피가_샘플_크기보다_많아도_상한_안이다() -> None:
    matching = [recipe(f"오이요리{i}", "오이") for i in range(20)]
    pool = items(*matching)
    found = requested_rows(pool, ["오이 들어간 음식"])

    sample = sample_pool(pool, seed="run-1", first=found)

    assert len(sample) == SAMPLE_MAX
    assert all(k.startswith("오이요리") for k in keys(sample))


def test_first_가_없으면_지금_샘플과_같다() -> None:
    pool = items(*[recipe(f"기본반찬{i}", f"기본재료{i}") for i in range(30)])

    assert keys(sample_pool(pool, seed="run-1")) == keys(sample_pool(pool, seed="run-1", first=()))


# ── 풀 상한과 조건 맞는 레시피 — 상한(POOL_MAX)이 조건 맞는 레시피를 자르면 안 된다 (#740) ──────
async def test_조건_맞는_레시피는_풀_상한에_잘리지_않는다() -> None:
    rows = [*many(POOL_MAX * 3), CUCUMBER]
    for run_id in ("run-1", "run-2", "run-3", "run-4", "run-5"):
        ctx = context(recipes=recipes(*rows), run_id=run_id)
        ctx.state.utterance = "오이 들어간 음식 추천해줘"

        pool = await build_pool(ctx)

        assert "오이무침" in keys(pool), run_id
        assert len(pool) <= POOL_MAX


async def test_조건_맞는_레시피가_상한보다_많아도_풀은_상한_안이다() -> None:
    matching = [recipe(f"오이요리{i}", "오이") for i in range(POOL_MAX + 10)]
    ctx = context(recipes=recipes(*matching, *many(20)))
    ctx.state.utterance = "오이 들어간 음식"

    pool = await build_pool(ctx)

    assert len(pool) == POOL_MAX
    assert all(key.startswith("오이요리") for key in keys(pool))


async def test_조건이_없는_요청은_풀을_지금처럼_만든다() -> None:
    rows = many(POOL_MAX * 3)
    plain = context(recipes=recipes(*rows), run_id="run-1")
    asked = context(recipes=recipes(*rows), run_id="run-1")
    asked.state.utterance = "저녁 추천해줘"

    assert keys(await build_pool(plain)) == keys(await build_pool(asked))


async def test_풀을_만들면_레시피북_전체와_최근_추천을_run_state_에_남긴다() -> None:
    ctx = context(
        recipes=recipes(TOFU, CUCUMBER),
        suggestions=InMemorySuggestionHistory({CHILD: {"오이무침"}}),
    )

    pool = await build_pool(ctx)

    assert keys(pool) == ["두부조림"]  # 최근 추천한 오이무침은 풀에서 빠진다
    assert ctx.state.recipe_keys == frozenset({"두부조림", "오이무침"})  # 풀 밖 레시피도 안다
    assert ctx.state.recent_keys == frozenset({"오이무침"})
