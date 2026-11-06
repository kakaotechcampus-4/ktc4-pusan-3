"""받침에 맞는 조사 — 되묻기 · 안내 문구가 "은(는)" 을 그대로 내지 않는다."""

import pytest

from app.rules.particle import has_final, topic_particle


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("양치", "는"),
        ("밥", "은"),
        ("숟가락 쓰기", "는"),
        ("장난감 정리", "는"),
        ("양치 ", "는"),  # 끝 공백은 받침 판정에 들어가지 않는다
        ("ABC", "은(는)"),  # 한글로 끝나지 않으면 받침을 모른다
        ("", "은(는)"),
    ],
)
def test_받침에_맞는_주제_조사를_고른다(word: str, expected: str) -> None:
    assert topic_particle(word) == expected


def test_받침_유무를_가린다() -> None:
    assert has_final("밥") is True
    assert has_final("양치") is False
    assert has_final("ABC") is None
    assert has_final("") is None
