"""Food 카탈로그 적재 (FT-19) — 영양DB 품목대표 → menu_catalog, 레시피 → recipe_catalog.

    uv run python -m scripts.load_food_catalog --all            # 영양DB 먼저, 그다음 레시피
    uv run python -m scripts.load_food_catalog --nutrition
    uv run python -m scripts.load_food_catalog --recipes        # 영양값은 menu_catalog 에서 붙인다
    uv run python -m scripts.load_food_catalog --all --dry-run  # 받고 세기만 하고 쓰지 않는다

- 받기를 먼저 다 끝내고 쓴다. 외부 API 를 기다리는 동안 DB 트랜잭션을 쥐지 않는다.
- 쓰기는 한 트랜잭션이다. 하나라도 실패하면 아무것도 쓰지 않고 이전 적재본이 그대로 남는다 —
  일부만 실으면 재료를 덜 아는 레시피가 생겨 알레르기를 놓친다.
- 행은 요청 중 조회와 같은 함수로 만든다(`catalog_rows`). 사람이 넣은 메뉴 행 · `stage_min` 태깅은
  덮지 않는다(`domains/food/repository.py`).
- 하나도 못 받았으면(0건) 실패다. 이 API 는 오류를 빈 결과로 주기도 하는데, 그대로 적재하면 "성공"
  으로 끝나고 영양DB 갱신을 놓치며, 레시피는 후보 풀이 통째로 내려간다.
- 이번 받기에 없는 레시피는 후보 풀에서 내린다(`resolved=false`, 행은 남긴다). 소스가 내린 레시피에
  옛 재료 판정이 `resolved=true` 로 남아 계속 나가지 않게 한다.
- 출력은 건수만이다. 키 · 주소 · 원문 이름을 내지 않는다.

매일 새벽 돌리는 것(크론 · 관리자 API)은 BE 가 붙인다(FT-19).
🚨 **이 스크립트를 프로세스로 실행한다**(`python -m scripts.load_food_catalog --all`). `run()` 을
`app/workers/` · `app/api/` 에서 import 하지 않는다 — 행을 만드는 함수가 `app/agents/food/` 에
있어서 workers(agents import 금지)에서는 부를 수 없다. import 로 부르려면 행 만들기를 core 로
내리거나 레이어 예외 표(apps/api CLAUDE.md)에 줄을 더해야 하고, 두 Owner 협의다.
"""

import argparse
import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.food.catalog_rows import (
    facts_from_catalog,
    nutrition_catalog_rows,
    recipe_catalog_rows,
)
from app.agents.food.recipe_catalog import IngredientEntry, ingredient_table
from app.core.mafra_recipe_raw import RecipeSync
from app.core.mfds_nutri_raw import RawNutriRow
from app.domains.food import repository
from app.domains.food.agent_reader import catalog_row, menu_values, recipe_values
from app.rules.menu_key import normalize_menu_key


@dataclass(frozen=True)
class Loaded:
    nutrition_rows: int = 0  # menu_catalog 에 넣거나 고친 영양DB 행
    recipe_rows: int = 0  # recipe_catalog 에 넣거나 덮은 레시피
    resolved_recipes: int = 0  # 그중 재료가 다 정리돼 후보 풀에 들어가는 것
    retired_recipes: int = 0  # 소스에서 사라져 후보 풀에서 내린 레시피


async def run(
    *,
    nutrition: bool,
    recipes: bool,
    fetch_nutrition: Callable[[], Awaitable[Sequence[RawNutriRow]]],
    fetch_recipes: Callable[[], Awaitable[RecipeSync]],
    open_session: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    table: Callable[[], Mapping[str, IngredientEntry]],
    now: datetime,
    dry_run: bool = False,
) -> Loaded:
    raw_rows = await fetch_nutrition() if nutrition else ()
    sync = await fetch_recipes() if recipes else None
    ingredients = table() if recipes else {}
    # 쓰기 전에 막는다. 건수만 말한다
    if nutrition and not raw_rows:
        raise RuntimeError("영양DB 에서 받은 행이 0건이다 — 적재하지 않는다")
    if sync is not None and not sync.recipes:
        raise RuntimeError("레시피를 0건 받았다 — 적재하지 않는다")

    async with open_session() as session:
        try:
            loaded = Loaded()
            if nutrition:
                menus = nutrition_catalog_rows(raw_rows, now=now)
                await repository.refresh_nutrition_menus(
                    session, rows=[menu_values(row) for row in menus]
                )
                loaded = Loaded(nutrition_rows=len(menus))
            if sync is not None:
                keys = sorted({key for r in sync.recipes if (key := normalize_menu_key(r.name))})
                known = await repository.list_nutrition_menus(session, keys=keys)
                facts = {
                    model.menu_key: found
                    for model in known
                    if (found := facts_from_catalog(catalog_row(model))) is not None
                }
                built = recipe_catalog_rows(sync, ingredients, now=now, nutrition=facts)
                await repository.upsert_recipes(session, rows=[recipe_values(row) for row in built])
                retired = await repository.retire_missing_recipes(
                    session,
                    source="mafra_recipe",
                    keep_ids=[row.source_id for row in built if row.source_id],
                )
                loaded = Loaded(
                    nutrition_rows=loaded.nutrition_rows,
                    recipe_rows=len(built),
                    resolved_recipes=sum(row.resolved for row in built),
                    retired_recipes=retired,
                )
        except BaseException:
            await session.rollback()
            raise
        if dry_run:
            await session.rollback()
        else:
            await session.commit()
    return loaded


async def _fetch_nutrition() -> Sequence[RawNutriRow]:
    from app.core.config import settings
    from app.integrations.mfds.client import MfdsNutriClient, mfds_batch_http

    async with mfds_batch_http() as http:
        client = MfdsNutriClient(service_key=settings.DATA_GO_KR_SERVICE_KEY, http=http)
        return await client.representative_rows()


async def _fetch_recipes() -> RecipeSync:
    from app.core.config import settings
    from app.integrations.mafra.client import MafraRecipeClient, mafra_recipe_http

    async with mafra_recipe_http() as http:
        client = MafraRecipeClient(api_key=settings.MAFRA_RECIPE_API_KEY, http=http)
        return await client.recipes_with_ingredients()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--nutrition", action="store_true", help="영양DB 품목대표 → menu_catalog")
    parser.add_argument("--recipes", action="store_true", help="레시피 → recipe_catalog")
    parser.add_argument("--all", action="store_true", help="둘 다(영양DB 먼저)")
    parser.add_argument("--dry-run", action="store_true", help="받고 세기만 하고 쓰지 않는다")
    args = parser.parse_args(argv)
    if not (args.nutrition or args.recipes or args.all):
        parser.error("--nutrition · --recipes · --all 중 하나를 고른다")

    from app.infra.db.session import async_session_factory

    loaded = asyncio.run(
        run(
            nutrition=args.nutrition or args.all,
            recipes=args.recipes or args.all,
            fetch_nutrition=_fetch_nutrition,
            fetch_recipes=_fetch_recipes,
            open_session=async_session_factory,
            table=ingredient_table,
            now=datetime.now(UTC),
            dry_run=args.dry_run,
        )
    )
    suffix = " — dry-run, 쓰지 않음" if args.dry_run else ""
    print(
        f"menu_catalog {loaded.nutrition_rows}행 · recipe_catalog {loaded.recipe_rows}행"
        f"(후보 풀 {loaded.resolved_recipes}개 · 내린 것 {loaded.retired_recipes}개){suffix}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
