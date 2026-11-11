"""홈 "오늘" 의 급식 줄 — DB 없이 행 → 줄만 본다.

조회 · 권한 · 일정과의 순서는 `tests/integration/api/test_home.py` 가 건다.
메뉴는 전부 지어낸 값이다 (루트 CLAUDE.md §9).
"""

from datetime import date

from app.api.v1.routers.home import today_meals
from app.domains.daycare_meal.models import DaycareMeal, DaycareOrigin, DaycareSlot

_DAY = date(2026, 11, 11)


def _row(slot, menus, origin=DaycareOrigin.OCR) -> DaycareMeal:
    return DaycareMeal(serve_date=_DAY, meal_slot=slot, menu_keys=list(menus), origin=origin)


def test_끼니는_하루_순서로_서고_메뉴는_저장된_그대로_잇는다():
    lines = today_meals(
        [
            _row(DaycareSlot.LUNCH, ["잡곡밥", "소고기미역국"]),
            _row(DaycareSlot.SNACK_PM, ["감자"]),
            _row(DaycareSlot.SNACK_AM, ["우유"]),
        ]
    )

    assert [(line.kind, line.title, line.origin) for line in lines] == [
        ("meal", "오전 간식 · 우유", "기관 급식표"),
        ("meal", "점심 · 잡곡밥, 소고기미역국", "기관 급식표"),
        ("meal", "오후 간식 · 감자", "기관 급식표"),
    ]


def test_메뉴가_빈_끼니는_줄을_만들지_않는다():
    # 빈 제목 줄을 세우지 않는다 — 빈 문자열만 남은 메뉴도 빈 끼니다
    assert today_meals([_row(DaycareSlot.LUNCH, []), _row(DaycareSlot.SNACK_AM, ["", " "])]) == []


def test_지역_표준식단은_실제와_다를_수_있다고_적는다():
    # Food 의 급식 조회(`daycare.reference_menu`)와 같은 사실 — 아이가 실제로 먹은 급식이 아니다
    (line,) = today_meals([_row(DaycareSlot.LUNCH, ["카레"], DaycareOrigin.CENTER_STANDARD)])

    assert line.origin == "지역 표준식단 · 실제 급식과 다를 수 있어요"


def test_급식_줄에는_알레르기_판정_문구가_없다():
    # 루트 §2 — 급식은 있는 그대로 보여 주기만 한다. "괜찮아요" · "주의" 같은 판정을 붙이지 않는다
    (line,) = today_meals([_row(DaycareSlot.LUNCH, ["우유", "땅콩조림"])])

    assert line.model_dump() == {
        "kind": "meal",
        "title": "점심 · 우유, 땅콩조림",
        "origin": "기관 급식표",
    }


def test_NULL_과_공백뿐인_키는_건너뛰고_앞뒤_공백은_다듬는다():
    # 한 행의 이상한 키 때문에 홈 전체가 500 이 되면 안 된다 — 홈은 가장 자주 불리는 화면이다
    lines = today_meals(
        [
            _row(DaycareSlot.LUNCH, [None, " 우유 ", "  ", "잡곡밥"]),
            _row(DaycareSlot.SNACK_PM, [None, " "]),
        ]
    )

    assert [line.title for line in lines] == ["점심 · 우유, 잡곡밥"]
