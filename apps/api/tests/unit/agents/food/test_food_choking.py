"""질식 주의 문구는 어디에도 나가지 않고, 48개월 미만 질식 위험 식품은 그대로 막힌다.

식단 추천 · 급식 어디에도 "통째로 먹으면 질식할 수 있어요" 같은 문구를 내지 않는다(어른이 곁에서
지켜본다는 전제). 대신 규칙은 그대로다 — 막는 것(block)은 막고, 문구를 달던 것(caution)은 문구 없이
후보에 남는다. 규칙 표(`food_safety_terms.yaml` age_rules)에서 항목을 읽어 표로 검증한다.
"""

from datetime import date

import pytest

from app.agents.common.reference import food_safety_terms
from app.agents.food import readouts
from app.agents.food.meal_pool import build_pool
from app.agents.food.readouts import READOUTS
from app.agents.food.schemas.menu import DaycareMenuArgs
from app.agents.food.schemas.recommend import MealCandidate, ProposeMealCandidatesArgs
from app.agents.food.store import (
    InMemoryDaycareMeals,
    InMemoryMenuCatalog,
    InMemoryRecipeCatalog,
    SafetyEntry,
)
from app.agents.food.tools.menu import lookup_daycare_menu
from app.agents.food.tools.recommend import propose_meal_candidates
from app.agents.food.tools.safety import filter_food_safety
from tests.unit.agents.food.support import context, daycare, menu_row

_RULES = food_safety_terms().age_rules
CAUTION = [rule for rule in _RULES if rule.action == "caution"]
BLOCK_BY_AGE = [rule for rule in _RULES if rule.action == "block" and rule.below_month is not None]
BLOCK_ALWAYS = [rule for rule in _RULES if rule.action == "block" and rule.below_month is None]

# NOW(2026-10-14) 기준 월령을 만드는 생일
BIRTH_47 = date(2022, 11, 10)
BIRTH_48 = date(2022, 10, 10)

CAUTION_ALIASES = [alias for rule in CAUTION for alias in rule.term.aliases]
RICE_CAKE = next(rule for rule in _RULES if rule.term.key == "떡")


def row_of(name: str):
    """이름만 있는 한 줄짜리 메뉴. 재료도 같은 이름이라 이름 · 재료 어느 쪽에서든 규칙이 걸린다."""
    return menu_row(name, source="mafra_recipe", ingredients=(name,))


def verdict_of(name: str, months: int, *, purpose: str = "recommend"):
    stage = "preschool" if months >= 36 else "toddler"
    return filter_food_safety([row_of(name)], [], stage, months=months, purpose=purpose)  # type: ignore[arg-type]


# ── 문구가 없다 ───────────────────────────────────────────────────────
def test_질식_주의_문구_키가_없다() -> None:
    assert "guide.choking_caution" not in READOUTS.texts
    assert not hasattr(readouts, "GUIDE_CHOKING_CAUTION")


def test_질식_주의_규칙은_표에_있고_막지_않는_것이다() -> None:
    assert CAUTION
    assert {rule.term.key for rule in CAUTION} == {"질식 주의 식품"}


@pytest.mark.parametrize("alias", CAUTION_ALIASES)
def test_질식_주의_식품은_48개월_미만에도_막히지_않는다(alias: str) -> None:
    for months in (13, 30, 47):
        assert verdict_of(alias, months).blocked == (), (alias, months)


@pytest.mark.parametrize("alias", CAUTION_ALIASES)
async def test_질식_주의_식품은_문구_없이_후보_풀에_남는다(alias: str) -> None:
    name = f"{alias}요리"
    ctx = context(recipes=InMemoryRecipeCatalog([row_of(name)]))

    pool = await build_pool(ctx)

    assert [item.row.menu_key for item in pool] == [row_of(name).menu_key] or pool, alias


async def test_질식_주의_식품을_추천해도_결과_어디에도_질식_문구가_없다() -> None:
    row = row_of("포도")
    ctx = context()
    ctx.state.sampled = {row.menu_key: row}
    ctx.state.exhausted = True
    args = ProposeMealCandidatesArgs(
        candidates=[
            MealCandidate(
                menu_key="포도", meal_slot="snack", reason="오늘 급식과 겹치지 않아서 골랐어요"
            )
        ]
    )

    result = await propose_meal_candidates(ctx, args)

    assert result.success
    [pick] = ctx.state.picks
    shown = str(pick.draft.to_payload()) + str(result.data)
    assert "질식" not in shown and "caution" not in shown
    assert ctx.state.notice is None or "질식" not in ctx.state.notice.body


