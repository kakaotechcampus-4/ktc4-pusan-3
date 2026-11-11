"""급식표 저장 키에 괄호 안 음식 이름이 실린다 (#908 — `photo_run.meal_row` → `meal_menu_keys`).

"떡(꿀설기)" 의 키가 "떡" 하나면 급식 알레르기 · 월령 판정(`agents/food/tools/menu.py` 의
`lookup_daycare_menu`)이 "꿀" 을 못 봐서 8개월 아기에게 "차단" 이 아니라 "확인 못 함" 이 나온다.
판정은 그 tool 과 같은 길로 부른다 — 카탈로그에 없는 키는 이름만 있는 행(`_unknown_menu` 모양)이다.
메뉴 이름은 전부 지어낸 것이다.
"""

from datetime import UTC, date, datetime

from app.agents.common.age import life_stage
from app.agents.common.food_safety import filter_food_safety
from app.agents.food.store.ports import MenuCatalogRow, SafetyEntry
from app.api.runs import photo_run
from app.domains.daycare_meal.models import DaycareSlot
from app.rules.menu_key import meal_menu_keys

TODAY = date(2026, 11, 11)
_NOW = datetime(2026, 11, 11, tzinfo=UTC)


def _verdict(keys: tuple[str, ...], entries: list[SafetyEntry], birth_date: date) -> dict[str, str]:
    """`lookup_daycare_menu` 처럼 카탈로그에 없는 키를 이름만으로 판정한다."""
    rows = [
        MenuCatalogRow(
            menu_key=key, display_name=key, source="manual", resolved=False, synced_at=_NOW
        )
        for key in keys
    ]
    life = life_stage(birth_date, TODAY)
    verdict = filter_food_safety(rows, entries, life.stage, months=life.months, purpose="daycare")
    allergy = dict.fromkeys(keys, "unchecked")
    allergy.update({row.menu_key: "ok" for row in verdict.passed})
    allergy.update({row.menu_key: "blocked" for row in verdict.blocked})
    return allergy


def test_저장할_급식_행에_괄호_안_음식_이름이_키로_실린다() -> None:
    slot = photo_run.MealSlot(serve_date=TODAY, slot=DaycareSlot.LUNCH)

    row = photo_run.meal_row(slot, title="떡(꿀설기), 우유(200ml), 닭고기(국내산)", day=None)

    assert row is not None
    assert row.menu_keys == ("떡", "꿀설기", "우유", "닭고기")


def test_8개월_아기에게_꿀이_든_떡은_차단이다() -> None:
    allergy = _verdict(meal_menu_keys("떡(꿀설기)"), [], birth_date=date(2026, 3, 1))

    assert allergy["꿀설기"] == "blocked"


def test_우유_알레르기_아이에게_우유가_든_빵은_차단이다() -> None:
    entries = [SafetyEntry(kind="allergy", label="우유", status="active")]

    allergy = _verdict(meal_menu_keys("빵(우유카스텔라)"), entries, birth_date=date(2023, 1, 1))

    assert allergy["우유카스텔라"] == "blocked"
