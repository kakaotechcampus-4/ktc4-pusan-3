"""메뉴명 → `menu_key` 정규화. 정본은 `food_agent_own_table.md` §1.
공백 · 괄호 · 급식 알레르기 번호를 지운다.

떼는 것은 읽는 것이 아니라 지우는 것이다 — 급식표의 알레르기 번호는 판정에 쓰지 않는다.
"""

import pytest

from app.rules.menu_key import normalize_menu_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("된장찌개", "된장찌개"),
        ("된장 찌개", "된장찌개"),  # 공백
        (" 미역국 ", "미역국"),
        (
            "카레라이스(2.5.6)",
            "카레라이스",
        ),  # 급식표 알레르기 번호 — 안 떼면 카탈로그에서 못 찾는다
        ("닭갈비(5.6.15.)", "닭갈비"),
        ("우유 2,5", "우유"),
        ("우유②", "우유"),
        ("우유(200ml)", "우유"),  # 괄호
        ("두부조림[국산]", "두부조림"),
        ("3색나물", "3색나물"),  # 이름 속 숫자는 번호가 아니다
        ("ＫＩＭＣＨＩ볶음밥", "kimchi볶음밥"),  # 전각 영문, 소문자
    ],
)
def test_menu_key_정규화(raw: str, expected: str) -> None:
    assert normalize_menu_key(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "(2.5)", "①②", "---"])
def test_이름이_남지_않으면_빈_문자열(raw: str) -> None:
    # 빈 키는 호출부가 unresolved 로 둔다 — 빈 이름으로 외부를 부르지 않는다
    assert normalize_menu_key(raw) == ""


def test_같은_이름은_표기가_달라도_같은_키() -> None:
    assert normalize_menu_key("김치 볶음밥 (1.5)") == normalize_menu_key("김치볶음밥")


def test_다른_이름은_다른_키로_남는다() -> None:
    # 비슷한 이름으로 합치지 않는다 — 된장찌개와 된장찌개_두부는 다른 메뉴다
    assert normalize_menu_key("된장찌개") != normalize_menu_key("된장찌개 두부")
