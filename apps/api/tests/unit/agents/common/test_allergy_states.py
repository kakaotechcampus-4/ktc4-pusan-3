"""추천 allergens ↔ health_safety 행 (#249 · types.ts Precheck). 판정은 코드다 (루트 §3)."""

from app.agents.common.allergy import allergen_states, shared_book


def _row(label, status="active", kind="allergy"):
    return (kind, label, status)


def states(allergens, rows):
    return allergen_states(allergens, rows, shared_book())


def test_행이_없으면_모른다():
    assert states(["난류"], []) == {"난류": None}


def test_다른_이름으로_적어도_같은_항목이다():
    """보호자는 "계란" 으로 적고 추천은 정식 명칭 "난류" 로 온다."""
    assert states(["난류"], [_row("계란")]) == {"난류": "active"}


def test_active_가_retracted_보다_앞선다():
    assert states(["우유"], [_row("우유", "retracted"), _row("우유")]) == {"우유": "active"}


def test_none_과_retracted_는_확인된_것이다():
    assert states(["우유"], [_row("우유", "none")]) == {"우유": "known"}
    assert states(["밀"], [_row("밀", "retracted")]) == {"밀": "known"}


def test_환경_알레르기도_active_면_막는다():
    """Activity 는 19종 밖 이름(꽃가루)을 등록된 이름 그대로 싣는다."""
    assert states(["꽃가루"], [_row("꽃가루", kind="environmental")]) == {"꽃가루": "active"}


def test_다른_항목은_걸리지_않는다():
    assert states(["땅콩"], [_row("우유")]) == {"땅콩": None}
