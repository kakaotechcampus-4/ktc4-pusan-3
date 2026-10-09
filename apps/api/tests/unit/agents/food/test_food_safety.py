"""app/agents/food/tools/safety.py — filter_food_safety.

알레르기를 거르는 가장 안전 민감한 코드 경로. Food_Tool_명세.md §3 의 판정 순서를
그대로 검증한다. 실제 DB 없음 — MenuCatalogRow·SafetyEntry 를 직접 구성한다.
"""

import shutil
from dataclasses import fields
from datetime import datetime, timezone

import pytest

from app.agents.common import allergy, reference
from app.agents.food.store.ports import MenuCatalogRow, SafetyEntry
from app.agents.food.tools import safety
from app.agents.food.tools.safety import (
    IngredientCheck,
    Purpose,
    filter_food_safety,
    menu_codes,
    resolve_safety,
)

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def _row(
    menu_key: str,
    *,
    ingredients: tuple[str, ...] = (),
    allergen_codes: frozenset[int] = frozenset(),
    resolved: bool = True,
    display_name: str | None = None,
) -> MenuCatalogRow:
    return MenuCatalogRow(
        menu_key=menu_key,
        display_name=display_name or menu_key,
        source="manual",
        resolved=resolved,
        synced_at=NOW,
        ingredients=ingredients,
        allergen_codes=allergen_codes,
    )


def _allergy(label: str, *, status: str = "active", category: tuple[str, ...] = ()) -> SafetyEntry:
    return SafetyEntry(kind="allergy", label=label, status=status, category=category)  # type: ignore[arg-type]


def _condition(label: str, *, kind: str = "chronic_disease", status: str = "active") -> SafetyEntry:
    return SafetyEntry(kind=kind, label=label, status=status)  # type: ignore[arg-type]


# ── 브리핑 표의 여덟 케이스 ──────────────────────────────────────────
def test_우유_알레르기_크림수프는_blocked() -> None:
    row = _row("크림수프", ingredients=("우유", "밀가루"), allergen_codes=frozenset({2}))
    entries = [_allergy("우유")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)
    assert result.passed == ()
    assert result.unchecked == ()
    assert "2" in result.hits["크림수프"]


def test_키위_알레르기_코드없음_키위주스는_blocked() -> None:
    row = _row("키위주스", ingredients=("키위", "물"))
    entries = [_allergy("키위")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)
    assert result.hits["키위주스"] == ("키위",)


def test_꿀_이유기는_blocked_유아기는_passed() -> None:
    row = _row("허니버터 감자", ingredients=("감자", "허니버터"))

    infant_result = filter_food_safety([row], [], "infant_weaning")
    toddler_result = filter_food_safety([row], [], "toddler")

    assert infant_result.blocked == (row,)
    assert toddler_result.passed == (row,)


def test_재료_빈_된장국은_unchecked() -> None:
    row = _row("된장국", ingredients=())

    result = filter_food_safety([row], [], "toddler")

    assert result.unchecked == (row,)
    assert result.blocked == ()
    assert result.passed == ()


def test_새우_알레르기_재료_빈_새우볶음밥은_blocked_이지_unchecked_가_아니다() -> None:
    # ingredients 는 비었지만 allergen_codes 는 이미 채워져 있다(메뉴명 사전 매칭은
    # resolve_menu 의 몫이라 menu_catalog.allergen_codes 에 합쳐져 들어온다는 전제).
    # 이 케이스는 "코드가 있으면 재료를 몰라도 blocked" 임을, 그리고 blocked 판정이
    # unchecked 보다 먼저 적용됨을 확인한다.
    row = _row("새우볶음밥", ingredients=(), allergen_codes=frozenset({9}))
    entries = [_allergy("새우")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)
    assert result.unchecked == ()


