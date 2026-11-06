"""레시피 재료 문자열 파서 — `matrlInfo` 같은 한 줄 재료 목록을 `RawIngredient` 로 (#510).

🚨 아래 문자열은 전부 **가짜**다. 모양(`▶` 구간 머리 · 인분 머리말 · 괄호 속 쉼표 · 양 표기
두 가지)만 실제 응답을 확인한 기록(2026-10-18)에서 옮겼다.

여기서 지키는 것 셋.
    ① 괄호 안의 쉼표 · 등호는 재료를 쪼개지 않는다
    ② 재료일 수 있는 글은 버리지 않는다 — 이름을 모르는 항목(`name=None`)으로 남겨 그 레시피가
       후보에서 빠지게 한다. 알레르기 판정 입력을 조용히 줄이지 않는다
    ③ 구조가 깨진 문자열(닫히지 않은 괄호 · 내용 없는 구간)은 해석하지 않고 올린다
"""

import pytest

from app.core.mafra_recipe_raw import RawIngredient
from app.core.recipe_materials import split_materials, split_outside_parentheses


# ── 괄호 밖에서만 자르기 ──────────────────────────────────────────────
def test_괄호_밖_쉼표에서만_자른다() -> None:
    text = "오이지(80g), 다시마(1장=5X5cm, 두꺼운 것), 소금"

    assert split_outside_parentheses(text) == (
        "오이지(80g)",
        "다시마(1장=5X5cm, 두꺼운 것)",
        "소금",
    )


def test_중첩_괄호_안의_쉼표도_자르지_않는다() -> None:
    assert split_outside_parentheses("a(b(c,d),e),f") == ("a(b(c,d),e)", "f")


def test_전각_괄호도_괄호로_본다() -> None:
    assert split_outside_parentheses("가（나,다）,라") == ("가（나,다）", "라")


def test_빈_조각을_버리지_않는다() -> None:
    assert split_outside_parentheses("a,,b,") == ("a", "", "b", "")


def test_닫는_괄호만_있으면_깊이를_내리지_않고_계속_자른다() -> None:
    # 짝 없는 ")" 는 이름에 남는다 — 정리 표에 없는 이름이라 그 레시피는 후보에서 빠진다
    assert split_outside_parentheses("a),b") == ("a)", "b")


def test_닫히지_않은_괄호는_올린다() -> None:
    with pytest.raises(ValueError):
        split_outside_parentheses("a(b,c")


def test_구분자를_바꿔_쓸_수_있다() -> None:
    assert split_outside_parentheses("▶a(▶)▶b", "▶") == ("", "a(▶)", "b")


# ── ▶ 구간 머리 · 머리말 ──────────────────────────────────────────────
def test_인분_머리말_뒤_구간_머리가_role_이_된다() -> None:
    text = "(3인분) ▶주재료 : 오이지(80g), 무 ▶양념 : 소금"

    assert split_materials(text) == (
        RawIngredient("오이지", "80g", "주재료"),
        RawIngredient("무", None, "주재료"),
        RawIngredient("소금", None, "양념"),
    )


@pytest.mark.parametrize("label", ["(3인분)", "<2인분 기준>", "[4인분]", "2~3인분", "(1인분 기준)"])
def test_인분_표기_머리말은_재료가_아니라서_버린다(label: str) -> None:
    assert split_materials(f"{label} ▶주재료 : 두부") == (RawIngredient("두부", None, "주재료"),)


def test_구간_머리의_앞뒤_공백은_지운다() -> None:
    assert split_materials("▶ 재료 : 두부") == split_materials("▶재료:두부")
    assert split_materials("▶ 재료 : 두부")[0].role == "재료"


def test_전각_콜론도_구간_머리의_끝으로_본다() -> None:
    assert split_materials("▶주재료：두부") == (RawIngredient("두부", None, "주재료"),)


def test_첫_구간_앞의_인분_표기가_아닌_글은_재료로_읽는다() -> None:
    # 버리거나 이름 없는 재료 하나로 뭉개면 그 글 속 재료를 아무도 검수할 수 없다 — 정리 표 초안에
    # 올라가야 사람이 보고 not_food 로 두든 코드를 달든 한다
    text = "※ 신선한 재료로 준비 ▶주재료 : 두부"

    assert split_materials(text) == (
        RawIngredient("※ 신선한 재료로 준비"),
        RawIngredient("두부", None, "주재료"),
    )


def test_첫_구간_앞에_재료가_적혀_있으면_그대로_재료다() -> None:
    text = "오이지 80g, 쇠고기 250g ▶양념 : 다시마"

    assert split_materials(text) == (
        RawIngredient("오이지", "80g"),
        RawIngredient("쇠고기", "250g"),
        RawIngredient("다시마", None, "양념"),
    )


@pytest.mark.parametrize(
    ("text", "head"),
    [
        ("▶양념장(간장) : 설탕 1큰술, 식초", "양념장(간장)"),
        ("▶양념 간장 : 설탕 1큰술, 식초", "양념 간장"),
        ("▶주재료(2인분) : 설탕 1큰술, 식초", "주재료(2인분)"),
    ],
)
def test_구간_머리에_낱말_하나가_아닌_글이_있으면_그_글도_재료로_남긴다(
    text: str, head: str
) -> None:
    # "양념장(간장)" 의 간장은 머리에만 있다 — role 로만 두면 재료 목록 · 알레르기 판정 어디에도
    # 안 들어간다. 머리 글을 재료로도 남겨 정리 표 초안에 올린다
    assert split_materials(text) == (
        RawIngredient(head),
        RawIngredient("설탕", "1큰술", head),
        RawIngredient("식초", None, head),
    )


