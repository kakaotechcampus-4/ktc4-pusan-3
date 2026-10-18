"""레시피 → 메뉴 카탈로그 행. 재료명 정리 표(`reference/recipe_ingredients.yaml`)로 재료를 읽는다.

정리 표는 알레르기 판정 입력이라 사람이 검수한다. 표에 없거나 검수 전인 재료가 하나라도 있는
레시피는 `resolved=false` 로 두어 후보 풀에 들어가지 않게 한다(#321 완료 기준). 레시피 · 재료는
전부 가짜다.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from app.agents.food.recipe_catalog import (
    IngredientEntry,
    build_recipe_row,
    ingredient_key,
    parse_ingredient_table,
)
from app.agents.food.store import NutritionFacts, SafetyEntry, in_memory_ports
from app.agents.food.store.ports import RECIPE_SOURCES, MenuCatalogRow
from app.agents.food.tools.safety import filter_food_safety
from app.core.mafra_recipe_raw import RawIngredient, RawRecipe
from app.rules.menu_key import normalize_menu_key

NOW = datetime(2026, 10, 11, tzinfo=UTC)


@pytest.fixture
def ports():
    return in_memory_ports()


def recipe_row(*, source_id: str, name: str) -> MenuCatalogRow:
    """레시피 카탈로그 행 하나 — 테스트용. 이름·source_id 는 전부 가짜다."""
    return MenuCatalogRow(
        menu_key=normalize_menu_key(name),
        display_name=name,
        source="mafra_recipe",
        resolved=True,
        synced_at=NOW,
        ingredients=("가짜재료",),
        source_id=source_id,
    )


def entry(
    raw: str,
    names: list[str] | None = None,
    codes: list[int] | None = None,
    *,
    status: str = "ok",
    food_group: str | None = None,
) -> dict[str, Any]:
    made: dict[str, Any] = {
        "raw": raw,
        "names": [raw] if names is None else names,
        "codes": codes or [],
        "status": status,
    }
    if food_group is not None:
        made["food_group"] = food_group
    return made


def table(*entries: dict[str, Any]) -> dict[str, IngredientEntry]:
    return parse_ingredient_table(
        {
            "source": "테스트",
            "version": "t",
            "fetched_at": "2026-10-11",
            "ingredients": list(entries),
        }
    )


TABLE = table(
    entry("돼기고기", ["돼지고기"], [10], food_group="meat_fish_egg_legumes"),  # 오타
    entry("안심", ["쇠고기"], [16], food_group="meat_fish_egg_legumes"),  # 부위명
    entry("감동젓", codes=[9]),  # 이름은 그대로 두고 코드만 — 사전은 감동젓(작은 새우젓)을 모른다
    entry("두부", codes=[5], food_group="meat_fish_egg_legumes"),
    entry("간장"),  # 밀 · 대두는 제품마다 — 이름이 남아 있으면 필터가 따로 본다
    entry("양파", food_group="vegetables"),
    entry("소금, 후추", ["소금", "후추"]),  # 묶음 표기
    entry("은박컵", [], status="not_food"),
    entry("육수", status="unknown"),
    entry("대파", status="draft"),
)


def recipe(*names: str | None, name: str = "가짜찌개") -> RawRecipe:
    return RawRecipe(
        recipe_id="1",
        name=name,
        ingredients=tuple(RawIngredient(name=n) for n in names),
    )


def recipe_with_roles(*lines: tuple[str, str | None], name: str = "가짜찌개") -> RawRecipe:
    """재료 행마다 역할(주재료 · 부재료 · 양념)을 단다."""
    return RawRecipe(
        recipe_id="1",
        name=name,
        ingredients=tuple(RawIngredient(name=n, role=role) for n, role in lines),
    )


# ── 정리 표 키 ────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("돼기고기", "돼기고기"),
        (" 다진 마늘 ", "다진마늘"),  # 공백
        ("[쇠고기양념] 간장", "간장"),  # 섹션 머리는 재료가 아니다
        ("[절임용 소금물] 소금", "소금"),
        ("다짐육(돼지고기)", "다짐육돼지고기"),  # 괄호 속은 지우지 않는다 — 알레르기 정보다
        ("소금, 후추", "소금후추"),
    ],
)
def test_정리_표_키(raw: str, key: str) -> None:
    assert ingredient_key(raw) == key


# ── 레시피 → 행 ───────────────────────────────────────────────────────
def test_다_정리된_레시피는_해석된_행이_된다() -> None:
    row = build_recipe_row(recipe("돼기고기", "두부", "간장", "양파"), TABLE, now=NOW)

    assert row.resolved is True
    assert row.source == "mafra_recipe"
    assert row.menu_key == "가짜찌개"
    assert row.display_name == "가짜찌개"
    assert row.synced_at == NOW
    # 오타가 정리된 이름으로 실린다
    assert row.ingredients == ("돼지고기", "두부", "간장", "양파")
    assert {5, 10} <= row.allergen_codes


def test_오타_재료는_정리_표로_알레르기에_걸린다() -> None:
    # "돼기고기" 는 사전이 못 읽는다 — 정리 표가 없으면 돼지고기 알레르기 아이에게 통과한다
    row = build_recipe_row(recipe("돼기고기", "양파"), TABLE, now=NOW)
    pork = [SafetyEntry(kind="allergy", label="돼지고기", status="active")]

    verdict = filter_food_safety([row], pork, "toddler", months=24)

    assert verdict.blocked == (row,)


def test_부위명은_정리_표의_코드로_걸린다() -> None:
    row = build_recipe_row(recipe("안심", "양파"), TABLE, now=NOW)

    assert 16 in row.allergen_codes
    assert row.ingredients == ("쇠고기", "양파")


def test_사전이_모르는_이름은_정리_표의_코드로만_걸린다() -> None:
    row = build_recipe_row(recipe("감동젓", "양파"), TABLE, now=NOW)
    shrimp = [SafetyEntry(kind="allergy", label="새우", status="active")]

    assert row.ingredients == ("감동젓", "양파")
    assert 9 in row.allergen_codes
    assert filter_food_safety([row], shrimp, "toddler", months=24).blocked == (row,)


def test_메뉴명의_코드도_합친다() -> None:
    # 재료 + 메뉴명 사전의 합집합 (own_table §1)
    row = build_recipe_row(recipe("양파", name="가짜우유죽"), TABLE, now=NOW)

    assert 2 in row.allergen_codes


def test_섹션_머리가_붙은_재료도_찾는다() -> None:
    row = build_recipe_row(recipe("[쇠고기양념] 간장", "양파"), TABLE, now=NOW)

    assert row.resolved is True
    assert row.ingredients == ("간장", "양파")


def test_묶음_표기는_나눈_이름으로_싣는다() -> None:
    row = build_recipe_row(recipe("소금, 후추", "양파"), TABLE, now=NOW)

    assert row.ingredients == ("소금", "후추", "양파")


def test_음식이_아닌_재료는_빼고_해석을_막지_않는다() -> None:
    row = build_recipe_row(recipe("두부", "은박컵"), TABLE, now=NOW)

    assert row.resolved is True
    assert row.ingredients == ("두부",)


# ── 식품군 · 주재료 ───────────────────────────────────────────────────
def test_주재료_식품군의_합집합이_행의_식품군이_된다() -> None:
    row = build_recipe_row(
        recipe_with_roles(
            ("돼기고기", "주재료"), ("두부", "주재료"), ("양파", "부재료"), ("간장", "양념")
        ),
        TABLE,
        now=NOW,
    )

    # 부재료 · 양념은 세지 않는다 — 양파(채소)가 빠진다(#420: 주재료만)
    assert row.food_groups == frozenset({"meat_fish_egg_legumes"})


def test_주재료_하나라도_식품군을_안_정했으면_비어_있다() -> None:
    # 간장은 식품군 칸이 비어 있다(아직 안 정함). 일부만 적으면 빠진 식품군이 "0일" 이 된다
    row = build_recipe_row(
        recipe_with_roles(("두부", "주재료"), ("간장", "주재료")), TABLE, now=NOW
    )

    assert row.resolved is True
    assert row.food_groups == frozenset()


def test_식품군이_없는_재료는_none_으로_정하고_식품군에는_넣지_않는다() -> None:
    parsed = table(
        entry("두부", codes=[5], food_group="meat_fish_egg_legumes"),
        entry("소금", food_group="none"),
    )

    row = build_recipe_row(
        recipe_with_roles(("두부", "주재료"), ("소금", "주재료")), parsed, now=NOW
    )

    assert row.food_groups == frozenset({"meat_fish_egg_legumes"})


def test_표에_없는_주재료가_있으면_식품군은_비어_있다() -> None:
    row = build_recipe_row(
        recipe_with_roles(("양파", "주재료"), ("처음보는재료", "주재료")), TABLE, now=NOW
    )

    assert row.resolved is False
    assert row.food_groups == frozenset()


def test_이름_없는_주재료가_있으면_식품군은_비어_있다() -> None:
    row = build_recipe_row(recipe_with_roles(("양파", "주재료"), (None, "주재료")), TABLE, now=NOW)

    assert row.food_groups == frozenset()


def test_음식이_아닌_주재료는_건너뛰고_식품군을_막지_않는다() -> None:
    parsed = table(
        entry("은박컵", [], status="not_food", food_group="grains"),
        entry("양파", food_group="vegetables"),
    )

    row = build_recipe_row(
        recipe_with_roles(("은박컵", "주재료"), ("양파", "주재료")), parsed, now=NOW
    )

    assert row.food_groups == frozenset({"vegetables"})


def test_역할이_없으면_식품군도_비어_있다() -> None:
    # 주재료를 모르면 무엇이 주인지 모른다 — 풀은 줄이지 않고 식품군만 빈다
    row = build_recipe_row(recipe("돼기고기", "양파"), TABLE, now=NOW)

    assert row.food_groups == frozenset()


def test_정해지지_않은_식품군_값은_읽지_않는다() -> None:
    with pytest.raises(ValueError, match="nothing"):
        table(entry("소금", food_group="nothing"))


def test_주재료_역할의_재료만_주재료로_간다() -> None:
    row = build_recipe_row(
        recipe_with_roles(
            ("돼기고기", "주재료"), ("두부", "주재료"), ("양파", "부재료"), ("간장", "양념")
        ),
        TABLE,
        now=NOW,
    )

    # 정리한 이름으로 간다(오타가 아니라 돼지고기)
    assert row.main_ingredients == ("돼지고기", "두부")
    assert row.ingredients == ("돼지고기", "두부", "양파", "간장")


def test_역할이_없으면_주재료는_비어_있다() -> None:
    # 칸이 비면 풀을 줄이지 않고 "같은 주재료 2개" 제약만 건너뛴다(후보 풀 쪽)
    row = build_recipe_row(recipe("돼기고기", "두부"), TABLE, now=NOW)

    assert row.main_ingredients == ()


def test_묶음_표기_주재료는_나눈_이름으로_싣고_중복은_한_번이다() -> None:
    row = build_recipe_row(
        recipe_with_roles(("소금, 후추", "주재료"), ("소금, 후추", "주재료")), TABLE, now=NOW
    )

    assert row.main_ingredients == ("소금", "후추")


# ── 정리 못 한 재료가 있으면 후보 풀에 들어가지 않는다 ─────────────────
@pytest.mark.parametrize(
    ("names", "why"),
    [
        (("두부", "처음보는재료"), "표에 없는 재료"),
        (("두부", "대파"), "검수 전(draft) 재료"),
        (("두부", "육수"), "구성을 알 수 없는 재료(unknown)"),
        (("두부", None), "이름이 빈 재료"),
        (("[양념장]",), "섹션 머리만 있는 재료"),
        ((), "재료가 하나도 없음"),
    ],
)
def test_정리_못_한_재료가_있으면_해석하지_않는다(names: tuple[str | None, ...], why: str) -> None:
    row = build_recipe_row(recipe(*names), TABLE, now=NOW)

    assert row.resolved is False, why


def test_해석_안_된_레시피는_후보_풀에_들어가지_않는다() -> None:
    # 후보 풀은 recipe_catalog 의 resolved 행만 쓴다(#326 리뷰). 레시피 표의 포트는 파트 2 에서
    # 만들어서, 여기서는 행의 resolved 와 필터 판정을 본다
    done = build_recipe_row(recipe("두부", "양파", name="가짜조림"), TABLE, now=NOW)
    pending = build_recipe_row(recipe("두부", "대파", name="가짜국"), TABLE, now=NOW)

    pool = [row for row in (done, pending) if row.resolved]
    verdict = filter_food_safety([done, pending], [], "toddler", months=24)

    assert pool == [done]
    assert verdict.passed == (done,)
    assert verdict.unchecked == (pending,)  # 안전한 것으로 보지 않는다


def test_해석_안_된_레시피도_아는_재료와_원문을_남겨_필터가_훑는다() -> None:
    # 후보에는 못 들어가지만, 쓰이는 곳이 생겨도 알레르기를 덜 보지 않게 원문까지 남긴다
    row = build_recipe_row(recipe("돼기고기", "처음보는재료"), TABLE, now=NOW)

    assert row.resolved is False
    assert row.ingredients == ("돼지고기", "처음보는재료")
    assert 10 in row.allergen_codes


# ── 영양 · 단계 ───────────────────────────────────────────────────────
def test_영양DB_에_같은_이름이_있으면_영양을_싣는다() -> None:
    facts = NutritionFacts(
        food_code="D1",
        name="가짜찌개",
        category="",
        serving_g=200.0,
        per_100g={"energy": 50.0, "calcium": 20.0},
        source_version="mfds_nutri_db",
    )

    row = build_recipe_row(recipe("두부"), TABLE, now=NOW, nutrition=facts)

    assert row.nutrients == {"energy": 50.0, "calcium": 20.0}
    assert (row.food_code, row.serving_g) == ("D1", 200.0)


def test_영양을_못_찾으면_비워_둔다() -> None:
    # 영양 판정에서는 unresolved 로 센다 — 0 으로 넣지 않는다
    row = build_recipe_row(recipe("두부"), TABLE, now=NOW)

    assert row.nutrients == {}
    assert row.serving_g is None


def test_단계는_사람이_태깅한_값만_쓰고_없으면_비운다() -> None:
    # 비면 toddler 로 본다(MenuCatalogRow.stage_min 주석). 코드가 단계를 짐작하지 않는다
    assert build_recipe_row(recipe("두부"), TABLE, now=NOW).stage_min is None
    tagged = build_recipe_row(recipe("두부"), TABLE, now=NOW, stage_min="preschool")
    assert tagged.stage_min == "preschool"


# ── 정리 표 읽기 ──────────────────────────────────────────────────────
def test_정리_표는_정리한_키로_찾는다() -> None:
    parsed = table(entry(" 다진 마늘 ", ["다진마늘"]))

    assert set(parsed) == {"다진마늘"}
    assert parsed["다진마늘"].names == ("다진마늘",)


@pytest.mark.parametrize(
    ("bad", "why"),
    [
        (entry("두부", codes=[20]), "19종 밖 코드"),
        (entry("두부", status="maybe"), "모르는 status"),
        (entry("두부", []), "ok 인데 이름이 없음"),
        ({**entry("두부"), "extra": 1}, "정해지지 않은 칸"),
        (entry("두부", food_group="vegetable"), "여섯 이름 밖 식품군"),
        ({k: v for k, v in entry("두부").items() if k != "status"}, "status 없음"),
        (entry("   "), "빈 원문"),
    ],
)
def test_모양이_틀린_정리_표는_읽지_않는다(bad: dict[str, Any], why: str) -> None:
    with pytest.raises(ValueError):
        table(bad)


def test_같은_키가_두_번_나오면_읽지_않는다() -> None:
    # 정리하면 같은 키 — 어느 쪽 검수를 따를지 모른다
    with pytest.raises(ValueError):
        table(entry("다진마늘"), entry("다진 마늘"))


def test_정리_표에는_출처가_있어야_한다() -> None:
    with pytest.raises(ValueError):
        parse_ingredient_table({"ingredients": [entry("두부")]})


# ── recipe_catalog 포트 (Task 10, #326 리뷰에서 미룬 것) ─────────────────
async def test_후보_풀은_레시피_표에서_읽는다(ports) -> None:
    rows = await ports.recipes.all_resolved()

    assert all(row.source in RECIPE_SOURCES for row in rows)


async def test_메뉴_카탈로그에는_all_resolved_가_없다(ports) -> None:
    assert not hasattr(ports.catalog, "all_resolved")


async def test_레시피_표는_source_와_source_id_로_갱신한다(ports) -> None:
    await ports.recipes.put(recipe_row(source_id="12", name="된장찌개"))
    await ports.recipes.put(recipe_row(source_id="12", name="된장찌개(개정)"))

    rows = await ports.recipes.all_resolved()
    assert len(rows) == 1 and rows[0].display_name == "된장찌개(개정)"


async def test_메뉴_카탈로그는_레시피_행을_받지_않는다(ports) -> None:
    with pytest.raises(ValueError, match="recipe_catalog"):
        await ports.catalog.put(recipe_row(source_id="12", name="된장찌개"))
