"""식단 추천 요청 글 읽기 — 모델이 적은 `requested_food` 인정과 조건 낱말 (#740)."""

import pytest

from app.agents.food.meal_request import condition_stems, names_mention, said_in_food_request


@pytest.mark.parametrize(
    ("requested", "said"),
    [
        ("파프리카", "파프리카 들어간 반찬 추천해줘"),
        ("파프리카 들어간 반찬", "파프리카 들어간 반찬 추천해줘"),
        ("태국음식", "태국음식 추천해줘"),
        ("오이", "오이 들어간 음식 추천해줘"),
    ],
)
def test_보호자가_정해_말한_조건은_인정한다(requested: str, said: str) -> None:
    assert said_in_food_request(requested, [said])


@pytest.mark.parametrize(
    "requested", ["반찬", "음식 추천", "저녁", "간식", "오늘 저녁 메뉴", "", None]
)
def test_일반_낱말만_적었으면_막연한_요청이다(requested: str | None) -> None:
    assert not said_in_food_request(requested, ["저녁 반찬 추천해줘"])


def test_모델이_지어낸_조건은_인정하지_않는다() -> None:
    assert not said_in_food_request("파프리카", ["저녁 뭐 먹일까?"])
    assert not said_in_food_request("태국음식", ["오이 들어간 음식 추천해줘"])


def test_조건_낱말은_일반_낱말을_뺀다() -> None:
    assert condition_stems("파프리카 들어간 반찬") == ["파프리카"]
    assert condition_stems("반찬") == []


def test_이름과_재료를_합쳐_조건_낱말이_모두_있는지_본다() -> None:
    assert names_mention(["파프리카볶음", "파프리카", "올리브유"], ["파프리카"])
    assert names_mention(["잡채", "당면", "파프리카"], ["파프리카"])
    assert not names_mention(["두부조림", "두부", "간장"], ["파프리카"])
    assert not names_mention(["파프리카볶음"], [])  # 조건이 없으면 맞다고 하지 않는다
