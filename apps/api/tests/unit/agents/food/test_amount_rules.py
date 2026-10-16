"""양 텍스트 → 섭취 계수. 정본은 `docs/agents/food/영양소_계산_설계.md` §4 표."""

import pytest

from app.agents.food.amount_rules import amount_of, portion_scale_for, whole_unit_groups
from app.agents.food.doc_seed import FOOD_GROUPS


@pytest.mark.parametrize(
    ("text", "factor", "known"),
    [
        ("다 먹음", 1.0, True),
        ("한 그릇", 1.0, True),
        ("반 그릇", 0.5, True),
        ("절반", 0.5, True),
        ("조금", 0.25, True),
        ("몇 입", 0.25, True),
        ("남김", 0.5, False),
        ("뱉음", 0.0, True),
        ("안 먹음", 0.0, True),
    ],
)
def test_설계_4절_표를_그대로_읽는다(text: str, factor: float, known: bool) -> None:
    rule = amount_of(text)
    assert (rule.factor, rule.known) == (factor, known)


def test_기록이_없으면_양을_모르는_한_그릇이다() -> None:
    assert (amount_of(None).factor, amount_of(None).known) == (1.0, False)


def test_사전에_없는_말도_양을_모르는_한_그릇이다() -> None:
    # 지어내지 않는다 — 모르는 말은 "기록 없음" 과 같게 본다
    assert amount_of("우걱우걱").known is False


# ── 유아 1인분 보정 계수 (Task 3) ────────────────────────────────────────
def test_연령군마다_계수가_있다() -> None:
    assert 0 < portion_scale_for("1-2y") < 1
    assert 0 < portion_scale_for("3-5y") < 1


def test_큰_아이가_더_많이_먹는다() -> None:
    assert portion_scale_for("1-2y") < portion_scale_for("3-5y")


# ── 음식 종류별 1인분 계수 (#411) ────────────────────────────────────────
# 우유 한 팩 · 과일 한 개처럼 개 단위로 먹는 식품은 성인 1인분의 몇 분의 일이 아니라 한 개를 먹는다.
# 카탈로그 행에는 대분류 이름이 남지 않고 식품군만 남아서, 계수는 식품군으로 건다.
@pytest.mark.parametrize("groups", [{"dairy"}, {"fruits"}, {"dairy", "fruits"}])
@pytest.mark.parametrize("age_group", ["1-2y", "3-5y"])
def test_개_단위로_먹는_식품군만_있는_메뉴는_유아_계수를_곱하지_않는다(
    age_group: str, groups: set[str]
) -> None:
    assert portion_scale_for(age_group, frozenset(groups)) == 1.0  # type: ignore[arg-type]


def test_밥_같은_메뉴는_연령군_계수가_그대로_곱해진다() -> None:
    assert portion_scale_for("3-5y", frozenset({"grains"})) == pytest.approx(0.2816)


def test_식품군을_모르는_메뉴는_지금_계수_그대로다() -> None:
    # 찌개 · 국 같은 음식 행은 식품군이 비어 있다 — 개 단위라고 추정하지 않는다
    assert portion_scale_for("3-5y", frozenset()) == pytest.approx(0.2816)
    assert portion_scale_for("3-5y") == pytest.approx(0.2816)


def test_개_단위_식품군과_다른_식품군이_섞인_메뉴는_계수를_곱한다() -> None:
    # 피자 · 치즈 토스트처럼 한 메뉴가 여러 식품군이면 개 단위로 먹는 음식이 아니다
    assert portion_scale_for("3-5y", frozenset({"grains", "dairy"})) == pytest.approx(0.2816)


def test_개_단위_식품군은_식품군_이름_여섯_개_안에_있다() -> None:
    assert whole_unit_groups() == frozenset({"dairy", "fruits"})
    assert whole_unit_groups() <= FOOD_GROUPS
