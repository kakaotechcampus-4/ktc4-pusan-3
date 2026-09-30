"""app/agents/food/tools/safety.py — filter_food_safety.

알레르기를 거르는 가장 안전 민감한 코드 경로. Food_Tool_명세.md §3 의 판정 순서를
그대로 검증한다. 실제 DB 없음 — MenuCatalogRow·SafetyEntry 를 직접 구성한다.
"""

from datetime import datetime, timezone

import pytest

from app.agents.food.store.ports import MenuCatalogRow, SafetyEntry
from app.agents.food.tools.safety import filter_food_safety

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


def _allergy(
    label: str, *, code: int | None, state: str = "active", aliases: tuple[str, ...] = ()
) -> SafetyEntry:
    return SafetyEntry(
        kind="allergy", label=label, state=state, allergen_code=code, aliases=aliases
    )


def _restriction(
    label: str,
    *,
    kind: str = "chronic_disease",
    restricted_foods: tuple[str, ...],
    state: str = "active",
) -> SafetyEntry:
    return SafetyEntry(kind=kind, label=label, state=state, restricted_foods=restricted_foods)


# ── 브리핑 표의 여덟 케이스 ──────────────────────────────────────────
def test_우유_알레르기_크림수프는_blocked() -> None:
    row = _row("크림수프", ingredients=("우유", "밀가루"), allergen_codes=frozenset({2}))
    entries = [_allergy("우유", code=2)]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)
    assert result.passed == ()
    assert result.unchecked == ()
    assert "2" in result.hits["크림수프"]


def test_키위_알레르기_코드없음_키위주스는_blocked() -> None:
    row = _row("키위주스", ingredients=("키위", "물"))
    entries = [_allergy("키위", code=None)]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)
    assert result.hits["키위주스"] == ("키위",)


def test_꿀떡_이유기는_blocked_유아기는_passed() -> None:
    row = _row("꿀떡", ingredients=("찹쌀", "꿀"))

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
    entries = [_allergy("새우", code=9)]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)
    assert result.unchecked == ()


def test_state_unknown_우유_크림수프는_passed() -> None:
    row = _row("크림수프", ingredients=("우유",))
    entries = [_allergy("우유", code=2, state="unknown")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


def test_state_retracted_땅콩_땅콩강정은_passed() -> None:
    row = _row("땅콩강정", ingredients=("땅콩", "물엿"))
    entries = [_allergy("땅콩", code=4, state="retracted")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


def test_만성질환_restricted_foods_설탕_설탕토스트는_blocked() -> None:
    """보호자 입력 경로는 아직 없다(#task-5) — 이 값을 직접 주입해서 그 경로가 생기면
    동작한다는 것만 확인한다. 매핑(chronic_restriction.yaml)이 주 경로다."""
    row = _row("설탕토스트", ingredients=("식빵", "설탕"))
    entries = [_restriction("당뇨", restricted_foods=("설탕",))]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)
    assert result.hits["설탕토스트"] == ("당뇨",)


# ── 추가 회귀: guard, 질환명 추론 금지, none 상태, 여러 행 ──────────────
def test_만성질환은_질환명_자체로는_안_걸린다() -> None:
    """'당뇨'라는 질환명만 보고 '설탕'을 유도하지 않는다 — restricted_foods 가 비면 통과.

    보호자 입력 경로는 아직 없다(#task-5) — restricted_foods 를 직접 비워 넣는다.
    매핑이 주 경로다.
    """
    row = _row("된장국", ingredients=("두부", "된장"))
    entries = [_restriction("당뇨", restricted_foods=())]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


# ── chronic_restriction.yaml 매핑 — 브리핑 §5 표 ────────────────────────
def test_유당불내증_매핑만으로_치즈토스트는_blocked() -> None:
    """restricted_foods 가 비어도 매핑(우유·유제품·치즈 …)만으로 걸린다."""
    row = _row("치즈토스트", ingredients=("식빵", "치즈"))
    entries = [_restriction("유당불내증", restricted_foods=())]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)


