"""Food 의 카탈로그 포트(`MenuCatalogStore` · `RecipeCatalogStore`)를 두 표로 구현한다.

Agent 는 DB 에 직접 닿지 않는다. 러너 · 05 가 이 어댑터를 만들어 entrypoint 로 넘긴다. 행 모양
(`MenuCatalogRow`)은 agents 가 정한 계약이라 여기서 import 한다 — `domains/safety/agent_reader.py`
와 같은 예외 경로다(apps/api CLAUDE.md "agents 가 정한 저장 계약").

- 🚨 알레르기 판정 입력(코드 · 재료 · 주재료 · 식품군 · 단계 · resolved)은 값을 바꾸지 않고 옮긴다.
  frozenset 은 정렬한 배열로 싣고 읽을 때 frozenset 으로 되돌린다. 재료 순서는 그대로다.
- 🚨 읽기에 실패하면 예외를 그대로 올린다. 빈 목록이면 Food 가 "추천 0개" 로 끝나 실패가 가려진다 —
  pipeline 이 그 Agent 를 실패(partial)로 센다.
- 호출마다 짧은 세션을 연다. run 세션을 도메인 task 들이 같이 타지 않게 (#257). 쓰기는 호출 하나가
  짧은 트랜잭션 하나다(포트 계약).
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.food.store.ports import RECIPE_SOURCES, MenuCatalogRow
from app.domains.food import repository
from app.domains.food.models import MenuCatalog, RecipeCatalog

# 호출마다 새 세션을 여는 함수. 러너가 `background_session` 으로 만든다
SessionOpener = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def _shared_values(row: MenuCatalogRow) -> dict[str, Any]:
    return {
        "menu_key": row.menu_key,
        "display_name": row.display_name,
        "source": row.source,
        "food_code": row.food_code,
        "serving_g": row.serving_g,
        "nutrients": dict(row.nutrients),
        "ingredients": list(row.ingredients),
        "allergen_codes": sorted(row.allergen_codes),
        "food_groups": sorted(row.food_groups),
        "stage_min": row.stage_min,
        "resolved": row.resolved,
        "source_version": row.source_version,
        "synced_at": row.synced_at,
    }


def menu_values(row: MenuCatalogRow) -> dict[str, Any]:
    """`menu_catalog` 한 행의 값. 레시피 행은 받지 않는다 — `recipe_catalog` 몫이다(#326 리뷰)."""
    if row.source in RECIPE_SOURCES:
        raise ValueError(f"{row.source} 행은 recipe_catalog 에 넣는다")
    return _shared_values(row)


def recipe_values(row: MenuCatalogRow) -> dict[str, Any]:
    if row.source not in RECIPE_SOURCES or not row.source_id:
        raise ValueError("recipe_catalog 에는 레시피 소스 · 레시피 번호가 있는 행만 넣는다")
    return _shared_values(row) | {
        "source_id": row.source_id,
        "main_ingredients": list(row.main_ingredients),
    }


def catalog_row(model: MenuCatalog | RecipeCatalog) -> MenuCatalogRow:
    recipe = isinstance(model, RecipeCatalog)
    return MenuCatalogRow(
        menu_key=model.menu_key,
        display_name=model.display_name,
        source=model.source,  # type: ignore[arg-type] — CHECK 가 값 범위를 막는다
        resolved=model.resolved,
        synced_at=model.synced_at,
        food_code=model.food_code,
        serving_g=None if model.serving_g is None else float(model.serving_g),
        nutrients={key: float(value) for key, value in model.nutrients.items()},
        ingredients=tuple(model.ingredients),
        main_ingredients=tuple(model.main_ingredients) if recipe else (),
        allergen_codes=frozenset(model.allergen_codes),
        food_groups=frozenset(model.food_groups),
        stage_min=model.stage_min,  # type: ignore[arg-type] — CHECK 가 값 범위를 막는다
        source_version=model.source_version,
        source_id=model.source_id if recipe else None,
    )


class DbMenuCatalog:
    """`MenuCatalogStore`. 요청 중 캐시 미스를 채우는 `put` 은 없을 때만 넣는다(FT-13)."""

    def __init__(self, open_session: SessionOpener) -> None:
        self._open = open_session

    async def get(self, menu_key: str) -> MenuCatalogRow | None:
        return (await self.get_many((menu_key,))).get(menu_key)

    async def get_many(self, menu_keys: tuple[str, ...]) -> dict[str, MenuCatalogRow]:
        if not menu_keys:
            return {}
        async with self._open() as session:
            found = await repository.get_menus(session, keys=list(menu_keys))
        return {model.menu_key: catalog_row(model) for model in found}

    async def put(self, row: MenuCatalogRow) -> None:
        values = menu_values(row)
        async with self._open() as session:
            await repository.insert_menu_if_absent(session, values=values)
            await session.commit()


class DbRecipeCatalog:
    """`RecipeCatalogStore`. 후보 풀은 `resolved=true` 행만 읽는다."""

    def __init__(self, open_session: SessionOpener) -> None:
        self._open = open_session

    async def all_resolved(self) -> list[MenuCatalogRow]:
        async with self._open() as session:
            found = await repository.list_resolved_recipes(session)
        return [catalog_row(model) for model in found]

    async def put(self, row: MenuCatalogRow) -> None:
        values = recipe_values(row)
        async with self._open() as session:
            await repository.upsert_recipes(session, rows=[values])
            await session.commit()