@pytest.mark.parametrize("alias", CAUTION_ALIASES)
async def test_급식에_질식_주의_식품이_있어도_문구_없이_표시한다(alias: str) -> None:
    name = f"{alias}요리"
    ctx = context(
        daycare=InMemoryDaycareMeals([daycare(row_of(name).menu_key, days_ago=0)]),
        catalog=InMemoryMenuCatalog([menu_row(row_of(name).menu_key, ingredients=(alias,))]),
        safety=(),
    )

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert result.success
    assert [m["allergy"] for meal in result.data["meals"] for m in meal["menus"]] == ["ok"]
    assert "질식" not in str(result.data) and "caution" not in str(result.data)


def test_판정_자체는_주의를_계속_계산한다() -> None:
    # 문구를 화면으로 옮기지 않을 뿐 규칙은 그대로다 — 나중에 안내를 다시 붙일 수 있다
    assert verdict_of("포도", 30).cautions == {"포도": ("질식 주의 식품",)}
    assert verdict_of("포도", 48).cautions == {}


# ── 48개월 미만 질식 위험 식품은 그대로 막힌다 ────────────────────────
@pytest.mark.parametrize(
    ("rule", "alias"),
    [(rule, alias) for rule in BLOCK_BY_AGE for alias in rule.term.aliases],
    ids=lambda value: value if isinstance(value, str) else value.term.key,
)
def test_월령_규칙의_block_식품은_경계_하루_전까지_막고_경계에서_푼다(rule, alias: str) -> None:
    below = rule.below_month
    assert below is not None

    assert verdict_of(alias, below - 1).blocked, (
        f"{rule.term.key}/{alias} 가 {below - 1}개월에 안 막힌다"
    )
    # 다른 규칙이 나이와 상관없이 막는 이름("커피우유" 는 카페인)은 경계에서도 막힌다
    if not verdict_of(alias, 120).blocked:
        assert not verdict_of(alias, below).blocked, (
            f"{rule.term.key}/{alias} 가 {below}개월에도 막힌다"
        )


@pytest.mark.parametrize(
    ("rule", "alias"),
    [(rule, alias) for rule in BLOCK_ALWAYS for alias in rule.term.aliases],
    ids=lambda value: value if isinstance(value, str) else value.term.key,
)
def test_나이와_상관없는_block_식품은_늘_막는다(rule, alias: str) -> None:
    for months in (13, 47, 48, 70):
        assert verdict_of(alias, months).blocked, (rule.term.key, alias, months)


@pytest.mark.parametrize("alias", RICE_CAKE.term.aliases)
async def test_떡은_식단_추천에서_47개월에_막히고_48개월에_풀린다(alias: str) -> None:
    row = row_of(f"{alias}")

    blocked = await build_pool(context(recipes=InMemoryRecipeCatalog([row]), birth=BIRTH_47))
    open_ = await build_pool(context(recipes=InMemoryRecipeCatalog([row]), birth=BIRTH_48))

    assert blocked == ()
    assert [item.row.menu_key for item in open_] == [row.menu_key]


@pytest.mark.parametrize("alias", RICE_CAKE.term.aliases)
async def test_떡은_급식에서_47개월에도_막지_않고_문구_없이_표시한다(alias: str) -> None:
    ctx = context(
        daycare=InMemoryDaycareMeals([daycare(alias, days_ago=0)]),
        catalog=InMemoryMenuCatalog([menu_row(alias, ingredients=(alias,))]),
        safety=(),
        birth=BIRTH_47,
    )

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert [m["allergy"] for meal in result.data["meals"] for m in meal["menus"]] == ["ok"]
    assert "질식" not in str(result.data)


@pytest.mark.parametrize("alias", ["팝콘", "젤리", "사탕"])
async def test_질식_위험_식품은_급식에서도_그대로_경고한다(alias: str) -> None:
    ctx = context(
        daycare=InMemoryDaycareMeals([daycare(alias, days_ago=0)]),
        catalog=InMemoryMenuCatalog([menu_row(alias, ingredients=(alias,))]),
        safety=(),
        birth=BIRTH_47,
    )

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert [m["allergy"] for meal in result.data["meals"] for m in meal["menus"]] == ["blocked"]


# ── 견과 · 땅콩은 알레르기 필터가 따로 막는다 ─────────────────────────
@pytest.mark.parametrize("months", [30, 60])
async def test_땅콩_알레르기는_질식_규칙과_상관없이_막는다(months: int) -> None:
    peanut = [SafetyEntry(kind="allergy", label="땅콩", status="active")]
    ctx = context(
        recipes=InMemoryRecipeCatalog([row_of("땅콩조림")]),
        safety=peanut,
        birth=date(2026 - months // 12, 10, 10) if months >= 48 else date(2024, 6, 14),
    )

    assert await build_pool(ctx) == ()
