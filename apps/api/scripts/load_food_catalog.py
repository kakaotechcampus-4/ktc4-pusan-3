"""Food 카탈로그 적재 (FT-19) — 영양DB 품목대표 → menu_catalog, 레시피 → recipe_catalog.

    uv run python -m scripts.load_food_catalog --all            # 영양DB 먼저, 그다음 레시피
    uv run python -m scripts.load_food_catalog --nutrition
    uv run python -m scripts.load_food_catalog --recipes        # 영양값은 menu_catalog 에서 붙인다
    uv run python -m scripts.load_food_catalog --all --dry-run  # 받고 세기만 하고 쓰지 않는다

- 받기를 먼저 다 끝내고 쓴다. 외부 API 를 기다리는 동안 DB 트랜잭션을 쥐지 않는다.
- 쓰기는 한 트랜잭션이다. 하나라도 실패하면 아무것도 쓰지 않고 이전 적재본이 그대로 남는다 —
  일부만 실으면 재료를 덜 아는 레시피가 생겨 알레르기를 놓친다.
- 행은 요청 중 조회와 같은 함수로 만든다(`catalog_rows`). 사람이 넣은 메뉴 행 · `stage_min` 태깅은
  덮지 않는다(`domains/food/repository.py`). 레시피 행은 한 번만 만든다 — 식품군 빌리기에 먼저 쓰고,
  영양DB 를 쓴 뒤 영양값만 붙인다(`with_nutrition`).
- 하나도 못 받았으면(0건) 실패다. 이 API 는 오류를 빈 결과로 주기도 하는데, 그대로 적재하면 "성공"
  으로 끝나고 영양DB 갱신을 놓치며, 레시피는 후보 풀이 통째로 내려간다.
- 이번 받기에 없는 레시피는 후보 풀에서 내린다(`resolved=false`, 행은 남긴다). 소스가 내린 레시피에
  옛 재료 판정이 `resolved=true` 로 남아 계속 나가지 않게 한다. 내리는 것은 **소스마다 그 소스의
  번호로만** 한다 — 한 소스의 누락이 다른 소스의 행을 내리지 않는다.
- 레시피 소스는 둘이다(`mafra_recipe` · `nics_recipe`). 농식품정보원은 필수라 키가 없으면 실패하고,
  NICS 는 키(`NICS_RECIPE_API_KEY`)가 있을 때만 받는다. 키가 비면 그 소스는 건너뛰고 요약에
  "건너뜀" 으로 센다 — 단 그 소스의 레시피가 이미 후보 풀에 있으면 적재하지 않는다. 건너뛴 소스의
  행은 다시 만들 수 없어서, 정리 표에서 알레르기 코드를 고쳐도 옛 판정으로 후보 풀에 남는다. 어느
  소스든 받기에 실패하거나 0건이면 아무 소스도 쓰지 않는다.
- 찌개 · 국처럼 대응표에 없는 영양DB 메뉴는 이름이 같은 레시피의 주재료 식품군을 빌린다(#420).
  레시피도 받으면 그 값으로, `--nutrition` 만 돌리면 이미 적재된 레시피 표에서 빌린다.
  `--recipes` 만 돌리면 메뉴 행은 그대로라, 정리 표 `food_group` 을 고쳤으면 `--all` 로 돌린다.
- 출력은 건수만이다. 키 · 주소 · 원문 이름을 내지 않는다.

매일 새벽 돌리는 것(크론 · 관리자 API)은 BE 가 붙인다(FT-19).
🚨 **이 스크립트를 프로세스로 실행한다**(`python -m scripts.load_food_catalog --all`). `run()` 을
`app/workers/` · `app/api/` 에서 import 하지 않는다 — 행을 만드는 함수가 `app/agents/food/` 에
있어서 workers(agents import 금지)에서는 부를 수 없다. import 로 부르려면 행 만들기를 core 로
내리거나 레이어 예외 표(apps/api CLAUDE.md)에 줄을 더해야 하고, 두 Owner 협의다.
"""

