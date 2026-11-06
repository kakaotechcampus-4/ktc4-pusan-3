"""레시피 소스 받기 — 적재 · 정리 표 초안 · 검수 순서 스크립트가 같이 쓴다 (#510)."""

from typing import get_args

from app.core.mafra_recipe_raw import RecipeSync
from app.core.recipe_source import RecipeSource
from scripts import recipe_fetch


def test_CLI_이름은_모든_레시피_소스를_가리킨다():
    # 소스를 더하면 --source 이름도 함께 더해야 한다
    assert set(recipe_fetch.SOURCE_NAMES.values()) == set(get_args(RecipeSource))


async def test_소스에_맞는_받기를_부른다(monkeypatch):
    calls: list[str] = []
    empty = RecipeSync(
        recipes=(),
        recipe_rows=0,
        ingredient_rows=0,
        orphan_ingredient_rows=0,
        unnamed_ingredient_rows=0,
    )

    async def fake_mafra():
        calls.append("mafra")
        return empty

    async def fake_nics():
        calls.append("nics")
        return empty

    monkeypatch.setattr(recipe_fetch, "fetch_mafra", fake_mafra)
    monkeypatch.setattr(recipe_fetch, "fetch_nics", fake_nics)

    await recipe_fetch.fetch("nics_recipe")
    await recipe_fetch.fetch("mafra_recipe")

    assert calls == ["nics", "mafra"]
