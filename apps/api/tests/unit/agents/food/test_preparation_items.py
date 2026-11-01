"""`preparation_items` — 레시피 주재료에서 일정 준비물로 보일 이름만 남긴다 (#474).

입력은 `main_ingredients` 뿐이다. 제외는 정리 표의 이름과 **정확히** 같을 때만이다 — 이름
일부가 겹친다고 빼면 밀가루 · 엿기름 · 우유 · 유부 같은 핵심 재료가 같이 빠진다.
"""

import pytest

from app.agents.food.preparation_items import preparation_items

EXCLUDED = {
    "물": ("물",),
    "소금": ("소금", "굵은소금", "맛소금", "꽃소금", "고운소금"),
    "설탕": ("설탕", "황설탕", "흑설탕", "흰설탕"),
    "기름": (
        "기름",
        "식용유",
        "식물성기름",
        "튀김기름",
        "부침유",
        "샐러드오일",
        "포도씨유",
        "참빛고운포도씨유",
        "올리브유",
        "올리브오일",
        "참기름",
        "들기름",
        "고추기름",
        "고춧기름",
    ),
    "향신 가루": (
        "후추",
        "후춧가루",
        "통후추",
        "흰후추",
        "흰후춧가루",
        "고춧가루",
        "굵은고춧가루",
        "고운고춧가루",
        "깨소금",
        "파슬리가루",
        "계핏가루",
        "겨자가루",
        "산초가루",
    ),
}

# 이름에 `가루` · `기름` · `유` 가 들어 있어도 핵심 재료일 수 있어서 남긴다
KEPT = (
    "밀가루",
    "찹쌀가루",
    "쌀가루",
    "멥쌀가루",
    "부침가루",
    "튀김가루",
    "빵가루",
    "녹말가루",
    "카레가루",
    "콩가루",
    "날콩가루",
    "들깨가루",
    "들깻가루",
    "잣가루",
    "아몬드가루",
    "땅콩가루",
    "건새우가루",
    "치즈가루",
    "엿기름",
    "우유",
    "유부",
)


def test_주재료_순서를_지키고_중복은_한_번만_남긴다() -> None:
    assert preparation_items(("두부", "시금치", "두부", "당근", "시금치")) == (
        "두부",
        "시금치",
        "당근",
    )


def test_앞뒤_공백을_걷고_빈_이름은_버린다() -> None:
    assert preparation_items((" 두부 ", "", "   ", "시금치", "두부")) == ("두부", "시금치")


@pytest.mark.parametrize(
    "name", [name for names in EXCLUDED.values() for name in names], ids=lambda name: name
)
def test_상비_재료는_정확한_이름이_같을_때_뺀다(name: str) -> None:
    assert preparation_items(("두부", name)) == ("두부",)


def test_공백이_붙은_상비_재료도_정리한_이름으로_뺀다() -> None:
    assert preparation_items((" 소금 ", "두부", "참기름\t")) == ("두부",)


@pytest.mark.parametrize("name", KEPT)
def test_이름에_가루_기름_유가_들어도_정확히_다르면_남긴다(name: str) -> None:
    assert preparation_items((name, "소금")) == (name,)


def test_제외한_자리에_빈_문자열을_남기지_않는다() -> None:
    result = preparation_items(("소금", "두부", "설탕", "", "식용유"))

    assert result == ("두부",)
    assert "" not in result


def test_주재료가_비어_있으면_빈_튜플이다() -> None:
    assert preparation_items(()) == ()


def test_주재료가_모두_제외되면_빈_튜플이다() -> None:
    assert preparation_items(("물", "소금", "참기름", "후춧가루")) == ()


def test_돌려주는_값은_튜플이다() -> None:
    assert isinstance(preparation_items(["두부"]), tuple)
