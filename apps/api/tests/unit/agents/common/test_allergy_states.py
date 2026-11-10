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


def test_괜찮았던_식품은_정규화한_이름이_같으면_묻지_않는다():
    """05 확인 시트는 사전검사 항목 이름을 그대로 저장한다 — 공백 · 꼬리말만 다른 것도 같다."""
    assert states(["우유"], [_row("우유", "none")]) == {"우유": "known"}
    assert states(["우유"], [_row(" 우유 ", "none")]) == {"우유": "known"}
    assert states(["우유"], [_row("우유 알레르기", "none")]) == {"우유": "known"}


def test_괜찮았던_식품은_다른_항목을_덮지_않는다():
    """이름 사전은 하위 식품 · 묶음을 같은 코드로 읽는다 — none 을 넓게 맞추면 덜 묻게 된다."""
    assert states(["호두"], [_row("견과류", "none")]) == {"호두": None}
    assert states(
        ["우유", "대두", "조개류"],
        [_row("치즈", "none"), _row("간장", "none"), _row("굴", "none")],
    ) == {"우유": None, "대두": None, "조개류": None}


def test_이름이_비는_괜찮았던_식품은_아무것도_덮지_않는다():
    assert states(["우유", "!!"], [_row("!!", "none")]) == {"우유": None, "!!": None}


def test_내린_기록만_있으면_모른다():
    """내린 기록은 "지금은 아니다" 일 뿐 "먹어봤고 괜찮았다" 가 아니다 — 다시 묻는다."""
    assert states(["밀"], [_row("밀", "retracted")]) == {"밀": None}
    assert states(["호두", "잣"], [_row("견과류", "retracted")]) == {"호두": None, "잣": None}


def test_내린_기록이_있어도_괜찮았던_식품이면_묻지_않는다():
    """내린 뒤 다시 물어 "괜찮았어요" 로 답한 식품."""
    assert states(["우유"], [_row("우유", "retracted"), _row("우유", "none")]) == {"우유": "known"}


def test_알레르기가_괜찮았던_식품보다_앞선다():
    assert states(["호두"], [_row("호두", "none"), _row("견과류")]) == {"호두": "active"}


def test_환경_알레르기도_active_면_막는다():
    """Activity 는 19종 밖 이름(꽃가루)을 등록된 이름 그대로 싣는다."""
    assert states(["꽃가루"], [_row("꽃가루", kind="environmental")]) == {"꽃가루": "active"}


def test_다른_항목은_걸리지_않는다():
    assert states(["땅콩"], [_row("우유")]) == {"땅콩": None}
