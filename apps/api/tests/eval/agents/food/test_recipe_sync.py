"""농식품정보원 레시피 라이브 eval — 실제 응답으로 클라이언트와 정리 표 초안을 본다 (#321).

    Remove-Item Env:PYTEST_ADDOPTS -ErrorAction SilentlyContinue
    uv run pytest tests/eval/agents/food/test_recipe_sync.py -m live

키: MAFRA_RECIPE_API_KEY(농림축산식품 공공데이터포털). 호출 서버 IP 를 등록한 PC 에서만 돈다 —
등록하지 않은 IP 는 INFO-100 이 와서 건너뛴다. 원문(레시피 · 재료 이름)은 정리 표와
`recipe_catalog` 로만 들어가므로 이 eval 도 파일에 남기지 않고 건수만 남긴다.

- 동기화: 기본정보 · 재료를 다 받아 RECIPE_ID 로 이은 결과가 받은 행 수와 맞고, 레시피 이름이 모두
  `menu_key` 가 된다(비면 배치가 그 레시피에서 멈춘다).
- 정리 표 초안: 초안이 실제 재료 표기를 빠짐없이 덮고 정리 표 검사(`parse_ingredient_table`)를
  통과한다.
- 검수 전: 초안(전부 draft)으로 만든 레시피 행은 하나도 `resolved` 가 아니다 — 후보 풀이 비어 있다.

기본 실행에서는 제외된다(pyproject 의 addopts = "-m 'not live'").
"""

import dataclasses
import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.agents.food.recipe_catalog import (
    IngredientEntry,
    build_recipe_row,
    ingredient_key,
    parse_ingredient_table,
)
from app.core.config import Settings
from app.core.mafra_recipe_raw import RecipeSync
from app.integrations.mafra.client import MafraApiError, MafraRecipeClient, mafra_recipe_http
from app.rules.menu_key import normalize_menu_key
from scripts.draft_recipe_ingredients import build_draft

pytestmark = pytest.mark.live

NOW = datetime(2026, 10, 11, tzinfo=UTC)
RESULTS = Path(__file__).with_name("test_recipe_sync_results.jsonl")
_synced: dict[str, RecipeSync] = {}


async def _sync() -> RecipeSync:
    """한 번만 받아 세 테스트가 같이 쓴다(호출 8번 · 수 초)."""
    if "sync" not in _synced:
        key = Settings().MAFRA_RECIPE_API_KEY
        if not key:
            pytest.skip("MAFRA_RECIPE_API_KEY 가 없다 — apps/api/.env 에 넣고 돌린다")
        try:
            async with mafra_recipe_http() as http:
                client = MafraRecipeClient(api_key=key, http=http)
                _synced["sync"] = await client.recipes_with_ingredients()
        except MafraApiError as exc:
            if "INFO-100" in str(exc):
                pytest.skip("INFO-100 — 이 PC 의 IP 가 등록되지 않았거나 키가 다르다")
            raise
    return _synced["sync"]


def _draft_table(sync: RecipeSync) -> tuple[list[dict[str, Any]], dict[str, IngredientEntry]]:
    entries = build_draft(dataclasses.asdict(sync))
    table = parse_ingredient_table(
        {"source": "eval", "version": "eval", "fetched_at": "2026-10-11", "ingredients": entries}
    )
    return entries, table


def _record(case: str, data: dict[str, Any]) -> None:
    with RESULTS.open("a", encoding="utf-8") as out:
        out.write(json.dumps({"case": case, **data}, ensure_ascii=False) + "\n")


async def test_레시피_동기화(expect: Callable[..., None]) -> None:
    expect(
        "기본정보 · 재료를 다 받아 RECIPE_ID 로 이은 결과가 받은 행 수와 맞는다 — "
        "이은 재료 + 버린 재료(기본정보 없음) = 받은 재료 행",
        "레시피 이름은 모두 menu_key 가 된다",
    )
    sync = await _sync()
    joined = sum(len(recipe.ingredients) for recipe in sync.recipes)
    keys = Counter(normalize_menu_key(recipe.name) for recipe in sync.recipes)
    _record(
        "sync",
        {
            "recipes": len(sync.recipes),
            "recipe_rows": sync.recipe_rows,
            "ingredient_rows": sync.ingredient_rows,
            "joined": joined,
            "orphan_rows": sync.orphan_ingredient_rows,
            "unnamed_rows": sync.unnamed_ingredient_rows,
            "recipes_without_ingredients": sum(not recipe.ingredients for recipe in sync.recipes),
            "empty_keys": keys.get("", 0),
            # 카탈로그는 menu_key 하나에 한 행이라 배치가 하나를 골라야 하는 레시피 (파트 2)
            "same_key_extra": sum(count - 1 for key, count in keys.items() if key and count > 1),
        },
    )
    assert sync.recipes, "레시피를 하나도 받지 못했다"
    assert len(sync.recipes) == sync.recipe_rows
    assert joined + sync.orphan_ingredient_rows == sync.ingredient_rows
    assert not keys.get(""), f"이름이 menu_key 로 바뀌지 않는 레시피 {keys['']}개"


async def test_정리_표_초안이_재료_표기를_덮는다(expect: Callable[..., None]) -> None:
    expect(
        "실제 재료 표기마다 정리 표 초안에 항목이 있고, 초안이 정리 표 검사를 통과한다",
        "초안 항목은 전부 draft 다 — 사람이 검수하기 전에는 정리된 것으로 세지 않는다",
    )
    sync = await _sync()
    entries, table = _draft_table(sync)
    named = [item.name for recipe in sync.recipes for item in recipe.ingredients if item.name]
    keyed = [key for key in map(ingredient_key, named) if key]
    uncovered = sorted(set(keyed) - set(table))
    statuses = Counter(entry.status for entry in table.values())
    _record(
        "draft",
        {
            "named_rows": len(named),
            "rows_without_key": len(named) - len(keyed),
            "spellings": len({name.strip() for name in named}),
            "entries": len(table),
            "statuses": dict(statuses),
            "uncovered": len(uncovered),
        },
    )
    assert not uncovered, f"초안에 없는 재료 표기 {len(uncovered)}개"
    assert sum(entry["rows"] for entry in entries) == len(keyed), "초안의 행 수가 재료 행과 다르다"
    assert set(statuses) == {"draft"}, f"초안에 draft 가 아닌 항목이 있다: {dict(statuses)}"


async def test_검수_전에는_후보_풀이_비어_있다(expect: Callable[..., None]) -> None:
    expect(
        "초안(전부 draft)으로 만든 레시피 행은 하나도 resolved 가 아니다 — "
        "후보 풀에 들어가는 레시피가 0개다"
    )
    sync = await _sync()
    _, table = _draft_table(sync)
    rows = [build_recipe_row(recipe, table, now=NOW) for recipe in sync.recipes]
    # 초안을 그대로 받아들인다면 풀릴 수 있는 레시피 — 검수 뒤 풀 크기의 위쪽 한계로만 남긴다
    accepted = {
        key: dataclasses.replace(entry, status="ok" if entry.names else "not_food")
        for key, entry in table.items()
    }
    could = sum(build_recipe_row(recipe, accepted, now=NOW).resolved for recipe in sync.recipes)
    resolved = sum(row.resolved for row in rows)
    _record("pool", {"recipes": len(rows), "resolved": resolved, "if_draft_accepted": could})
    assert resolved == 0, f"검수 전인데 후보 풀에 들어가는 레시피가 {resolved}개다"