import argparse
import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.food.catalog_rows import (
    borrowable_food_groups,
    facts_from_catalog,
    nutrition_catalog_rows,
    recipe_catalog_rows,
)
from app.agents.food.recipe_catalog import (
    IngredientEntry,
    has_main_section,
    ingredient_table,
    with_nutrition,
)
from app.agents.food.store.ports import MenuCatalogRow
from app.core.mafra_recipe_raw import RecipeSync
from app.core.mfds_nutri_raw import RawNutriRow
from app.core.recipe_source import RecipeSource
from app.domains.food import repository
from app.domains.food.agent_reader import catalog_row, menu_values, recipe_values
from scripts import recipe_fetch


@dataclass(frozen=True)
class RecipeBatch:
    """한 소스에서 받은 레시피 전체. 적재는 소스마다 그 소스의 번호로만 내린다."""

    source: RecipeSource
    sync: RecipeSync


@dataclass(frozen=True)
class FetchedRecipes:
    """이번에 받은 레시피 소스들. `skipped` 는 키가 없어 받지 않은 소스 — 그 소스의 레시피가 후보
    풀에 있으면 적재하지 않는다(`_refuse_stale`)."""

    batches: tuple[RecipeBatch, ...]
    skipped: tuple[RecipeSource, ...] = ()


@dataclass(frozen=True)
class SourceLoaded:
    """한 소스의 적재 결과. 건수만이다 — 원문 이름 · 주소를 담지 않는다."""

    source: RecipeSource
    rows: int  # recipe_catalog 에 넣거나 덮은 레시피
    resolved: int  # 그중 재료가 다 정리돼 후보 풀에 들어가는 것
    retired: int  # 소스에서 사라져 후보 풀에서 내린 레시피
    without_main: int  # `주재료` 구간이 없어 식품군이 빈 채로 실은 레시피


@dataclass(frozen=True)
class Loaded:
    nutrition_rows: int = 0  # menu_catalog 에 넣거나 고친 영양DB 행
    recipe_rows: int = 0  # recipe_catalog 에 넣거나 덮은 레시피(모든 소스 합)
    resolved_recipes: int = 0  # 그중 재료가 다 정리돼 후보 풀에 들어가는 것
    retired_recipes: int = 0  # 소스에서 사라져 후보 풀에서 내린 레시피
    # 영양DB 에서 받은 원문 행. 같은 이름은 한 메뉴로 모이고 핵심 영양 칸이 빈 이름은 메뉴가 못 돼서
    # `nutrition_rows` 보다 많다(실제 8,807행 → 5,488메뉴). 숫자 하나만 보이면 오해한다
    nutrition_received: int = 0
    # 그중 식품군이 있는 메뉴. 영양소 분석은 식품군을 아는 메뉴가 절반이 안 되면 출현을
    # 말하지 않는다
    grouped_menus: int = 0
    # 소스별 결과와 건너뛴 소스. 합계만 보이면 어느 소스가 비었는지 모른다
    sources: tuple[SourceLoaded, ...] = ()
    skipped: tuple[RecipeSource, ...] = ()


# 한 소스에서 만든 레시피 행(영양값은 아직 없다)
Built = list[tuple[RecipeBatch, list[MenuCatalogRow]]]


async def run(
    *,
    nutrition: bool,
    recipes: bool,
    fetch_nutrition: Callable[[], Awaitable[Sequence[RawNutriRow]]],
    fetch_recipes: Callable[[], Awaitable[FetchedRecipes]],
    open_session: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    table: Callable[[], Mapping[str, IngredientEntry]],
    now: datetime,
    dry_run: bool = False,
) -> Loaded:
    raw_rows = await fetch_nutrition() if nutrition else ()
    fetched = await fetch_recipes() if recipes else None
    ingredients = table() if recipes else {}
    # 쓰기 전에 막는다. 건수만 말한다
    if nutrition and not raw_rows:
        raise RuntimeError("영양DB 에서 받은 행이 0건이다 — 적재하지 않는다")
    built: Built | None = None
    if fetched is not None:
        _check_fetched(fetched)
        # 행은 한 번만 만든다 — 식품군은 영양값과 무관해서 영양값 없이 만들고, 영양값은 영양DB 를
        # 쓴 뒤 붙인다
        built = [
            (
                batch,
                recipe_catalog_rows(
                    batch.sync, ingredients, now=now, source=batch.source, nutrition={}
                ),
            )
            for batch in fetched.batches
        ]

    async with open_session() as session:
        try:
            if fetched is not None and fetched.skipped:
                await _refuse_stale(session, fetched.skipped)
            loaded = Loaded()
            if nutrition:
                borrowed = await _borrowed_groups(session, built)
                menus = nutrition_catalog_rows(raw_rows, now=now, borrowed=borrowed)
                await repository.refresh_nutrition_menus(
                    session, rows=[menu_values(row) for row in menus]
                )
                loaded = Loaded(
                    nutrition_rows=len(menus),
                    nutrition_received=len(raw_rows),
                    grouped_menus=sum(1 for row in menus if row.food_groups),
                )
            if fetched is not None and built is not None:
                done = await _load_recipes(session, built)
                loaded = replace(
                    loaded,
                    recipe_rows=sum(item.rows for item in done),
                    resolved_recipes=sum(item.resolved for item in done),
                    retired_recipes=sum(item.retired for item in done),
                    sources=done,
                    skipped=fetched.skipped,
                )
        except BaseException:
            await session.rollback()
            raise
        if dry_run:
            await session.rollback()
        else:
            await session.commit()
    return loaded


