"""`lookup_daycare_menu` — 그날 급식 메뉴와 메뉴별 알레르기 판정.

급식 조회는 `menu_catalog` 만 본다. 레시피 재료로 "없음" 을 만들지 않는다(own_table §1-1).
메뉴 · 재료 · 아이 정보는 전부 가짜다.
"""

from datetime import date

import pytest

from app.agents.food.schemas.menu import DaycareMenuArgs, MenuDay
from app.agents.food.store import (
    InMemoryDaycareMeals,
    InMemoryMenuCatalog,
    InMemoryRecipeCatalog,
    SafetyEntry,
)
from app.agents.food.tools.menu import lookup_daycare_menu
from tests.unit.agents.food.support import context, daycare, menu_row

SHELLFISH = [SafetyEntry(kind="allergy", label="조개류", status="active")]
SHRIMP = [SafetyEntry(kind="allergy", label="새우", status="active")]
TWO_YEARS = date(2024, 6, 14)


def menus(result) -> dict[str, str]:
    return {m["menu_key"]: m["allergy"] for meal in result.data["meals"] for m in meal["menus"]}


async def test_재료가_없는_메뉴는_통과가_아니라_확인_못_함이다() -> None:
    # 된장찌개에 조개가 들었는지 모른다 — 영양DB 행에는 재료가 없다
    ctx = context(
        daycare=InMemoryDaycareMeals([daycare("된장찌개", days_ago=0)]),
        catalog=InMemoryMenuCatalog([menu_row("된장찌개", source="mfds_nutri", ingredients=())]),
        safety=SHELLFISH,
    )

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert result.success
    assert menus(result) == {"된장찌개": "unchecked"}


async def test_재료를_아는_메뉴는_알레르기에_안_걸리면_ok_다() -> None:
    ctx = context(
        daycare=InMemoryDaycareMeals([daycare("두부조림", days_ago=0)]),
        catalog=InMemoryMenuCatalog([menu_row("두부조림", ingredients=("두부", "간장"))]),
        safety=SHELLFISH,
    )

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert menus(result) == {"두부조림": "ok"}


async def test_재료를_몰라도_이름에_알레르기_식품이_있으면_막는다() -> None:
    ctx = context(
        daycare=InMemoryDaycareMeals([daycare("새우볶음밥", days_ago=0)]),
        catalog=InMemoryMenuCatalog([menu_row("새우볶음밥", source="mfds_nutri", ingredients=())]),
        safety=SHRIMP,
    )

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert menus(result) == {"새우볶음밥": "blocked"}


async def test_카탈로그에_없는_메뉴도_이름으로_거르고_없으면_확인_못_함이다() -> None:
    ctx = context(
        daycare=InMemoryDaycareMeals([daycare("새우튀김", "처음보는국", days_ago=0)]),
        safety=SHRIMP,
    )

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert menus(result) == {"새우튀김": "blocked", "처음보는국": "unchecked"}


async def test_같은_이름_레시피가_있어도_그_재료로_판정하지_않는다() -> None:
    # 레시피 재료가 급식 메뉴를 "조개 있음" 으로도 "조개 없음" 으로도 덮지 않는다
    with_shellfish = menu_row("된장찌개", source="mafra_recipe", ingredients=("바지락", "두부"))
    without = menu_row("미역국", source="mafra_recipe", ingredients=("미역", "참기름"))
    ctx = context(
        daycare=InMemoryDaycareMeals([daycare("된장찌개", "미역국", days_ago=0)]),
        recipes=InMemoryRecipeCatalog([with_shellfish, without]),
        catalog=InMemoryMenuCatalog(
            [
                menu_row("된장찌개", source="mfds_nutri", ingredients=()),
                menu_row("미역국", source="mfds_nutri", ingredients=()),
            ]
        ),
        safety=SHELLFISH,
    )

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert menus(result) == {"된장찌개": "unchecked", "미역국": "unchecked"}


async def test_안전_정보를_못_읽었으면_전부_확인_못_함이다() -> None:
    ctx = context(
        daycare=InMemoryDaycareMeals([daycare("두부조림", days_ago=0)]),
        catalog=InMemoryMenuCatalog([menu_row("두부조림", ingredients=("두부",))]),
        safety=None,
    )

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert result.success
    assert result.data["allergy_checked"] is False
    assert menus(result) == {"두부조림": "unchecked"}


async def test_알레르기를_읽었으면_allergy_checked_다() -> None:
    ctx = context(daycare=InMemoryDaycareMeals([daycare("두부조림", days_ago=0)]), safety=())

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert result.data["allergy_checked"] is True


async def test_결과에_알레르기_이름이나_걸린_사유를_싣지_않는다() -> None:
    ctx = context(
        daycare=InMemoryDaycareMeals([daycare("새우볶음밥", days_ago=0)]),
        safety=SHRIMP,
    )

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    text = str(result.data)
    assert "새우" not in text.replace("새우볶음밥", "")  # 사유 · 알레르기 항목 이름이 없다
    assert "hits" not in text and "allergens" not in text


async def test_급식이_없는_날은_메뉴_없음이고_실패가_아니다() -> None:
    ctx = context(daycare=InMemoryDaycareMeals([daycare("두부조림", days_ago=3)]), safety=())

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert result.success
    assert result.data["meals"] == []


@pytest.mark.parametrize(
    ("day_label", "days_ago"),
    [(MenuDay.YESTERDAY, 1), (MenuDay.TODAY, 0), (MenuDay.TOMORROW, -1)],
)
async def test_날짜_라벨은_코드가_날짜로_푼다(day_label: MenuDay, days_ago: int) -> None:
    rows = [
        daycare("어제국", days_ago=1),
        daycare("오늘국", days_ago=0),
        daycare("내일국", days_ago=-1),
    ]
    ctx = context(daycare=InMemoryDaycareMeals(rows), safety=())

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs(day=day_label))

    expected = {1: "어제국", 0: "오늘국", -1: "내일국"}[days_ago]
    assert list(menus(result)) == [expected]


async def test_끼니마다_출처와_보호자_확인_여부를_싣고_끼니_순서로_준다() -> None:
    afternoon = daycare("오후간식", days_ago=0, slot="snack_pm")
    lunch = daycare("점심국", days_ago=0, slot="lunch")
    ctx = context(daycare=InMemoryDaycareMeals([afternoon, lunch]), safety=())

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert [m["slot"] for m in result.data["meals"]] == ["lunch", "snack_pm"]
    assert all(m["origin"] == "ocr" for m in result.data["meals"])
    assert all(m["caregiver_checked"] is False for m in result.data["meals"])


async def test_떡_급식은_막지_않고_주의_문구도_싣지_않는다() -> None:
    ctx = context(
        daycare=InMemoryDaycareMeals([daycare("인절미", days_ago=0)]),
        catalog=InMemoryMenuCatalog([menu_row("인절미", ingredients=("찹쌀", "콩가루"))]),
        safety=(),
        birth=TWO_YEARS,
    )

    result = await lookup_daycare_menu(ctx, DaycareMenuArgs())

    assert menus(result) == {"인절미": "ok"}
    assert "caution" not in str(result.data)