def test_유당_불내증_공백_섞인_라벨도_정규화로_매핑에_걸린다() -> None:
    row = _row("치즈토스트", ingredients=("식빵", "치즈"))
    entries = [_restriction("유당 불내증", restricted_foods=())]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)


def test_셀리악병_밀크티는_guards가_취소해서_passed() -> None:
    row = _row("밀크티", ingredients=("홍차",))
    entries = [_restriction("셀리악병", restricted_foods=())]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


def test_셀리악병_통밀빵은_blocked() -> None:
    row = _row("통밀빵")
    entries = [_restriction("셀리악병", restricted_foods=())]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)


def test_당뇨는_매핑에_없어_설탕토스트가_passed() -> None:
    """매핑에 없는 질환은 질환명에서 식품을 유도하지 않는다. 이 테스트가 없으면
    나중에 누가 매핑에 당뇨를 넣어도 아무도 모른다."""
    row = _row("설탕토스트", ingredients=("식빵", "설탕"))
    entries = [_restriction("당뇨", restricted_foods=())]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


def test_유당불내증_매핑밖_아이스크림도_restricted_foods로_blocked() -> None:
    """합집합의 한쪽 — 매핑에 없는 품목도 보호자가 등록한 값으로 걸린다."""
    row = _row("아이스크림")
    entries = [_restriction("유당불내증", restricted_foods=("아이스크림",))]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)


def test_유당불내증_restricted_foods를_등록해도_매핑쪽_치즈토스트는_그대로_blocked() -> None:
    """합집합의 다른 쪽 — restricted_foods 를 채웠어도 매핑이 아는 식품은 계속 걸린다."""
    row = _row("치즈토스트", ingredients=("식빵", "치즈"))
    entries = [_restriction("유당불내증", restricted_foods=("아이스크림",))]

    result = filter_food_safety([row], entries, "toddler")

    assert result.blocked == (row,)


def test_state_retracted_유당불내증_치즈토스트는_passed() -> None:
    row = _row("치즈토스트", ingredients=("식빵", "치즈"))
    entries = [_restriction("유당불내증", restricted_foods=(), state="retracted")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


def test_state_none은_거르지_않는다() -> None:
    row = _row("계란말이", ingredients=("계란",))
    entries = [_allergy("난류", code=1, state="none")]

    result = filter_food_safety([row], entries, "toddler")

    assert result.passed == (row,)


def test_코드없는_계란_알레르기가_동의어표로_정규화돼_이름_없이도_걸린다() -> None:
    """label="계란"·allergen_code=None 인데 메뉴 이름엔 "계란"이 없다("마요네즈").

    동의어표(allergen_terms())로 label 을 코드 1 로 정규화하지 않으면, 메뉴의
    allergen_codes 가 맞게 채워져 있어도(F-1 대응으로 이미 1을 포함) 통과해 버린다.
    난류 알레르기가 있는 아이에게 마요네즈가 나가면 안 된다.
    """
    row = _row(
        "마요네즈",
        ingredients=("난황", "식용유"),
        allergen_codes=frozenset({1}),
    )
    entries = [_allergy("계란", code=None)]

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
    entries = [_allergy("달 걀", code=None)]

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
    entries = [_allergy("키위", code=None)]

    result = filter_food_safety([kiwi_juice, mayo], entries, "toddler")

    assert kiwi_juice in result.blocked
    assert mayo in result.passed


def test_한_번의_호출로_세_묶음이_함께_나뉜다() -> None:
    blocked_row = _row("새우볶음밥", allergen_codes=frozenset({9}))
    unchecked_row = _row("된장국", ingredients=())
    passed_row = _row("현미밥", ingredients=("현미",))
    entries = [_allergy("새우", code=9)]

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
    row = _row("꿀떡", ingredients=("꿀",))

    result = filter_food_safety([row], [], stage)  # type: ignore[arg-type]

    assert result.passed == (row,)


def test_hits는_menu_key만_키로_갖고_로그성_원문을_남기지_않는다() -> None:
    row = _row("키위주스", ingredients=("키위",))
    entries = [_allergy("키위", code=None)]

    result = filter_food_safety([row], entries, "toddler")

    assert set(result.hits.keys()) == {"키위주스"}