@pytest.mark.parametrize("head", ["주재료", "양념장", "육수재료", "타르트지재료"])
def test_낱말_하나인_구간_머리는_재료로_더하지_않는다(head: str) -> None:
    assert split_materials(f"▶{head} : 두부") == (RawIngredient("두부", None, head),)


def test_구간_머리가_없는_구간은_role_없이_재료로_본다() -> None:
    assert split_materials("▶ 소금, 후추") == (RawIngredient("소금"), RawIngredient("후추"))


# ── ▶ 없는 문자열 ────────────────────────────────────────────────────
def test_구간_표시가_없으면_전체를_재료_목록으로_본다() -> None:
    text = "쌀 1컵, 물 2컵, 소금 약간"

    assert split_materials(text) == (
        RawIngredient("쌀", "1컵"),
        RawIngredient("물", "2컵"),
        RawIngredient("소금", "약간"),
    )


def test_구간_표시가_없는_괄호_양_표기도_나눈다() -> None:
    text = "밥(1공기), 김가루(약간), 다시마(1장=5X5cm), 물(1컵, 200ml), 소금(약 1작은술)"

    assert split_materials(text) == (
        RawIngredient("밥", "1공기"),
        RawIngredient("김가루", "약간"),
        RawIngredient("다시마", "1장=5X5cm"),
        RawIngredient("물", "1컵, 200ml"),
        RawIngredient("소금", "약 1작은술"),
    )


@pytest.mark.parametrize(
    "piece",
    [
        "육수(2컵, 새우·멸치 우린 것)",
        "튀김가루(1컵, 새우가루 섞은 것)",
        "소스(2큰술, 땅콩버터 들어감)",
        "다시마(1장=5X5cm, 두꺼운 것)",
        "우유(200ml 두유 아님)",
        "양념(약간 달걀물)",
    ],
)
def test_괄호_속에_양과_함께_다른_글이_있으면_통째로_이름에_남긴다(piece: str) -> None:
    # 양으로 시작한다고 괄호 속 전체를 양으로 떼어 내면 뒤에 든 재료(새우 · 땅콩 · 달걀)가 이름에서
    # 빠진다. `amount` 는 판정이 읽지 않는다 — 놓친 알레르기를 아무도 못 본다
    assert split_materials(f"▶주재료 : {piece}") == (RawIngredient(piece, None, "주재료"),)


# ── 이름 · 양 나누기 ─────────────────────────────────────────────────
def test_괄호_속이_양이_아니면_이름에_남긴다() -> None:
    # "다짐육(돼지고기)" 의 괄호 속이 알레르기 정보다 — 양으로 떼어 내면 놓친다
    assert split_materials("▶주재료 : 다짐육(돼지고기)") == (
        RawIngredient("다짐육(돼지고기)", None, "주재료"),
    )


def test_이름_뒤_양_표기를_나눈다() -> None:
    assert split_materials("▶주재료 : 쇠고기 샤브샤브용 250g") == (
        RawIngredient("쇠고기 샤브샤브용", "250g", "주재료"),
    )


def test_숫자로_끝나도_단위가_아니면_이름_그대로다() -> None:
    # "3분카레" 를 "카레" 이름과 양으로 가르면 안 된다
    assert split_materials("▶주재료 : 오뚜기 3분카레") == (
        RawIngredient("오뚜기 3분카레", None, "주재료"),
    )


def test_양만_있는_조각은_이름을_모르는_재료다() -> None:
    assert split_materials("▶주재료 : 250g") == (RawIngredient(None, "250g", "주재료"),)


def test_구간_안의_빈_조각은_모르는_재료로_남긴다() -> None:
    assert split_materials("▶주재료 : 두부,, 파") == (
        RawIngredient("두부", None, "주재료"),
        RawIngredient(None, None, "주재료"),
        RawIngredient("파", None, "주재료"),
    )


def test_끝_쉼표의_빈_조각도_남긴다() -> None:
    assert split_materials("▶주재료 : 두부,") == (
        RawIngredient("두부", None, "주재료"),
        RawIngredient(None, None, "주재료"),
    )


# ── 구조가 깨진 문자열 ───────────────────────────────────────────────
@pytest.mark.parametrize(
    "text",
    [
        "▶주재료 :",
        "▶주재료 :   ",
        "▶주재료 : 두부 ▶양념 : ",
        "▶주재료 : 두부(큰 것, 파",
        "(3인분) ▶",
    ],
)
def test_내용_없는_구간과_닫히지_않은_괄호는_올린다(text: str) -> None:
    with pytest.raises(ValueError):
        split_materials(text)


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_빈_문자열은_재료_없음이다(text: str) -> None:
    # 재료를 모르는 레시피는 `build_recipe_row` 가 resolved=False 로 둔다 — 해석하지 않는다
    assert split_materials(text) == ()
