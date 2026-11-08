"""app/rules/hazard_rules.py — 위험 물질 사전 묶음. 순수 데이터 검증만 한다."""

import pytest

from app.rules.hazard_rules import (
    DEFAULT_RULES,
    FOOD_NAME_TERMS,
    HAZARD_KINDS,
    PLAY_NAME_TERMS,
    HazardRules,
)
from app.rules.term_match import Term


def _rules(**override) -> HazardRules:
    """유효한 최소 묶음에서 한 칸만 바꾼다. 이름은 모두 지어낸 것이다."""
    base = {
        "named": (Term("drug", ("가상마약",)),),
        "with_intake": (
            Term("alcohol", ("가상술",)),
            Term("tobacco", ("가상담배",)),
            Term("chemical", ("가상세제",)),
        ),
        "with_swallow": (Term("object", ("가상구슬",)),),
        "intake": Term("intake", ("마시",)),
        "swallow": Term("swallow", ("삼키",)),
    }
    return HazardRules(**{**base, **override})


def test_기본_규칙은_위험_종류마다_이름_사전이_있다() -> None:
    assert {term.key for term in DEFAULT_RULES.substances} == set(HAZARD_KINDS)


def test_관찰_이름_칸이_보는_위험_종류() -> None:
    # 물건은 놀잇감 이름이기도 해서 이름 칸에서는 보지 않는다
    assert tuple(t.key for t in FOOD_NAME_TERMS) == ("drug", "alcohol", "tobacco", "chemical")
    assert tuple(t.key for t in PLAY_NAME_TERMS) == ("drug",)


@pytest.mark.parametrize("field", ["named", "with_intake", "with_swallow"])
def test_빈_묶음으로는_규칙을_만들_수_없다(field) -> None:
    with pytest.raises(ValueError):
        _rules(**{field: ()})


@pytest.mark.parametrize("field", ["intake", "swallow"])
def test_동사_사전이_비면_규칙을_만들_수_없다(field) -> None:
    with pytest.raises(ValueError):
        _rules(**{field: Term(field, ())})


def test_별칭이_없는_이름_사전은_받지_않는다() -> None:
    with pytest.raises(ValueError):
        _rules(with_intake=(Term("alcohol", ()),))


def test_모르는_위험_종류_키는_받지_않는다() -> None:
    with pytest.raises(ValueError, match="drugs"):
        _rules(named=(Term("drugs", ("가상마약",)),))


@pytest.mark.parametrize("alias", ["", " ", "..."])
def test_공백이나_문장부호뿐인_별칭은_받지_않는다(alias) -> None:
    # normalize 하면 비어서 어느 글에도 걸리지 않는다 — 검사가 조용히 빠진다
    with pytest.raises(ValueError):
        _rules(intake=Term("intake", ("마시", alias)))
    with pytest.raises(ValueError):
        _rules(
            with_intake=(
                Term("alcohol", ("가상술", alias)),
                Term("tobacco", ("가상담배",)),
                Term("chemical", ("가상세제",)),
            )
        )


def test_위험_종류가_빠진_묶음은_받지_않는다() -> None:
    # 술만 있으면 담배 · 화학제품 검사가 통째로 빠진다
    with pytest.raises(ValueError, match="tobacco"):
        _rules(with_intake=(Term("alcohol", ("가상술",)), Term("chemical", ("가상세제",))))
