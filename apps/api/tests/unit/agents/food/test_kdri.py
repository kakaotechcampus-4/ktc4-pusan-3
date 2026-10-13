"""섭취기준 — 정본은 `docs/agents/food/영양소_계산_설계.md` §2 · §6, 값은
`reference/kdri_2025.yaml`."""

import pytest

from app.agents.food.kdri import refs_for


def test_두_연령군이_다_있다() -> None:
    assert refs_for("1-2y") and refs_for("3-5y")


def test_칼슘은_EAR_기준이다() -> None:
    ref = refs_for("1-2y")["calcium"]
    assert ref.kind == "ear" and ref.ear and ref.ear > 0


def test_탄단지는_AMDR_범위다() -> None:
    ref = refs_for("1-2y")["carbohydrate"]
    assert ref.kind == "amdr" and ref.low is not None and ref.high is not None
    assert ref.low < ref.high


def test_나트륨은_상한만_있다() -> None:
    ref = refs_for("1-2y")["sodium"]
    assert ref.kind == "upper" and ref.upper and ref.upper > 0
    assert ref.ear is None


def test_성별로_나뉜_행을_담지_않는다() -> None:
    # 성별 미사용 확정 (설계 N-6). 성별 칸이 들어오면 읽을 때 실패한다
    for group in ("1-2y", "3-5y"):
        for ref in refs_for(group).values():
            assert not hasattr(ref, "sex")


def test_모르는_연령군은_실패한다() -> None:
    with pytest.raises(ValueError, match="연령군"):
        refs_for("6-8y")  # type: ignore[arg-type]
