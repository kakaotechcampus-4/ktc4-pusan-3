"""양 텍스트 → 섭취 계수. 정본은 `docs/agents/food/영양소_계산_설계.md` §4 표."""

import pytest

from app.agents.food.amount_rules import amount_of


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
