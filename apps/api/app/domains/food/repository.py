"""카탈로그 저장소. 값은 칸 이름 → 값 dict 로 받는다.
agents 의 행 모양은 `agent_reader.py` 가 옮긴다.

- 배치(`refresh_nutrition_menus`)는 영양DB 행만 고친다. 사람이 넣은 행(`manual` ·
  `center_standard`)과 사람이 태깅한 `stage_min` 은 그대로다(own_table §1).
- 요청 중 저장(`insert_menu_if_absent`)은 없을 때만 넣는다 — 같은 메뉴를 두 run 이 동시에 채워도
  한 행만 남는다(FT-13).
- 레시피(`upsert_recipes`)는 (source, source_id) 로 새 값을 덮는다. 정리 표가 바뀌면 `resolved` 도
  따라 바뀌어야 해서다. `stage_min` 만 새 값이 비면 있는 값을 남긴다. 소스에서 사라진 레시피는
  `retire_missing_recipes` 가 후보 풀에서 내린다.
"""

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.food.models import MenuCatalog, RecipeCatalog
from app.rules.menu_evidence import menu_evidence_id

# 한 문장에 싣는 행 수. 칸이 14개라 PostgreSQL 인자 상한(32,767)에 걸리지 않는 크기다
CHUNK = 1000

# 배치가 영양DB 행을 다시 받을 때 고치는 칸. source · stage_min 은 고치지 않는다
_REFRESHED = (
    "display_name",
    "food_code",
    "serving_g",
    "nutrients",
    "ingredients",
    "allergen_codes",
    "food_groups",
    "resolved",
    "source_version",
    "synced_at",
)
_RECIPE_COLUMNS = (*_REFRESHED, "menu_key", "main_ingredients")


async def get_menus(session: AsyncSession, *, keys: Sequence[str]) -> list[MenuCatalog]:
    if not keys:
        return []
    stmt = select(MenuCatalog).where(MenuCatalog.menu_key.in_(keys))
    return list((await session.scalars(stmt)).all())


async def list_nutrition_menus(session: AsyncSession, *, keys: Sequence[str]) -> list[MenuCatalog]:
    """레시피 배치가 영양값을 붙일 영양DB 행. 영양값은 영양DB 에서만 채운다(own_table §1)."""
    if not keys:
        return []
    stmt = (
        select(MenuCatalog)
        .where(MenuCatalog.menu_key.in_(keys), MenuCatalog.source == "mfds_nutri")
        .order_by(MenuCatalog.menu_key)
    )
    return list((await session.scalars(stmt)).all())


async def insert_menu_if_absent(session: AsyncSession, *, values: Mapping[str, Any]) -> None:
    stmt = insert(MenuCatalog).values(**values).on_conflict_do_nothing(index_elements=["menu_key"])
    await session.execute(stmt)


async def refresh_nutrition_menus(
    session: AsyncSession, *, rows: Sequence[Mapping[str, Any]]
) -> None:
    if any(row["source"] != "mfds_nutri" for row in rows):
        raise ValueError("배치는 영양DB 행(mfds_nutri)만 싣는다")
    for start in range(0, len(rows), CHUNK):
        stmt = insert(MenuCatalog).values(list(rows[start : start + CHUNK]))
        stmt = stmt.on_conflict_do_update(
            index_elements=["menu_key"],
            set_={column: stmt.excluded[column] for column in _REFRESHED},
            # 있는 행이 사람이 넣은 행이면 고치지 않는다
            where=MenuCatalog.source == "mfds_nutri",
        )
        await session.execute(stmt)


async def upsert_recipes(session: AsyncSession, *, rows: Sequence[Mapping[str, Any]]) -> None:
    for start in range(0, len(rows), CHUNK):
        stmt = insert(RecipeCatalog).values(list(rows[start : start + CHUNK]))
        stmt = stmt.on_conflict_do_update(
            index_elements=["source", "source_id"],
            set_={column: stmt.excluded[column] for column in _RECIPE_COLUMNS}
            | {"stage_min": func.coalesce(stmt.excluded.stage_min, RecipeCatalog.stage_min)},
        )
        await session.execute(stmt)


async def retire_missing_recipes(
    session: AsyncSession, *, source: str, keep_ids: Sequence[str]
) -> int:
    """이번 받기에 없는 레시피를 후보 풀에서 내린다(`resolved=false`). 지우지는 않는다.

    소스가 내린 레시피는 upsert 가 다시 건드리지 않아서, 그대로 두면 옛 재료 판정이 `resolved=true`
    로 남아 후보 풀에 계속 나간다. 정리 표를 되돌려도 반영되지 않는다. 내린 수를 돌려준다.
    """
    if not keep_ids:
        # 빈 받기로 부르면 후보 풀 전체가 내려간다. 빈 받기는 호출부가 먼저 막는다
        raise ValueError("남길 레시피 번호가 비어 있다")
    stmt = (
        update(RecipeCatalog)
        .where(
            RecipeCatalog.source == source,
            RecipeCatalog.source_id.notin_(keep_ids),
            RecipeCatalog.resolved.is_(True),
        )
        .values(resolved=False)
    )
    return (await session.execute(stmt)).rowcount


async def resolved_recipe_names(session: AsyncSession) -> dict[uuid.UUID, str]:
    """후보 풀(resolved)의 레시피를 메뉴 근거 id → 메뉴 이름으로 (#442).

    Food 일반 추천의 메뉴 근거가 실제 후보 풀의 레시피인지 저장 때 확인하고, 05 가 근거 카드의
    이름을 찾는다. 근거 id 는 (source, source_id) 로 만드는 uuid5 라 표에 칸이 없어서, 해석된
    행을 읽어 같은 함수(`rules/menu_evidence`)로 다시 만든다 — 지금은 537행이라 한 번에 읽는다.
    """
    stmt = select(RecipeCatalog.source, RecipeCatalog.source_id, RecipeCatalog.display_name).where(
        RecipeCatalog.resolved.is_(True)
    )
    rows = (await session.execute(stmt)).all()
    return {menu_evidence_id(source, source_id): name for source, source_id, name in rows}


async def list_resolved_recipes(session: AsyncSession) -> list[RecipeCatalog]:
    stmt = (
        select(RecipeCatalog)
        .where(RecipeCatalog.resolved.is_(True))
        .order_by(RecipeCatalog.source, RecipeCatalog.source_id)
    )
    return list((await session.scalars(stmt)).all())