def _check_fetched(fetched: FetchedRecipes) -> None:
    """쓰기 전에 받은 결과를 막는다. 한 소스라도 이상하면 아무 소스도 쓰지 않는다."""
    if not fetched.batches:
        raise RuntimeError("받은 레시피 소스가 하나도 없다 — 적재하지 않는다")
    sources = [batch.source for batch in fetched.batches]
    if len(set(sources)) != len(sources):
        # 같은 소스가 둘이면 뒤 묶음의 번호만 남기고 앞 묶음의 행을 내리게 된다
        raise ValueError("같은 레시피 소스가 두 번 왔다")
    for batch in fetched.batches:
        if not batch.sync.recipes:
            raise RuntimeError(f"{batch.source} 레시피를 0건 받았다 — 적재하지 않는다")


async def _refuse_stale(session: AsyncSession, skipped: Sequence[RecipeSource]) -> None:
    """키가 없어 건너뛴 소스의 레시피가 후보 풀에 있으면 멈춘다. 그 행은 다시 만들 수 없어서 정리
    표에서 알레르기 코드를 고치거나 재료를 unknown 으로 돌려도 옛 판정 그대로 후보에 남는다."""
    stale = Counter(
        model.source
        for model in await repository.list_resolved_recipes(session)
        if model.source in skipped
    )
    if stale:
        found = ", ".join(f"{source} {count}건" for source, count in sorted(stale.items()))
        raise RuntimeError(
            f"키가 없어 건너뛴 소스의 레시피가 후보 풀에 있다({found}) — 정리 표 변경을 반영할 수 "
            "없어 적재하지 않는다. 키를 넣고 다시 돌린다"
        )


def _without_main(sync: RecipeSync) -> int:
    """`주재료` 구간이 없는 레시피 수. 식품군이 빈 채로 실린다 — 재료 · 알레르기 판정은 그대로다."""
    return sum(1 for recipe in sync.recipes if not has_main_section(recipe))


async def _load_recipes(session: AsyncSession, built: Built) -> tuple[SourceLoaded, ...]:
    """만든 행에 영양값을 붙여 다 쓴 뒤, 소스마다 그 소스의 번호로만 내린다."""
    keys = sorted({row.menu_key for _, rows in built for row in rows})
    known = await repository.list_nutrition_menus(session, keys=keys)
    facts = {
        model.menu_key: found
        for model in known
        if (found := facts_from_catalog(catalog_row(model))) is not None
    }
    for _, rows in built:
        await repository.upsert_recipes(
            session,
            rows=[recipe_values(with_nutrition(row, facts.get(row.menu_key))) for row in rows],
        )
    done: list[SourceLoaded] = []
    for batch, rows in built:
        retired = await repository.retire_missing_recipes(
            session,
            source=batch.source,
            keep_ids=[row.source_id for row in rows if row.source_id],
        )
        done.append(
            SourceLoaded(
                source=batch.source,
                rows=len(rows),
                resolved=sum(row.resolved for row in rows),
                retired=retired,
                without_main=_without_main(batch.sync),
            )
        )
    return tuple(done)