def test_status_retracted_땅콩_땅콩강정은_passed() -> None:
    row = _row("땅콩강정", ingredients=("땅콩", "물엿"))
    entries = [_allergy("땅콩", status="retracted")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


# ── 추가 회귀: guard, 질환명 추론 금지, none 상태, 여러 행 ──────────────
def test_만성질환은_질환명_자체로는_안_걸린다() -> None:
    """'당뇨'라는 질환명만 보고 '설탕'을 유도하지 않는다. 식품을 정하는 것은 매핑뿐이다.

    `management` 는 자유 텍스트라 코드가 제한 식품을 뽑지 않는다(루트 §2).
    """
    row = _row("된장국", ingredients=("두부", "된장"))
    entries = [_condition("당뇨")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


# ── chronic_restriction.yaml 매핑 — 브리핑 §5 표 ────────────────────────
def test_유당불내증_매핑만으로_치즈토스트는_blocked() -> None:
    """매핑(우유·유제품·치즈 …)만으로 걸린다."""
    row = _row("치즈토스트", ingredients=("식빵", "치즈"))
    entries = [_condition("유당불내증")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)


def test_유당_불내증_공백_섞인_라벨도_정규화로_매핑에_걸린다() -> None:
    row = _row("치즈토스트", ingredients=("식빵", "치즈"))
    entries = [_condition("유당 불내증")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)


def test_셀리악병_밀크푸딩은_guards가_취소해서_passed() -> None:
    # 밀크티로 보던 테스트다. 밀크티는 홍차라 카페인 규칙(#258)으로 모든 아이에게 막힌다
    row = _row("밀크푸딩", ingredients=("우유", "밀크"))
    entries = [_condition("셀리악병")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


def test_셀리악병_통밀빵은_blocked() -> None:
    row = _row("통밀빵")
    entries = [_condition("셀리악병")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)


def test_당뇨는_매핑에_없어_설탕토스트가_passed() -> None:
    """매핑에 없는 질환은 질환명에서 식품을 유도하지 않는다. 이 테스트가 없으면
    나중에 누가 매핑에 당뇨를 넣어도 아무도 모른다."""
    row = _row("설탕토스트", ingredients=("식빵", "설탕"))
    entries = [_condition("당뇨")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


def test_status_retracted_유당불내증_치즈토스트는_passed() -> None:
    row = _row("치즈토스트", ingredients=("식빵", "치즈"))
    entries = [_condition("유당불내증", status="retracted")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


def test_status_none은_거르지_않는다() -> None:
    row = _row("계란말이", ingredients=("계란",))
    entries = [_allergy("난류", status="none")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


def test_분류가_비어_있는_땅콩은_땅콩강정을_막는다() -> None:
    # 비어 있으면 거른다. "food 가 있는 행만 거른다" 로 만들면 분류가 빠진 식품 알레르기가
    # 아무 신호 없이 필터에서 빠진다
    row = _row("땅콩강정", ingredients=("땅콩", "물엿"))

    result = filter_food_safety([row], [_allergy("땅콩", category=())], "toddler")

    assert result.blocked == (row,)


def test_환경으로만_분류된_쑥은_쑥떡을_막지_않는다() -> None:
    row = _row("쑥떡", ingredients=("쌀", "쑥"))

    # 떡 규칙이 끼지 않는 월령 — 시험하는 것은 쑥 분류다
    result = filter_food_safety(
        [row], [_allergy("쑥", category=("environment",))], "preschool", months=60
    )

    assert result.passed == (row,)


def test_식품과_환경이_같이_분류된_쑥은_쑥떡을_막는다() -> None:
    row = _row("쑥떡", ingredients=("쌀", "쑥"))

    result = filter_food_safety(
        [row], [_allergy("쑥", category=("food", "environment"))], "preschool", months=60
    )

    assert result.blocked == (row,)


def test_약물로만_분류된_페니실린은_어떤_메뉴도_막지_않는다() -> None:
    rows = [_row("계란말이", ingredients=("계란",)), _row("우유푸딩", ingredients=("우유",))]

    result = filter_food_safety(rows, [_allergy("페니실린", category=("drug",))], "toddler")

    assert result.blocked == ()
    assert result.passed == tuple(rows)


def test_약물로만_분류돼도_우유_같은_19종_이름이면_식품으로_보지_않고_뺀다() -> None:
    # category 가 label 보다 먼저다. 보호자가 분류를 약물로 골랐으면 식품 필터의 대상이 아니다
    row = _row("크림수프", ingredients=("우유",))

    result = filter_food_safety([row], [_allergy("우유", category=("drug",))], "toddler")

    assert result.passed == (row,)


def test_알레르기가_아닌_행은_category_가_아니라_label_매핑으로_읽는다() -> None:
    # category 는 kind='allergy' 행만 갖는다. 질환 행의 판단은 category 가 아니라 label 이다
    row = _row("치즈토스트", ingredients=("식빵", "치즈"))

    result = filter_food_safety([row], [_condition("유당불내증")], "toddler")

    assert result.blocked == (row,)


def test_other_medical_갈락토스혈증은_치즈토스트를_막는다() -> None:
    row = _row("치즈토스트", ingredients=("식빵", "치즈"))

    result = filter_food_safety(
        [row], [_condition("갈락토스혈증", kind="other_medical")], "toddler"
    )

    assert result.blocked == (row,)


def test_environmental_고소공포는_어떤_메뉴도_막지_않는다() -> None:
    rows = [_row("계란말이", ingredients=("계란",)), _row("우유푸딩", ingredients=("우유",))]

    result = filter_food_safety(rows, [_condition("고소공포", kind="environmental")], "toddler")

    assert result.blocked == ()


def test_behavioral_행은_식품을_유도하지_않는다() -> None:
    row = _row("계란말이", ingredients=("계란",))

    result = filter_food_safety([row], [_condition("편식", kind="behavioral")], "toddler")

    assert result.passed == (row,)


def test_active_우유_알레르기는_검사_등급과_상관없이_크림수프를_막는다() -> None:
    # class_0 은 검사상 감작이 없다는 뜻이지만, 보호자가 active 로 둔 것을 코드가 낮추면 의료
    # 판단이다. 그래서 SafetyEntry 에 severity 가 없고 필터가 등급을 볼 길이 없다
    row = _row("크림수프", ingredients=("우유",))

    result = filter_food_safety([row], [_allergy("우유")], "toddler")

    assert result.blocked == (row,)


def test_SafetyEntry_는_필터가_안_쓰는_severity_와_management_를_싣지_않는다() -> None:
    assert {field.name for field in fields(SafetyEntry)} == {"kind", "label", "status", "category"}


def test_통과한_메뉴에_든_19종_이름을_정식_명칭으로_돌려준다() -> None:
    # 땅콩 알레르기 아이에게 계란 · 우유가 든 메뉴는 통과한다. 승인 때 확인을 물을 이름이다
    row = _row("계란찜", ingredients=("계란", "우유"), allergen_codes=frozenset({1, 2}))

    result = filter_food_safety([row], [_allergy("땅콩")], "toddler")

    assert result.passed == (row,)
    assert result.allergens == {"계란찜": ("난류", "우유")}


def test_이름_표는_저장된_코드가_비어도_재료를_훑은_코드까지_합친다() -> None:
    # 필터가 메뉴 코드로 보는 것과 같다. 저장 코드만 쓰면 크림(우유) 같은 성분이 표에서 빠진다
    row = _row("크림파스타", ingredients=("크림", "밀가루"))

    result = filter_food_safety([row], [], "toddler")

    assert result.allergens == {"크림파스타": ("우유", "밀")}


def test_이름_표에는_알레르기_성분이_없는_메뉴가_없다() -> None:
    row = _row("시금치무침", ingredients=("시금치", "소금"))

    result = filter_food_safety([row], [], "toddler")

    assert result.passed == (row,)
    assert "시금치무침" not in result.allergens


def test_이름_표는_막힌_메뉴와_확인_못_한_메뉴를_싣지_않는다() -> None:
    blocked = _row("우유푸딩", ingredients=("우유",))
    unchecked = _row("된장국", ingredients=())

    result = filter_food_safety([blocked, unchecked], [_allergy("우유")], "toddler")

    assert result.allergens == {}
    assert result.checks == {}
    assert result.needs_check == ()


# ── 메뉴명에 인쇄된 알레르기 번호 — 급식표 ─────────────────────────────────
def test_메뉴명에_인쇄된_알레르기_번호는_읽지_않는다() -> None:
    # 기관이 아이의 알레르기를 관리하고 보호자에게 미리 알린다. 번호가 붙어 있어도 재료로만 본다
    row = _row("두부조림(5.6)", ingredients=("두부", "설탕"))

    result = filter_food_safety([row], [_allergy("밀")], "toddler")

    assert result.passed == (row,)
    assert result.allergens == {"두부조림(5.6)": ("대두",)}


# ── 제품마다 다른 성분 — 막되 needs_check 로 표시 (#264 PM ⭐6 · #267 멘토) ─────
def test_제품마다_다른_성분만으로_막힌_메뉴는_needs_check_로_표시한다() -> None:
    # 간장의 밀은 제품마다 다르다. 지금은 막고, 식단 추천을 구현할 때 확인 필요로 내보낸다
    row = _row("불고기", ingredients=("소고기", "간장"))

    result = filter_food_safety([row], [_allergy("밀")], "toddler")

    assert result.blocked == (row,)
    assert result.needs_check == (row,)
    assert result.hits == {"불고기": ("6",)}
    assert result.checks == {"불고기": (IngredientCheck(item="간장", allergen="밀"),)}
    assert result.allergens == {"불고기": ("대두", "밀", "쇠고기")}


def test_등록하지_않은_알레르기도_이름_표에_실어_계속_묻는다() -> None:
    # 결정 2 — 밀을 등록하지 않은 아이에게도 "밀 — 먹어본 적 있나요?" 가 나가야 한다
    row = _row("불고기", ingredients=("소고기", "간장"))

    result = filter_food_safety([row], [], "toddler")

    assert result.passed == (row,)
    assert result.allergens == {"불고기": ("대두", "밀", "쇠고기")}
    assert result.checks == {"불고기": (IngredientCheck(item="간장", allergen="밀"),)}


def test_확실한_성분이_같이_있으면_확인으로_풀_수_없다() -> None:
    row = _row("간장 국수", ingredients=("소면", "간장"))

    result = filter_food_safety([row], [_allergy("밀")], "toddler")

    assert result.blocked == (row,)
    assert result.needs_check == ()
    assert result.checks == {}


def test_재료를_모르면_확인으로_풀_수_없다() -> None:
    # 다른 재료를 모르니 된장 하나만 확인받고 내보낼 수 없다. 막힌 채로 두고 표시하지 않는다
    row = _row("된장국", ingredients=())

    result = filter_food_safety([row], [_allergy("밀")], "toddler")

    assert result.blocked == (row,)
    assert result.needs_check == ()
    assert result.checks == {}


def test_확인_필요_음식을_그_이름으로_등록하면_확인으로_풀_수_없다() -> None:
    # "어묵" 으로 등록한 보호자는 어묵 자체를 피하려는 것이다
    row = _row("어묵볶음", ingredients=("어묵", "양파"))

    result = filter_food_safety([row], [_allergy("어묵")], "toddler")

    assert result.blocked == (row,)
    assert result.needs_check == ()


def test_확인_필요_음식_이름으로_등록하면_예전처럼_그_코드도_막는다() -> None:
    # 이름 읽기는 확실 · 확인 필요를 가르지 않는다 — 어묵으로 등록한 보호자는 전에도 밀(6)을 막았다.
    # 이 줄을 빼면 "어묵" 등록이 덜 막는 쪽으로 바뀐다
    row = _row("식빵 토스트", ingredients=("식빵", "버터"))

    result = filter_food_safety([row], [_allergy("어묵")], "toddler")

    assert resolve_safety([_allergy("어묵")]).codes == {6}
    assert result.blocked == (row,)


@pytest.mark.parametrize("label", ["글루텐", "셀리악병"])
def test_묶음_이름_질환으로_등록해도_needs_check_로_표시한다(label: str) -> None:
    row = _row("불고기", ingredients=("소고기", "간장"))
    entry = _condition(label) if label == "셀리악병" else _allergy(label)

    result = filter_food_safety([row], [entry], "toddler")

    assert result.needs_check == (row,)


def test_확인할_재료가_여럿이면_코드_순_이름_순이다() -> None:
    row = _row("제육볶음", ingredients=("돼지고기", "고추장", "간장"))

    result = filter_food_safety([row], [_allergy("밀")], "toddler")

    assert result.checks["제육볶음"] == (
        IngredientCheck(item="간장", allergen="밀"),
        IngredientCheck(item="고추장", allergen="밀"),
    )


def test_치맛살은_맛살이_아니다() -> None:
    row = _row("소고기 볶음", ingredients=("소고기(치맛살)",))

    result = filter_food_safety([row], [_allergy("게")], "toddler")

    assert result.passed == (row,)
    assert result.checks == {}


# ── 이름 표의 19종 밖 이름 (#264 PM ⭐8) ──────────────────────────────────────
def test_이름_표에_19종_밖_알레르기_이름도_담는다() -> None:
    # 데이터 모델 — 19종 밖(아몬드 · 쑥)은 이름으로 담는다. 승인 때 active 행과 대조한다
    row = _row("시금치나물", ingredients=("시금치", "참기름"))

    result = filter_food_safety([row], [], "toddler")

    assert result.allergens == {"시금치나물": ("참깨",)}


def test_19종_이름_다음에_19종_밖_이름이_온다() -> None:
    row = _row("키위 요거트", ingredients=("키위", "요거트"))

    result = filter_food_safety([row], [], "toddler")

    assert result.allergens == {"키위 요거트": ("우유", "키위")}


def test_코드없는_계란_알레르기가_동의어표로_정규화돼_이름_없이도_걸린다() -> None:
    """label="계란" 인데 메뉴 이름엔 "계란"이 없다("마요네즈").

    동의어표(allergen_terms())로 label 을 코드 1 로 정규화하지 않으면, 메뉴의
    allergen_codes 가 맞게 채워져 있어도(F-1 대응으로 이미 1을 포함) 통과해 버린다.
    난류 알레르기가 있는 아이에게 마요네즈가 나가면 안 된다.
    """
    row = _row(
        "마요네즈",
        ingredients=("난황", "식용유"),
        allergen_codes=frozenset({1}),
    )
    entries = [_allergy("계란")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)
    assert result.hits["마요네즈"] == ("1",)


def test_공백_섞인_label도_동의어표_정규화에서_같은_코드로_맞는다() -> None:
    """ "달 걀"처럼 공백이 낀 label 도 normalize() 를 거쳐 같은 코드(1)로 맞아야 한다."""
    row = _row(
        "마요네즈",
        ingredients=("난황", "식용유"),
        allergen_codes=frozenset({1}),
    )
    entries = [_allergy("달 걀")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)


def test_동의어표에_없는_알레르기는_이름_매칭으로만_걸린다() -> None:
    """ "키위"는 19종 밖이라 정규화되지 않는다 — 이름이 걸리는 메뉴만 blocked 다."""
    kiwi_juice = _row("키위주스", ingredients=("키위", "물"))
    mayo = _row(
        "마요네즈",
        ingredients=("난황", "식용유"),
        allergen_codes=frozenset({1}),
    )
    entries = [_allergy("키위")]

    result = filter_food_safety([kiwi_juice, mayo], entries, "toddler")

    assert kiwi_juice in result.blocked
    assert mayo in result.passed


def test_한_번의_호출로_세_묶음이_함께_나뉜다() -> None:
    blocked_row = _row("새우볶음밥", allergen_codes=frozenset({9}))
    unchecked_row = _row("된장국", ingredients=())
    passed_row = _row("현미밥", ingredients=("현미",))
    entries = [_allergy("새우")]

    result = filter_food_safety([blocked_row, unchecked_row, passed_row], entries, "toddler")

    assert result.blocked == (blocked_row,)
    assert result.unchecked == (unchecked_row,)
    assert result.passed == (passed_row,)


def test_resolved_false_는_ingredients가_있어도_unchecked() -> None:
    row = _row("정체불명찌개", ingredients=("무언가",), resolved=False)

    result = filter_food_safety([row], [], "toddler")

    assert result.unchecked == (row,)


@pytest.mark.parametrize("stage", ["toddler", "preschool"])
def test_영유아_이후_단계는_꿀_금지가_적용되지_않는다(stage: str) -> None:
    row = _row("허니버터 감자", ingredients=("감자", "꿀"))

    result = filter_food_safety([row], [], stage)  # type: ignore[arg-type]

    assert result.passed == (row,)


def test_hits는_menu_key만_키로_갖고_로그성_원문을_남기지_않는다() -> None:
    row = _row("키위주스", ingredients=("키위",))
    entries = [_allergy("키위")]

    result = filter_food_safety([row], entries, "toddler")

    assert set(result.hits.keys()) == {"키위주스"}


def test_저장된_코드가_없어도_메뉴명과_재료를_훑어_막는다() -> None:
    # 오래된 카탈로그 행 · 코드를 빠뜨린 행도 막는다 — 저장된 코드만 믿지 않는다
    row = _row("콩국수", ingredients=("소면", "콩국"))

    result = filter_food_safety([row], [_allergy("밀")], "toddler")

    assert result.hits["콩국수"] == ("6",)


def test_글자가_없어도_저장된_코드로_막는다() -> None:
    row = _row("오늘의 국", ingredients=("육수",), allergen_codes=frozenset({9}))

    result = filter_food_safety([row], [_allergy("새우")], "toddler")

    assert result.blocked == (row,)


def test_menu_codes는_메뉴명과_재료에서_19종_코드를_찾는다() -> None:
    assert menu_codes(("바지락칼국수", "바지락", "칼국수면")) == {6, 18}
    assert menu_codes(("강낭콩밥", "쌀", "강낭콩")) == set()


@pytest.mark.parametrize("state", ["ACTIVE", "confirmed", "", "unknown"])
def test_모르는_상태값은_active_로_보고_거른다(state: str) -> None:
    # 어댑터가 status를 잘못 옮겨도 필터가 조용히 꺼지지 않는다.
    # unknown 은 저장하지 않는 값(행이 없는 것)이라 이 칸에 오면 어댑터 버그다
    row = _row("크림수프", ingredients=("우유",))

    result = filter_food_safety([row], [_allergy("우유", status=state)], "toddler")

    assert result.blocked == (row,)


def test_질환_칸에_적은_알레르기도_같은_사전으로_읽는다() -> None:
    rules = resolve_safety([_condition("우유 알레르기")])

    assert rules.codes == {2}


@pytest.mark.parametrize(("months", "blocked"), [(11, True), (12, False)])
def test_꿀은_12개월_미만에만_막는다(months: int, blocked: bool) -> None:
    row = _row("허니버터 감자", ingredients=("감자", "허니버터"))

    result = filter_food_safety([row], [], "toddler", months=months)

    assert (result.blocked == (row,)) is blocked


def test_월령을_모르면_그_단계에서_가장_어린_월령으로_본다() -> None:
    # preschool 은 36개월부터다. 48개월 미만 질식 주의가 붙어야 한다
    row = _row("포도", ingredients=("포도",))

    result = filter_food_safety([row], [], "preschool")

    assert result.cautions == {"포도": ("질식 주의 식품",)}


@pytest.mark.parametrize(("months", "cautioned"), [(47, True), (48, False)])
def test_질식_주의는_막지_않고_안내만_단다(months: int, cautioned: bool) -> None:
    row = _row("포도", ingredients=("포도",))

    result = filter_food_safety([row], [], "preschool", months=months)

    assert result.passed == (row,)
    assert ("포도" in result.cautions) is cautioned


# ── 떡 — 추천은 막고 급식은 주의만 (#264 PM ⭐2) ─────────────────────────────
@pytest.mark.parametrize(
    ("purpose", "blocked", "cautions"),
    [("recommend", True, ()), ("daycare", False, ("떡",))],
)
def test_떡은_추천에서는_막고_급식에서는_주의만_단다(
    purpose: Purpose, blocked: bool, cautions: tuple[str, ...]
) -> None:
    # 끈적해서 잘라도 위험이 남는다. 급식은 기관이 이미 잘라서 주니 주의만 단다
    row = _row("떡국", ingredients=("떡", "소고기"))

    result = filter_food_safety([row], [], "preschool", months=30, purpose=purpose)

    assert (result.blocked == (row,)) is blocked
    assert result.cautions.get("떡국", ()) == cautions


def test_purpose_를_안_넘기면_추천으로_보고_막는다() -> None:
    row = _row("떡국", ingredients=("떡", "소고기"))

    result = filter_food_safety([row], [], "preschool", months=30)

    assert result.blocked == (row,)


def test_급식이어도_알레르기는_그대로_막는다() -> None:
    row = _row("우유푸딩", ingredients=("우유",))

    result = filter_food_safety(
        [row], [_allergy("우유")], "preschool", months=30, purpose="daycare"
    )

    assert result.blocked == (row,)


def test_떡갈비는_떡이_아니다() -> None:
    row = _row("떡갈비", ingredients=("소고기", "돼지고기"))

    result = filter_food_safety([row], [], "preschool", months=30)

    assert result.passed == (row,)
    assert "떡갈비" not in result.cautions


@pytest.fixture
def broken_reference(tmp_path, monkeypatch):
    """사전 파일을 임시 폴더로 옮겨 깨뜨린다.

    앞뒤로 캐시를 비워 다른 테스트가 깨진 값을 보지 않게 한다.
    """
    caches = (
        reference.allergen_terms,
        reference.allergen_groups,
        reference.food_safety_terms,
        reference.chronic_restriction_terms,
        reference.chronic_restriction_codes,
        allergy.shared_book,
        safety.food_book,
        safety._code_matcher,
        safety._varies_matcher,
        safety._extra_matcher,
    )
    for name in ("allergen_terms.yaml", "chronic_restriction.yaml", "food_safety_terms.yaml"):
        shutil.copy(reference.REFERENCE_DIR / name, tmp_path / name)
    monkeypatch.setattr(reference, "REFERENCE_DIR", tmp_path)
    for loader in caches:
        loader.cache_clear()
    yield tmp_path
    for loader in caches:
        loader.cache_clear()


def test_사전을_읽지_못하면_통과시키지_않고_예외를_올린다(broken_reference) -> None:
    (broken_reference / "food_safety_terms.yaml").write_text("source: 깨짐\n", encoding="utf-8")
    row = _row("크림수프", ingredients=("우유",))

    with pytest.raises(ValueError, match="필수 키"):
        filter_food_safety([row], [_allergy("우유")], "toddler")


@pytest.mark.parametrize("ingredients", [("",), (" ", "-")])
def test_빈_재료_글자만_있으면_재료를_모르는_것으로_본다(ingredients: tuple[str, ...]) -> None:
    # 어댑터가 빈 재료 문자열을 나눠 넣어도 통과가 아니라 '확인 못 함'이다
    row = _row("오늘의 국", ingredients=ingredients)

    result = filter_food_safety([row], [_allergy("우유")], "toddler")

    assert result.unchecked == (row,)


def test_술과_카페인은_메뉴명에서만_본다() -> None:
    # 재료 속 양념(커피 1g · 와인)은 조리로 대부분 날아가거나 양이 적다.
    # 마시는 메뉴만 막는다
    cake = _row("고구마 케이크", ingredients=("고구마", "커피 1g"))
    steak = _row("스테이크", ingredients=("소고기", "레드와인"))
    coffee = _row("아이스 아메리카노", ingredients=("에스프레소", "물"))

    result = filter_food_safety([cake, steak, coffee], [], "preschool", months=60)

    assert result.passed == (cake, steak)
    assert result.blocked == (coffee,)
