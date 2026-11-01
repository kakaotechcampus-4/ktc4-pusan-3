"""채팅으로 저장하지 않는 알레르기 · 만성질환 이름 (#639)."""

import pytest

from app.rules.safety_term import mentions_safety


@pytest.mark.parametrize(
    "text",
    [
        "꽃가루 알레르기",
        "우유 알러지",
        "알레르기 반응",
        "아토피",
        "아토피 피부염",
        "천식",
        "천 식",
    ],
)
def test_알레르기_만성질환_이름을_찾는다(text: str) -> None:
    assert mentions_safety(text)


@pytest.mark.parametrize("text", ["기침", "두드러기", "입 주변 빨개짐", "재채기", "열", ""])
def test_증상은_걸지_않는다(text: str) -> None:
    assert not mentions_safety(text)
