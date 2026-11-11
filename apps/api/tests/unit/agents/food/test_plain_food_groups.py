"""식품군 출현용 단일 식품 표 (`reference/plain_food_groups.yaml`).

밥 · 바나나 · 김치처럼 보호자가 흔히 적는 단일 식품은 영양DB 이름과 안 맞아 카탈로그 행에
식품군이 없는 일이 있다. 그 이름만 모은 표를 식품군 **출현**에만 본다 — 영양 합계 · 알레르기
판정에는 쓰지 않는다.
"""

import pytest

from app.agents.common.reference import load_reference
from app.agents.food.food_groups import (
    PLAIN_FOOD_GROUPS,
    appearance_food_groups,
    parse_plain_food_groups,
    plain_food_groups,
)
from app.rules.menu_key import normalize_menu_key


def _data(*menus: dict, **extra) -> dict:
    return {
        "source": "테스트",
        "version": "t",
        "fetched_at": "2026-11-11",
        "menus": list(menus),
        **extra,
    }


def test_흔한_단일_식품은_표에_있다():
    table = plain_food_groups()
    assert table["밥"] == {"grains"}
    assert table["고구마"] == {"grains"}
    assert table["바나나"] == {"fruits"}
    assert table["김치"] == {"vegetables"}
    assert table["요거트"] == {"dairy"}
    assert table["계란찜"] == {"meat_fish_egg_legumes"}
    assert table["두부"] == {"meat_fish_egg_legumes"}


def test_섞인_음식과_카탈로그가_아는_이름은_표에_없다():
    table = plain_food_groups()
    for name in (
        "잡곡밥",
        "떡",
        "빵",
        "김밥",
        "볶음밥",
        "불고기",
        "돈까스",
        "된장국",
        "미역국",
        "닭죽",
        "시리얼",
        "토마토",
        "순두부",
        "버섯",
        "김",
        "쌀밥",
        "시금치나물",
        "된장찌개",
        "우유",
        "물",
    ):
        assert normalize_menu_key(name) not in table, name


def test_표는_다섯_식품군만_쓴다():
    assert all(groups and groups <= PLAIN_FOOD_GROUPS for groups in plain_food_groups().values())


def test_표_이름은_메뉴_키로_찾는다():
    raw = load_reference("plain_food_groups.yaml")
    table = plain_food_groups()
    assert all(normalize_menu_key(row["menu"]) in table for row in raw["menus"])


def test_유지_당류는_받지_않는다():
    with pytest.raises(ValueError):
        parse_plain_food_groups(_data({"menu": "버터", "groups": ["fats_sugars"]}))


def test_정해지지_않은_칸이나_같은_이름_두_번은_받지_않는다():
    with pytest.raises(ValueError):
        parse_plain_food_groups(_data(categories=[]))
    with pytest.raises(ValueError):
        parse_plain_food_groups(
            _data({"menu": "사과", "groups": ["fruits"]}, {"menu": "사과 ", "groups": ["fruits"]})
        )
    with pytest.raises(ValueError):
        parse_plain_food_groups(_data({"menu": "사과", "groups": []}))


def test_카탈로그_식품군이_있으면_그대로_쓴다():
    assert appearance_food_groups("바나나", frozenset({"vegetables"})) == {"vegetables"}


def test_카탈로그_식품군이_비면_표를_본다():
    assert appearance_food_groups("바나나", frozenset()) == {"fruits"}
    assert appearance_food_groups("처음보는음식", frozenset()) == frozenset()