async def _borrowed_groups(session: AsyncSession, built: Built | None) -> dict[str, frozenset[str]]:
    """영양DB 메뉴가 빌릴 레시피 식품군. 이번에 레시피도 받았으면 그 행으로 만든다(건너뛴 소스는
    후보 풀에 행이 없을 때만 건너뛰므로 빌려 줄 것도 없다). 레시피를 안 받았으면(`--nutrition`
    만) 적재된 레시피 표 전부에서 읽는다."""
    if built is None:
        models = await repository.list_resolved_recipes(session)
        return borrowable_food_groups(catalog_row(model) for model in models)
    return borrowable_food_groups(row for _, rows in built for row in rows)


async def _fetch_nutrition() -> Sequence[RawNutriRow]:
    from app.core.config import settings
    from app.integrations.mfds.client import MfdsNutriClient, mfds_batch_http

    async with mfds_batch_http() as http:
        client = MfdsNutriClient(service_key=settings.DATA_GO_KR_SERVICE_KEY, http=http)
        return await client.representative_rows()


async def _fetch_recipes() -> FetchedRecipes:
    """레시피 소스를 차례로 받는다. 농식품정보원은 필수라 실패하면 그대로 올린다(키가 없어도).
    NICS 는 키가 있을 때만 받는다 — 비면 건너뜀으로 센다(그 소스 레시피가 후보 풀에 있으면
    `run` 이 멈춘다)."""
    from app.core.config import settings

    batches = [RecipeBatch("mafra_recipe", await recipe_fetch.fetch_mafra())]
    if not settings.NICS_RECIPE_API_KEY:
        return FetchedRecipes(batches=tuple(batches), skipped=("nics_recipe",))
    batches.append(RecipeBatch("nics_recipe", await recipe_fetch.fetch_nics()))
    return FetchedRecipes(batches=tuple(batches))


def build_parser() -> argparse.ArgumentParser:
    # 모듈 docstring 을 그대로 쓰지 않는다 — `—` · 이모지는 Windows 콘솔(cp949)에서 못 찍어
    # `--help` 가 UnicodeEncodeError 로 죽는다
    parser = argparse.ArgumentParser(
        description="Food 카탈로그 적재 (FT-19). 영양DB 품목대표는 menu_catalog 로, 레시피는 "
        "recipe_catalog 로 싣는다. 받기를 다 끝낸 뒤 한 트랜잭션으로 쓴다.",
        epilog=(
            "예) uv run python -m scripts.load_food_catalog --all --dry-run\n"
            "    (--nutrition 이나 --recipes 만 따로 돌릴 수도 있다)"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--nutrition", action="store_true", help="영양DB 품목대표 → menu_catalog")
    parser.add_argument("--recipes", action="store_true", help="레시피 → recipe_catalog")
    parser.add_argument("--all", action="store_true", help="둘 다(영양DB 먼저)")
    parser.add_argument("--dry-run", action="store_true", help="받고 세기만 하고 쓰지 않는다")
    return parser


def summary(loaded: Loaded, *, dry_run: bool) -> str:
    # `—` 를 쓰지 않는다 — Windows 콘솔(cp949)에서 못 찍는다
    suffix = " (dry-run, 쓰지 않음)" if dry_run else ""
    # 영양DB 를 받았을 때만 받은 행 수를 같이 보여준다 — 받은 행과 메뉴 수가 다르다
    received = f"(영양DB {loaded.nutrition_received}행 받음)" if loaded.nutrition_received else ""
    grouped = f" · 식품군 있음 {loaded.grouped_menus}행" if loaded.nutrition_received else ""
    # 소스별 건수 · 건너뛴 소스. 키 · 주소 · 레시피 이름은 싣지 않는다
    by_source = "".join(
        f" · {item.source} {item.rows}행(후보 풀 {item.resolved} · 내린 것 {item.retired}"
        f" · 주재료 없음 {item.without_main})"
        for item in loaded.sources
    )
    skipped = f" · 건너뜀 {' · '.join(loaded.skipped)}" if loaded.skipped else ""
    return (
        f"menu_catalog {loaded.nutrition_rows}행{received}{grouped} · "
        f"recipe_catalog {loaded.recipe_rows}행"
        f"(후보 풀 {loaded.resolved_recipes}개 · 내린 것 {loaded.retired_recipes}개)"
        f"{by_source}{skipped}{suffix}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
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
    print(summary(loaded, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
