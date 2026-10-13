"""영양DB 행을 `menu_key` 별로 묶는 함수 — 배치(FT-19)가 쓴다. 정본은
`app/core/nutrition_pick.py`, 호출 규칙은 `docs/agents/shared/외부연결_계획.md` §2.
"""

from app.core.nutrition_pick import nutrition_facts_by_key
from tests.unit.agents.food.test_nutrition_source import row  # 기존 픽스처 재사용


def test_이름별로_묶어_각_이름의_행을_고른다() -> None:
    rows = [row("된장찌개", code="A"), row("김치찌개", code="B")]

    facts = nutrition_facts_by_key(rows)

    assert sorted(facts) == ["김치찌개", "된장찌개"]
    assert facts["된장찌개"].food_code == "A"


def test_괄호가_붙은_이름은_따로_묶인다() -> None:
    # 요청 중 조회에서 "김치찌개" 로 찾으면 "김치찌개(참치)" 는 걸러진다(_usable).
    # 배치도 같은 키로 묶어야 같은 메뉴가 경로에 따라 다른 값을 갖지 않는다
    facts = nutrition_facts_by_key([row("김치찌개(참치)", code="C")])

    assert sorted(facts) == ["김치찌개참치"]


def test_쓸_수_없는_행만_있는_이름은_키가_없다() -> None:
    # 상용제품 · 핵심 영양 칸이 빈 행은 pick_nutrition 이 고르지 않는다
    assert nutrition_facts_by_key([row("백미밥", cls="대표")]) == {}
