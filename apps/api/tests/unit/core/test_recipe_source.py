"""레시피 소스 값 — BE 조회 계약 · AI 포트 · DB CHECK 가 같이 쓰는 계약 (#510)."""

from typing import get_args

from app.core.recipe_source import RecipeSource
from app.domains.food.models import RECIPE_SOURCE_CHECK, RecipeCatalog


def _quoted(check: str) -> set[str]:
    return set(check.split("IN (", 1)[1].rstrip(")").replace("'", "").replace(" ", "").split(","))


def test_운영에서_받는_레시피_소스는_둘이다():
    assert set(get_args(RecipeSource)) == {"mafra_recipe", "nics_recipe"}


def test_RecipeSource_의_모든_값을_DB_가_받는다():
    assert set(get_args(RecipeSource)) <= _quoted(RECIPE_SOURCE_CHECK)


def test_DB_는_식약처_소스도_받고_RecipeSource_는_게이트_뒤에_더한다():
    # Part E 게이트를 통과해 RecipeSource 에 mfds_recipe 를 더하면 이 테스트를 함께 고친다
    assert _quoted(RECIPE_SOURCE_CHECK) - set(get_args(RecipeSource)) == {"mfds_recipe"}


def test_모델의_CHECK_가_상수를_쓴다():
    check = next(
        constraint
        for constraint in RecipeCatalog.__table__.constraints
        if constraint.name == "recipe_catalog_source"
    )

    assert str(check.sqltext) == RECIPE_SOURCE_CHECK
